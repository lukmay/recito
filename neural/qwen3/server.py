r"""
Qwen3-TTS server (CustomVoice: preset speakers plus style instructions).
Protocol: ../server_base.py. Runs in the venv <data_dir>/qwen3/venv.

Configuration through environment variables, or <data_dir>/qwen3/config.json
with the same keys in lower case without prefix, e.g. {"model": "0.6B"}:
  QWEN3_MODEL   1.7B (default, understands instructions) | 0.6B (faster, no instructions)
  QWEN3_CLONE   1 (default) | 0. With 1, Qwen3-TTS-0.6B-Base is loaded too and
                the reference voices in <data_dir>/voices are offered as
                "ref:<id>" (+~1.9 GB VRAM, no instructions). The German default
                voice is then ref:de_male_1, because none of the preset
                speakers is a native German speaker.
  QWEN3_FIRST   frames until the first audio chunk (12.5 frames = 1 s), default 4
  QWEN3_EMIT    frames per further chunk, default 10
  QWEN3_GRAPH   1 (default) | 0: no CUDA graphs (then ~1.8x slower than real time)

Streaming uses its own decode loop: the talker runs frame by frame and the
codec decoder works on a sliding window of 72 frames. The qwen_tts package
comes from the fork dffdeeq/Qwen3-TTS-streaming; this loop adds a repetition
penalty, a length limit, a shorter first chunk and CUDA graphs
(GraphCodePredictor, FrameGraph).
"""
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cuda_graph import StaticKV, capture  # noqa: E402
from server_base import Backend, data_dir, log_fn, run, voices_dir  # noqa: E402

KEY = "qwen3"
log = log_fn(KEY)


def _cfg():
    c = {}
    f = data_dir() / KEY / "config.json"
    if f.exists():
        try:
            c = {k.lower(): v for k, v in json.loads(f.read_text(encoding="utf-8")).items()}
        except Exception as exc:
            log(f"config.json unreadable: {exc}")
    for k in ("model", "clone", "first", "emit", "graph"):
        v = os.environ.get(f"QWEN3_{k.upper()}")
        if v is not None:
            c[k] = v
    return c


CFG = _cfg()
SIZE = "0.6B" if str(CFG.get("model", "1.7B")).upper().startswith("0.6") else "1.7B"
CLONE = str(CFG.get("clone", "1")).lower() in ("1", "true", "yes", "on")
FIRST = int(CFG.get("first", 4))
EMIT = int(CFG.get("emit", 10))
GRAPH = str(CFG.get("graph", "1")).lower() not in ("0", "false", "no", "off")
WINDOW = 72          # decoder context in frames (~5.8 s)
SR = 24000

# Preset speakers of the CustomVoice model (model card): native language, description.
SPEAKERS = {
    "Ryan":     ("en", "männlich, dynamisch, kräftiger Rhythmus (EN-Muttersprachler)"),
    "Aiden":    ("en", "männlich, sonnig, amerikanisch, klare Mitten (EN-Muttersprachler)"),
    "Serena":   ("zh", "weiblich, warm und sanft, jung (ZH-Muttersprachlerin)"),
    "Vivian":   ("zh", "weiblich, hell, leicht kantig, jung (ZH-Muttersprachlerin)"),
    "Uncle_Fu": ("zh", "männlich, gesetzt, tief und weich (ZH-Muttersprachler)"),
    "Dylan":    ("zh", "männlich, jung, Peking-Klang, klar (ZH-Muttersprachler)"),
    "Eric":     ("zh", "männlich, lebhaft, leicht rauchig, Chengdu (ZH-Muttersprachler)"),
    "Ono_Anna": ("ja", "weiblich, verspielt, leicht (JA-Muttersprachlerin)"),
    "Sohee":    ("ko", "weiblich, warm, emotional (KO-Muttersprachlerin)"),
}
DEFAULTS = {"de": "Dylan", "en": "Aiden", "fr": "Serena", "es": "Ryan", "it": "Vivian",
            "pt": "Eric", "ru": "Uncle_Fu", "zh": "Vivian", "ja": "Ono_Anna",
            "ko": "Sohee"}                          # preset speakers only
CLONE_DEFAULTS = {"de": "ref:de_male_1"}            # with QWEN3_CLONE=1, if present
# The ten languages of the model card: code -> name the model expects.
LANGS = {"de": "German", "en": "English", "fr": "French", "es": "Spanish", "it": "Italian",
         "pt": "Portuguese", "ru": "Russian", "zh": "Chinese", "ja": "Japanese",
         "ko": "Korean"}


def _local(repo):
    """Local snapshot path, so startup needs no network (with a repo id,
    transformers 4.57 queries the Hub API and fails offline)."""
    from huggingface_hub import snapshot_download
    try:
        return snapshot_download(repo, local_files_only=True)
    except Exception:
        return snapshot_download(repo)


class GraphCodePredictor:
    """The 15 code predictor steps per frame (codebooks 2..16) as one CUDA
    graph. Eagerly they cost about 100 ms per frame, almost all launch
    overhead (75 layer calls), which makes the model slower than real time.

    Fixed shapes: instead of a KV cache, each of the 15 steps runs the whole
    16-position sequence through the 5 layers (causal, so later positions do
    not interfere). Sampling uses Gumbel-max (graph-safe) with the model's
    temperature and top-k."""

    def __init__(self, talker, temperature=0.9, top_k=50):
        self.cp = talker.code_predictor
        self.n = talker.config.num_code_groups - 1          # 15
        h = talker.config.hidden_size
        dev = next(self.cp.parameters()).device
        dt = next(self.cp.parameters()).dtype
        self.temp, self.top_k = float(temperature), int(top_k)
        self.buf = torch.zeros(1, self.n + 1, h, device=dev, dtype=dt)
        self.tokens = torch.zeros(1, self.n, dtype=torch.long, device=dev)
        self.graph = capture(self._body)
        talker._use_fast_codebook_gen = True
        self.cp.generate_fast = self.generate_fast

    def _sample(self, logits):
        logits = logits.float() / self.temp
        if self.top_k > 0:
            kth = torch.topk(logits, self.top_k, dim=-1).values[..., -1:]
            logits = logits.masked_fill(logits < kth, float("-inf"))
        u = torch.rand_like(logits).clamp_(1e-10, 1.0)
        return torch.argmax(logits - torch.log(-torch.log(u)), dim=-1)

    def _body(self):
        cp = self.cp
        for i in range(self.n):
            x = cp.small_to_mtp_projection(self.buf)
            hs = cp.model(input_ids=None, inputs_embeds=x, use_cache=False,
                          output_hidden_states=False).last_hidden_state
            tok = self._sample(cp.lm_head[i](hs[:, i + 1]))
            self.tokens[:, i].copy_(tok)
            if i + 1 < self.n:
                self.buf[:, i + 2].copy_(cp.model.codec_embedding[i](tok))

    def generate_fast(self, inputs_embeds, num_codebooks, **_):
        self.buf[:, :2].copy_(inputs_embeds)
        self.graph.replay()
        return self.tokens.clone()


class FrameGraph:
    """A whole frame step as one CUDA graph: code predictor (15 codebooks) plus
    the talker step (28 layers, static KV cache). Only sampling of the first
    codebook and the EOS check stay outside. Eagerly a frame takes ~145 ms,
    as a graph a few ms."""

    def __init__(self, talker, cpg, max_len=3072):
        self.talker, self.cpg = talker, cpg
        cfg = talker.config
        p = next(talker.parameters())
        dev, dt = p.device, p.dtype
        self.max_len = max_len
        self.kv = StaticKV(cfg.num_hidden_layers, cfg.num_key_value_heads, max_len,
                           getattr(cfg, "head_dim", cfg.hidden_size // cfg.num_attention_heads),
                           dt, dev)
        h = cfg.hidden_size
        self.tok0 = torch.zeros(1, dtype=torch.long, device=dev)
        self.past_hidden = torch.zeros(1, 1, h, dtype=dt, device=dev)
        self.text_add = torch.zeros(1, 1, h, dtype=dt, device=dev)
        self.cache_pos = torch.zeros(1, dtype=torch.long, device=dev)
        self.pos3 = torch.zeros(3, 1, 1, dtype=torch.long, device=dev)
        self.mask = torch.full((1, 1, 1, max_len), float("-inf"), dtype=dt, device=dev)
        self.mask[..., 0] = 0
        self.graph = capture(self._body)

    def _body(self):
        t, cpg, cp = self.talker, self.cpg, self.cpg.cp
        cpg.buf[:, 0].copy_(self.past_hidden[:, 0])
        cpg.buf[:, 1].copy_(t.get_input_embeddings()(self.tok0))
        cpg._body()
        last = cp.model.codec_embedding[cpg.n - 1](cpg.tokens[:, -1])
        x = (cpg.buf[:, 1:].sum(1) + last).unsqueeze(1) + self.text_add
        out = t.model(inputs_embeds=x, attention_mask=self.mask, position_ids=self.pos3,
                      cache_position=self.cache_pos, past_key_values=self.kv, use_cache=True)
        self.hidden = out.last_hidden_state
        self.logits = t.codec_head(self.hidden)[:, -1]
        self.codes = torch.cat([self.tok0, cpg.tokens[0]])

    def start(self, pkv, past_hidden):
        """Take over the KV cache of the regular prefill -> remaining length."""
        n = self.kv.load(pkv)
        self.mask.fill_(float("-inf"))
        self.mask[..., :n] = 0
        self.past_hidden.copy_(past_hidden)
        self.n = n
        return self.max_len - n

    def step(self, token, text_add):
        p = self.n
        self.tok0.copy_(token.view(1))
        self.text_add.copy_(text_add)
        self.cache_pos.fill_(p)
        self.pos3.fill_(p)
        self.mask[..., p] = 0
        self.graph.replay()
        self.past_hidden.copy_(self.hidden)
        self.n = p + 1
        return self.codes.clone(), self.logits


class Qwen3Backend(Backend):
    languages = tuple(LANGS)
    name = "qwen3"
    sample_rate = SR
    tags = []
    instruct = SIZE == "1.7B"

    def __init__(self):
        self.tts = None
        self.clone_tts = None
        self.clone_prompts = {}

    def load(self):
        from qwen_tts import Qwen3TTSModel
        torch.backends.cuda.matmul.allow_tf32 = True
        t0 = time.perf_counter()
        repo = f"Qwen/Qwen3-TTS-12Hz-{SIZE}-CustomVoice"
        self.tts = Qwen3TTSModel.from_pretrained(
            _local(repo), device_map="cuda:0", dtype=torch.bfloat16, attn_implementation="sdpa")
        self.tts.model.eval()
        log(f"{repo} loaded in {time.perf_counter() - t0:.1f} s, "
            f"VRAM {torch.cuda.memory_allocated() / 2**30:.2f} GB")
        self._graph(self.tts)
        sup = {s.lower() for s in (self.tts.model.get_supported_speakers() or [])}
        self.speakers = {k: v for k, v in SPEAKERS.items() if k.lower() in sup} or SPEAKERS

        if CLONE:
            t1 = time.perf_counter()
            self.clone_tts = Qwen3TTSModel.from_pretrained(
                _local("Qwen/Qwen3-TTS-12Hz-0.6B-Base"), device_map="cuda:0",
                dtype=torch.bfloat16, attn_implementation="sdpa")
            self.clone_tts.model.eval()
            # Both checkpoints share the same codec tokenizer: keep one copy in VRAM.
            own = self.clone_tts.model.speech_tokenizer
            self.clone_tts.model.speech_tokenizer = self.tts.model.speech_tokenizer
            del own
            torch.cuda.empty_cache()
            self._graph(self.clone_tts)
            for wav in sorted(voices_dir().glob("*.wav")):
                txt = wav.with_suffix(".txt")
                if not txt.exists():
                    continue
                try:
                    items = self.clone_tts.create_voice_clone_prompt(
                        ref_audio=str(wav), ref_text=txt.read_text(encoding="utf-8").strip())
                    self.clone_prompts[f"ref:{wav.stem}"] = items[0]
                except Exception as exc:
                    log(f"reference {wav.name} unusable: {exc}")
            log(f"0.6B-Base + {len(self.clone_prompts)} reference voices in "
                f"{time.perf_counter() - t1:.1f} s, VRAM "
                f"{torch.cuda.memory_allocated() / 2**30:.2f} GB")

        # Warm up kernels and the first decode so the first real sentence is fast.
        for lang in ("de", "en"):
            for _ in self.synth("Hallo." if lang == "de" else "Hello.", "", lang):
                pass
        torch.cuda.synchronize()
        log(f"warm, VRAM {torch.cuda.memory_allocated() / 2**30:.2f} GB "
            f"(reserved {torch.cuda.memory_reserved() / 2**30:.2f} GB), "
            f"first={FIRST} emit={EMIT} frames")

    @staticmethod
    def _graph(tts):
        if not GRAPH:
            return
        g = tts.generate_defaults or {}
        try:
            cpg = GraphCodePredictor(tts.model.talker, g.get("subtalker_temperature", 0.9),
                                     g.get("subtalker_top_k", 50))
            log("code predictor as CUDA graph")
        except Exception as exc:
            log(f"CUDA graph failed, slow path: {exc!r}")
            return
        try:
            tts.frame_graph = FrameGraph(tts.model.talker, cpg)
            log("whole frame step as CUDA graph")
        except Exception as exc:
            import traceback
            traceback.print_exc()
            log(f"frame graph failed, only the code predictor runs as a graph: {exc!r}")

    def voices(self):
        out = [{"id": k, "lang": v[0], "desc": v[1]} for k, v in self.speakers.items()]
        for vid in self.clone_prompts:
            lang = vid[4:6] if vid[4:6] in LANGS and vid[6:7] in ("_", "-") else ""
            out.append({"id": vid, "lang": lang,
                        "desc": "Klon einer synthetischen Referenzstimme (0.6B-Base, kein instruct)"})
        return out

    def default_voices(self):
        d = dict(DEFAULTS)
        d.update({k: v for k, v in CLONE_DEFAULTS.items() if v in self.clone_prompts})
        return d

    def synth(self, text, voice, lang, speed=1.0, instruct=""):
        # No speed control in the model: speed is ignored and the daemon
        # time-stretches the output (models.json without native_speed).
        lang = lang if lang in LANGS else "en"
        ins = (instruct or "").strip()
        if not self.instruct:
            ins = ""
        if voice in self.clone_prompts:              # explicitly cloned voice
            yield from self._stream(self.clone_tts, text, lang, clone=self.clone_prompts[voice])
            return
        spk = next((k for k in self.speakers if k.lower() == (voice or "").lower()), None)
        if spk is None:                               # unknown or empty -> default
            dv = self.default_voices().get(lang) or DEFAULTS["en"]
            if dv in self.clone_prompts and not ins:
                yield from self._stream(self.clone_tts, text, lang, clone=self.clone_prompts[dv])
                return
            # Clones cannot follow instructions -> preset speaker of the language.
            spk = dv if dv in self.speakers else DEFAULTS.get(lang, DEFAULTS["en"])
        yield from self._stream(self.tts, text, lang, speaker=spk, instruct=ins)

    @torch.inference_mode()
    def _stream(self, tts, text, lang, speaker=None, instruct="", clone=None):
        m = tts.model
        talker = m.talker
        tcfg = m.config.talker_config
        input_ids = tts._tokenize_texts([tts._build_assistant_text(text)])
        instruct_ids = [tts._tokenize_texts([tts._build_instruct_text(instruct)])[0]
                        if instruct else None]
        kw = dict(input_ids=input_ids, instruct_ids=instruct_ids, languages=[LANGS[lang]],
                  non_streaming_mode=False, ref_ids=None, voice_clone_prompt=None, speakers=None)
        if clone is not None:
            kw["voice_clone_prompt"] = tts._prompt_items_to_voice_clone_prompt([clone])
            kw["ref_ids"] = [tts._tokenize_texts([tts._build_ref_text(clone.ref_text)])[0]]
        else:
            kw["speakers"] = [speaker]
        embeds, mask, trailing, pad_embed = m._build_talker_inputs(**kw)

        g = tts.generate_defaults or {}
        temp = float(g.get("temperature", 0.9))
        top_k = int(g.get("top_k", 50))
        rep = float(g.get("repetition_penalty", 1.05))
        sub = dict(subtalker_dosample=g.get("subtalker_dosample", True),
                   subtalker_top_k=g.get("subtalker_top_k", 50),
                   subtalker_top_p=g.get("subtalker_top_p", 1.0),
                   subtalker_temperature=g.get("subtalker_temperature", 0.9))
        eos = tcfg.codec_eos_token_id
        vocab = tcfg.vocab_size
        suppress = torch.zeros(vocab, dtype=torch.bool, device=embeds.device)
        suppress[vocab - 1024:] = True
        suppress[eos] = False
        # Length limit against endless babbling: ~12.5 frames/s, about 2x reading time.
        max_frames = int(60 + 2.0 * len(text))

        out = talker.forward(inputs_embeds=embeds, attention_mask=mask, use_cache=True,
                             output_hidden_states=True, return_dict=True,
                             trailing_text_hidden=trailing, tts_pad_embed=pad_embed,
                             generation_step=None, past_hidden=None, past_key_values=None,
                             **sub)
        pkv, past_hidden, step = out.past_key_values, out.past_hidden, out.generation_step
        seen = torch.zeros(vocab, dtype=torch.bool, device=embeds.device)

        def sample(logits):
            logits = logits.float().masked_fill(suppress, float("-inf"))
            if rep != 1.0:   # like HF RepetitionPenalty, without a GPU sync
                logits = torch.where(seen, torch.where(logits > 0, logits / rep, logits * rep),
                                     logits)
            logits = logits / temp
            if top_k > 0:
                kth = torch.topk(logits, top_k, dim=-1).values[..., -1:]
                logits = logits.masked_fill(logits < kth, float("-inf"))
            probs = torch.softmax(logits, dim=-1)
            return torch.multinomial(probs, 1).squeeze(-1)

        token = sample(out.logits[:, -1, :])
        tok = m.speech_tokenizer
        spf = tok.get_decode_upsample_rate()       # samples per frame (1920)
        ref_ctx = None
        if clone is not None and clone.ref_code is not None and clone.icl_mode:
            ref_ctx = clone.ref_code.to(embeds.device)

        fg = getattr(tts, "frame_graph", None)
        if fg is not None:
            max_frames = min(max_frames, fg.start(pkv, past_hidden))
        codes = []
        emitted = 0
        next_emit = FIRST
        for _ in range(max_frames):
            if int(token) == eos:
                break
            if fg is not None:
                text_add = trailing[:, step:step + 1] if step < trailing.shape[1] else pad_embed
                cids, logits = fg.step(token, text_add)
                step += 1
                codes.append(cids)
            else:
                so = talker.forward(input_ids=token.unsqueeze(1), use_cache=True, return_dict=True,
                                    output_hidden_states=False, past_key_values=pkv,
                                    past_hidden=past_hidden, generation_step=step,
                                    trailing_text_hidden=trailing, tts_pad_embed=pad_embed, **sub)
                pkv, past_hidden, step = so.past_key_values, so.past_hidden, so.generation_step
                codes.append(so.hidden_states[1][0])
                logits = so.logits[:, -1, :]
            seen[token] = True
            token = sample(logits)
            if len(codes) >= next_emit:
                yield self._decode(tok, codes, emitted, spf, ref_ctx)
                emitted = len(codes)
                next_emit = emitted + EMIT
        if len(codes) > emitted:
            yield self._decode(tok, codes, emitted, spf, ref_ctx)

    @staticmethod
    def _decode(tok, codes, emitted, spf, ref_ctx):
        """Decode the new frames codes[emitted:] with up to WINDOW frames of left context."""
        start = max(0, emitted - (WINDOW - (len(codes) - emitted)))
        start = min(start, emitted)
        win = torch.stack(codes[start:], 0)
        pre = 0
        if ref_ctx is not None and win.shape[0] < WINDOW:
            pre = min(WINDOW - win.shape[0], ref_ctx.shape[0])
            if pre:
                win = torch.cat([ref_ctx[-pre:], win], 0)
        wavs, _ = tok.decode([{"audio_codes": win}])
        w = np.asarray(wavs[0], dtype=np.float32)
        skip = (pre + emitted - start) * spf
        return w[skip:]


if __name__ == "__main__":
    run(Qwen3Backend(), KEY)
