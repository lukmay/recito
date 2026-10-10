r"""
Fish Audio S2 Pro TTS server. Protocol: ../server_base.py.

Uses its own generation loop instead of fish_speech.generate_long, because the
original
  * returns audio only after each text block (no token streaming),
  * reserves a 32k context (1 GB mask + 4.8 GB KV cache) and keeps the full
    155k-entry embedding table on the GPU.
Here: context 4096, text embedding table on the CPU (the GPU holds only the
4096 semantic rows plus <|im_end|>, which double as the output head), one
decode step (slow AR + 10 fast AR codebooks + sampling) as a CUDA graph, and
the codec decodes incrementally with left context (the codec is causal).

Environment (optional):
  FISH_CKPT        checkpoint folder (default <data_dir>/fish/checkpoints/s2-pro)
  FISH_MAX_LEN     context length in tokens (default 4096)
  FISH_NO_GRAPHS=1 disable CUDA graphs (debugging)
"""
import hashlib
import json
import math
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from server_base import Backend, data_dir, log_fn, run, voices_dir  # noqa: E402

KEY = "fish"
log = log_fn(KEY)

FISH_HOME = data_dir() / KEY
SRC = FISH_HOME / "src"
sys.path.insert(0, str(SRC))

import numpy as np  # noqa: E402
import torch  # noqa: E402
import torch.nn.functional as F  # noqa: E402

CKPT = Path(os.environ.get("FISH_CKPT") or FISH_HOME / "checkpoints" / "s2-pro")
MAX_LEN = int(os.environ.get("FISH_MAX_LEN") or 4096)
USE_GRAPHS = os.environ.get("FISH_NO_GRAPHS") != "1"
LOCAL_VOICES = FISH_HOME / "voices"      # model-specific references (fallback)
REF_CACHE = FISH_HOME / "refcache"       # VQ codes of the references

TEMPERATURE = 0.8
TOP_P = 0.8
TOP_K = 30
RAS_WIN = 10

# Tags from the model card and README; free text in [...] works as well.
TAGS = ["[pause]", "[short pause]", "[emphasis]", "[laughing]", "[laughing tone]",
        "[chuckle]", "[chuckling]", "[inhale]", "[exhale]", "[sigh]", "[tsk]",
        "[clearing throat]", "[panting]", "[whisper]", "[low voice]", "[low volume]",
        "[volume up]", "[volume down]", "[loud]", "[shouting]", "[screaming]",
        "[excited]", "[excited tone]", "[delight]", "[angry]", "[sad]",
        "[surprised]", "[shocked]", "[moaning]", "[singing]", "[echo]",
        "[interrupting]", "[audience laughter]", "[with strong accent]",
        "[pitch up]", "[pitch down]", "[professional broadcast tone]",
        "[whisper in small voice]"]


def _vram():
    return (f"alloc {torch.cuda.memory_allocated() / 2**30:.2f} GB, "
            f"reserved {torch.cuda.memory_reserved() / 2**30:.2f} GB")


def _remap_key(k):
    if k.startswith("text_model.model."):
        return k[len("text_model.model."):]
    if k.startswith("audio_decoder."):
        s = k[len("audio_decoder."):]
        return s if s.startswith("codebook_embeddings.") else "fast_" + s
    return k


def _sample(logits, temperature, top_p, top_k):
    """logits (V,) -> index (1,), without a host sync (graph-safe).
    Like fish_speech.logits_to_probs: top-k/top-p on the raw logits, then temperature."""
    sl, si = torch.sort(logits, descending=True)
    cp = torch.cumsum(torch.softmax(sl, dim=-1), dim=-1)
    ar = torch.arange(sl.shape[-1], device=sl.device)
    rm = ((cp > top_p) | (ar >= top_k)) & (ar > 0)
    rm = rm.scatter(0, si, rm)
    logits = torch.where(rm, float("-inf"), logits) / torch.clip(temperature, min=1e-5)
    probs = torch.softmax(logits.float(), dim=-1)
    q = -torch.log(torch.rand_like(probs))
    return torch.argmax(probs / q, dim=-1, keepdim=True)


def load_codec(path, device, dtype):
    """Like fish_speech.load_codec_model, but without the three 32768x32768 masks
    (1 GB each; WindowLimitedTransformer passes its own mask) and with shortened
    RoPE tables. Both are intercepted while building, since creating them on
    the CPU takes long."""
    from hydra.utils import instantiate
    from omegaconf import OmegaConf
    import fish_speech.models.dac.modded_dac as md
    cfg = OmegaConf.load(str(SRC / "fish_speech" / "configs" / "modded_dac_vq.yaml"))
    orig_pf, orig_ones = md.precompute_freqs_cis, torch.ones

    def small_ones(*size, **kw):
        if size in ((32768, 32768),):
            return orig_ones(1, 1, **kw)
        return orig_ones(*size, **kw)

    md.precompute_freqs_cis = lambda n, *a, **k: orig_pf(min(n, 32768), *a, **k)
    torch.ones = small_ones
    try:
        codec = instantiate(cfg)
    finally:
        md.precompute_freqs_cis, torch.ones = orig_pf, orig_ones
    sd = torch.load(str(path), map_location="cpu", mmap=True, weights_only=False)
    if "state_dict" in sd:
        sd = sd["state_dict"]
    if any("generator" in k for k in sd):
        sd = {k.replace("generator.", ""): v for k, v in sd.items() if "generator." in k}
    codec.load_state_dict(sd, strict=False)
    del sd
    return codec.eval().to(device=device, dtype=dtype)


class Engine:
    def __init__(self, ckpt: Path, device="cuda", max_len=MAX_LEN):
        from fish_speech.models.text2semantic.llama import (
            BaseModelArgs, DualARTransformer, precompute_freqs_cis)
        from fish_speech.tokenizer import FishTokenizer, IM_END_TOKEN
        from safetensors import safe_open

        self.dev = torch.device(device)
        dt = torch.bfloat16
        t0 = time.perf_counter()
        cfg = BaseModelArgs.from_pretrained(str(ckpt))
        cfg.max_seq_len = max_len
        self.tok = FishTokenizer.from_pretrained(str(ckpt))
        cfg.semantic_begin_id = self.tok.semantic_begin_id
        cfg.semantic_end_id = self.tok.semantic_end_id
        self.cfg = cfg
        self.sb, self.se = cfg.semantic_begin_id, cfg.semantic_end_id
        self.nc, self.cs = cfg.num_codebooks, cfg.codebook_size
        self.im_end = self.tok.get_token_id(IM_END_TOKEN)
        assert self.se - self.sb + 1 == self.cs, "semantic tokens are not contiguous"

        with torch.device("meta"):
            model = DualARTransformer(cfg)
        idx = json.loads((ckpt / "model.safetensors.index.json").read_text())
        sd, emb = {}, None
        for shard in sorted(set(idx["weight_map"].values())):
            with safe_open(str(ckpt / shard), framework="pt", device="cpu") as f:
                for k in f.keys():
                    nk = _remap_key(k)
                    t = f.get_tensor(k)
                    if nk == "embeddings.weight":
                        emb = t.to(dt).contiguous()
                    else:
                        sd[nk] = t.to(device=self.dev, dtype=dt)
        res = model.load_state_dict(sd, strict=False, assign=True)
        del sd
        missing = [k for k in res.missing_keys if k != "embeddings.weight"]
        if missing or res.unexpected_keys:
            log(f"WARNING weights: missing={missing[:8]} unexpected={res.unexpected_keys[:8]}")
        # Text embeddings stay on the CPU; only the prefill needs them.
        self.emb_cpu = emb
        model.embeddings = torch.nn.Identity()
        # Output head = tied embeddings, restricted to the allowed rows: 4096 semantic + im_end.
        self.E_out = torch.cat([emb[self.sb:self.se + 1], emb[self.im_end:self.im_end + 1]]
                               ).to(self.dev)
        model.register_buffer("freqs_cis", precompute_freqs_cis(
            max_len, cfg.head_dim, cfg.rope_base).to(self.dev), persistent=False)
        model.register_buffer("causal_mask", torch.tril(torch.ones(
            max_len, max_len, dtype=torch.bool, device=self.dev)), persistent=False)
        model.register_buffer("fast_freqs_cis", precompute_freqs_cis(
            cfg.num_codebooks, cfg.fast_head_dim, cfg.rope_base).to(self.dev),
            persistent=False)
        for n, p in list(model.named_parameters()) + list(model.named_buffers()):
            if p.is_meta:
                raise RuntimeError(f"parameter without weights: {n}")
        model.eval()
        with torch.device(self.dev):
            model.setup_caches(max_batch_size=1, max_seq_len=max_len, dtype=dt)
        self.model = model
        self.max_len = max_len
        self.norm_fast = getattr(cfg, "norm_fastlayer_input", False)
        log(f"LLM loaded after {time.perf_counter() - t0:.1f} s, VRAM {_vram()}")

        # static tensors (also used by the CUDA graphs)
        d = self.dev
        self.temp = torch.tensor(TEMPERATURE, device=d, dtype=torch.float)
        self.top_p = torch.tensor(TOP_P, device=d, dtype=torch.float)
        self.temp_hi = torch.tensor(1.0, device=d, dtype=torch.float)
        self.top_p_hi = torch.tensor(0.9, device=d, dtype=torch.float)
        self.fpos = [torch.tensor([i], device=d, dtype=torch.long) for i in range(self.nc)]
        self.prev = torch.full((RAS_WIN,), -1, device=d, dtype=torch.long)
        self.s_tok = torch.zeros(self.nc + 1, device=d, dtype=torch.long)
        self.s_pos = torch.zeros(1, device=d, dtype=torch.long)
        self.s_out = torch.zeros(self.nc + 1, device=d, dtype=torch.long)
        self.cb_off = (torch.arange(self.nc, device=d) * self.cs).long()
        self.graphs = {}
        self.buckets = [b for b in (1024, 4096) if b <= max_len]
        if self.buckets[-1] != max_len:
            self.buckets.append(max_len)

        t1 = time.perf_counter()
        self.codec = load_codec(ckpt / "codec.pth", self.dev, dt)
        self.sr = self.codec.sample_rate
        self.hop = int(self.codec.frame_length)
        log(f"codec loaded after {time.perf_counter() - t1:.1f} s (sr {self.sr}, "
            f"{self.hop} samples/frame), VRAM {_vram()}")

    # ---------------------------------------------------------------- core
    def _head(self, x):
        """x (1,1,dim) last slow state -> (11,) [token id, c0..c9]."""
        m = self.model
        slow = m.norm(x)
        logits = F.linear(slow, self.E_out)[0, -1]          # (4097,)
        hidden = m.fast_project_in(slow if self.norm_fast else x)
        main = _sample(logits, self.temp, self.top_p, TOP_K)
        hi = _sample(logits, self.temp_hi, self.top_p_hi, TOP_K)
        # repetition aware sampling as in the original
        use_hi = (self.prev == main).any() & (main < self.cs)
        main = torch.where(use_hi, hi, main)
        self.prev.copy_(torch.cat([self.prev[1:], main]))
        a = main.clamp(max=self.cs - 1)
        m.forward_generate_fast(hidden, self.fpos[0])
        h = m.fast_embeddings(a)
        codes = [a]
        for i in range(1, self.nc):
            lg = m.forward_generate_fast(h, self.fpos[i])
            a = _sample(lg[0, -1], self.temp, self.top_p, TOP_K)
            h = m.fast_embeddings(a)
            codes.append(a)
        tokid = torch.where(main >= self.cs, torch.full_like(main, self.im_end), main + self.sb)
        return torch.cat([tokid] + codes)

    def _layers(self, x, pos, kv_len):
        m = self.model
        mask = m.causal_mask[None, None, pos, :kv_len]
        freqs = m.freqs_cis[pos]
        for layer in m.layers:
            x = layer(x, freqs, mask, input_pos=pos)
        return x

    def _step(self, kv_len):
        m = self.model
        t = self.s_tok
        idx = (t[0] - self.sb).clamp(0, self.cs - 1)
        x = F.embedding(idx.view(1), self.E_out)[0] + m.codebook_embeddings(t[1:] + self.cb_off).sum(0)
        x = (x / math.sqrt(self.nc + 1)).view(1, 1, -1)
        x = self._layers(x, self.s_pos, kv_len)
        self.s_out.copy_(self._head(x))

    def _bucket(self, n):
        for b in self.buckets:
            if n <= b:
                return b
        return self.max_len

    def capture_graphs(self):
        if not USE_GRAPHS:
            return
        t0 = time.perf_counter()
        self.pool = torch.cuda.graph_pool_handle()
        s = torch.cuda.Stream()
        s.wait_stream(torch.cuda.current_stream())
        with torch.inference_mode():
            self.s_tok.zero_()
            self.s_tok[0] = self.sb
            for b in reversed(self.buckets):   # largest first, so the pool fits
                self.s_pos.fill_(0)
                with torch.cuda.stream(s):
                    self._step(b)
                torch.cuda.current_stream().wait_stream(s)
                g = torch.cuda.CUDAGraph()
                with torch.cuda.graph(g, pool=self.pool):
                    self._step(b)
                self.graphs[b] = g
        torch.cuda.synchronize()
        log(f"{len(self.graphs)} CUDA graphs in {time.perf_counter() - t0:.1f} s, VRAM {_vram()}")

    # ------------------------------------------------------------ prompt
    def build_prompt(self, text, ref_codes=None, ref_text=""):
        from fish_speech.content_sequence import TextPart, VQPart
        from fish_speech.conversation import Conversation, Message
        conv = Conversation()
        if ref_codes is not None:
            if not re.search(r"<\|speaker:\d+\|>", ref_text):
                ref_text = f"<|speaker:0|>{ref_text}"
            intro = "convert the provided text to speech reference to the following:\n\nText:\n"
            parts = [TextPart(text=intro, cal_loss=False),
                     TextPart(text=ref_text, cal_loss=False),
                     TextPart(text="\n\nSpeech:\n", cal_loss=False),
                     VQPart(codes=ref_codes, cal_loss=False)]
        else:
            parts = [TextPart(text="convert the provided text to speech", cal_loss=False)]
        conv.append(Message(role="system", parts=parts, cal_loss=False,
                            add_im_start=True, add_im_end=True))
        conv.append(Message(role="user", parts=[TextPart(text=text, cal_loss=False)],
                            cal_loss=False, add_im_start=True, add_im_end=True))
        conv.append(Message(role="assistant", parts=[], cal_loss=False, modality="voice",
                            add_im_start=True, add_im_end=False))
        values, _, _ = conv.encode_for_inference(self.tok, num_codebooks=self.nc)
        return values  # (11, T) long

    def _prefill(self, values):
        m = self.model
        T = values.shape[1]
        x = self.emb_cpu[values[0]].to(self.dev, non_blocking=True)        # (T, dim)
        v = values.to(self.dev)
        vq = (v[0] >= self.sb) & (v[0] <= self.se)
        if bool(vq.any()):
            cb = m.codebook_embeddings(v[1:] + self.cb_off[:, None]).sum(0)  # (T, dim)
            x = torch.where(vq[:, None], (x + cb) / math.sqrt(self.nc + 1), x)
        pos = torch.arange(T, device=self.dev)
        x = self._layers(x[None], pos, T)
        return self._head(x[:, -1:])

    # ------------------------------------------------------------ generation
    @torch.inference_mode()
    def generate(self, values, max_new):
        """Yields code frames (10,) on the GPU."""
        T = values.shape[1]
        if T + 8 >= self.max_len:
            raise ValueError(f"prompt too long ({T} tokens, context {self.max_len})")
        max_new = min(max_new, self.max_len - T - 1)
        self.prev.fill_(-1)
        out = self._prefill(values)
        self.s_tok.copy_(out)
        self.s_pos.fill_(T)
        for i in range(max_new):
            if int(self.s_tok[0]) == self.im_end:
                return
            yield self.s_tok[1:].clone()
            if i == max_new - 1:
                break
            kv = T + i + 1
            b = self._bucket(kv)
            g = self.graphs.get(b)
            if g is not None:
                g.replay()
            else:
                self._step(b)
            self.s_tok.copy_(self.s_out)
            self.s_pos += 1
        log(f"WARNING: max_new_tokens ({max_new}) reached, sentence may be cut off")

    @torch.inference_mode()
    def decode(self, codes):
        """codes (10, n) -> float32 numpy audio."""
        audio = self.codec.from_indices(codes[None].clone())
        return audio[0, 0].float().cpu().numpy()

    @torch.inference_mode()
    def encode_ref(self, wav_path):
        import soundfile as sf
        import torchaudio
        a, sr = sf.read(str(wav_path), dtype="float32", always_2d=True)
        w = torch.from_numpy(a.mean(axis=1))
        w = torchaudio.functional.resample(w, sr, self.sr)
        w = w.to(self.dev, dtype=next(self.codec.parameters()).dtype)
        lens = torch.tensor([w.shape[0]], device=self.dev)
        idx, ln = self.codec.encode(w[None, None], lens)
        return idx[0, :, :ln[0]].cpu()


# ------------------------------------------------------------------ text
_SENT = re.compile(r"(?<=[.!?…;:])\s+")


def split_text(text, limit=220):
    """Long texts as blocks at sentence boundaries (one generation per block)."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for s in _SENT.split(text):
        if cur and len(cur) + 1 + len(s) > limit:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    res = []
    for c in out:   # split overlong sentences at commas
        while len(c) > limit * 1.6:
            k = c.rfind(", ", 0, limit)
            if k < limit // 3:
                k = c.rfind(" ", 0, limit)
            if k <= 0:
                break
            res.append(c[:k + 1].strip())
            c = c[k + 1:].strip()
        res.append(c)
    return res


def _spoken_len(text):
    return len(re.sub(r"\[[^\]]*\]", "", text))


class FishBackend(Backend):
    # Languages of the model card (S1/S2 family) that the app can use
    languages = ("de", "en", "fr", "es", "it", "nl", "pt", "pl", "ru",
                 "zh", "ja", "ko", "ar")
    name = "fish"
    tags = TAGS
    instruct = True

    def load(self):
        t0 = time.perf_counter()
        torch.backends.cuda.matmul.allow_tf32 = True
        self.eng = Engine(CKPT)
        self.sample_rate = self.eng.sr
        self.eng.capture_graphs()
        self._refs = {}
        REF_CACHE.mkdir(parents=True, exist_ok=True)
        for v in self.voices():
            if v["id"].startswith("ref:"):
                try:
                    self._ref(v["id"][4:])
                except Exception as exc:
                    log(f"reference {v['id']} unusable: {exc}")
        # Warm-up: cuDNN autotuning in the codec, allocator.
        t1 = time.perf_counter()
        for _ in self.synth("Hallo, das ist ein Test.", "", "de"):
            pass
        log(f"warm-up sentence {time.perf_counter() - t1:.1f} s")
        log(f"loaded in {time.perf_counter() - t0:.1f} s, VRAM {_vram()}, "
            f"max {torch.cuda.max_memory_allocated() / 2**30:.2f} GB")

    # ------------------------------------------------------------ voices
    @staticmethod
    def _ref_files():
        found = {}
        for d in (LOCAL_VOICES, voices_dir()):   # shared voices override local ones
            if d.exists():
                for w in sorted(d.glob("*.wav")):
                    if w.with_suffix(".txt").exists():
                        found[w.stem] = w
        return found

    def voices(self):
        out = []
        for vid, w in self._ref_files().items():
            lang = vid[:2] if vid[:2] in FishBackend.languages and vid[2:3] in ("_", "-") else "multi"
            out.append({"id": f"ref:{vid}", "lang": lang,
                        "desc": f"Geklont aus {w.parent.name}\\{w.name}"})
        out.append({"id": "random", "lang": "multi",
                    "desc": "ohne Referenz: Modell wählt pro Satz eine zufällige Stimme"})
        return out

    def default_voices(self):
        refs = self._ref_files()
        res = {}
        for lang in self.languages:
            prefs = [f"{lang}_female_1", f"{lang}_male_1", f"fish_{lang}"]
            res[lang] = next((f"ref:{p}" for p in prefs if p in refs), "random")
        return res

    def _ref(self, vid):
        w = self._ref_files().get(vid)
        if w is None:
            return None
        txt = w.with_suffix(".txt").read_text(encoding="utf-8").strip()
        st = w.stat()
        key = hashlib.sha1(f"{w}|{st.st_size}|{st.st_mtime_ns}|{txt}".encode()).hexdigest()[:16]
        hit = self._refs.get(vid)
        if hit and hit[0] == key:
            return hit[1], hit[2]
        npy = REF_CACHE / f"{vid}-{key}.npy"
        if npy.exists():
            codes = torch.from_numpy(np.load(npy))
        else:
            codes = self.eng.encode_ref(w)
            np.save(npy, codes.numpy())
            log(f"reference {vid} encoded: {codes.shape[1]} frames")
        self._refs[vid] = (key, codes, txt)
        return codes, txt

    # ------------------------------------------------------------ synthesis
    def synth(self, text, voice, lang, speed=1.0, instruct=""):
        eng = self.eng
        if voice in ("", None) or (voice != "random" and not voice.startswith("ref:")):
            voice = self.default_voices().get(lang, "random")
        ref = self._ref(voice[4:]) if voice.startswith("ref:") else None
        if voice.startswith("ref:") and ref is None:
            voice = self.default_voices().get(lang, "random")
            ref = self._ref(voice[4:]) if voice.startswith("ref:") else None
        codes_ref, ref_text = ref if ref else (None, "")
        if instruct:
            text = f"[{instruct.strip().strip('[]')}] {text}"
        # S2 has no tempo control: speed is ignored, the daemon time-stretches.
        CTX = 48           # left context in frames for incremental decoding
        for chunk in split_text(text):
            values = eng.build_prompt(chunk, codes_ref, ref_text)
            max_new = int(_spoken_len(chunk) * 2.6 + 40 + 25 * chunk.count("["))
            frames = []
            done = 0          # frames already emitted
            step = 8          # small first block for early audio, then larger
            for fr in eng.generate(values, max_new):
                frames.append(fr)
                if len(frames) - done >= step:
                    yield self._emit(frames, done, len(frames), CTX)
                    done = len(frames)
                    step = min(step * 2, 48)
            if len(frames) > done:
                yield self._emit(frames, done, len(frames), CTX)

    def _emit(self, frames, a, b, ctx):
        s = max(0, a - ctx)
        codes = torch.stack(frames[s:b], dim=1)
        audio = self.eng.decode(codes)
        hop = len(audio) // (b - s)
        return audio[(a - s) * hop:]


if __name__ == "__main__":
    run(FishBackend(), KEY)
