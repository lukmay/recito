r"""
Chatterbox server (Resemble AI, MIT license).

Two sub-models in one process:
  en -> Chatterbox-Turbo (350M, GPT-2 backbone, one-step decoder, tags like [laugh])
  de and the other languages of MTL_LANGS -> Chatterbox-Multilingual V3
        (500M, Llama backbone, CFG + exaggeration)
Both share the voice encoder; the decoder (S3Gen) is selectable per sub-model.

Streaming: speech tokens are generated one at a time and decoded in windows
(fixed noise buffer, left context, crossfade), so the first sound comes after
a few tokens instead of after the whole sentence.

Environment (defaults are the chosen combination):
  CB_DE_MODEL   mtl3 | mtl2 | kartoffel     (default mtl3)
  CB_EN_MODEL   turbo | mtl3                (default turbo)
  CB_MTL_DEC    meanflow | s3gen | s3gen_v3 (decoder for mtl/kartoffel)
  CB_T3_DTYPE   bf16 | fp32 | fp16          (default bf16)
  CB_STREAM     1 | 0
  CB_WATERMARK  0 | 1  (Perth watermark, costs time; off by default)
  CB_LAZY       1 -> load the second sub-model on first use
  CB_GRAPH      1 | 0  decode step as a CUDA graph (~3.5x faster per token)
  CB_GRAPH_LEN  positions in the static KV cache (default 1600, ~400 MB)
  CB_MAX_VOICES voice conditionings kept in VRAM per sub-model (default 4)
  CB_FIRST_CFG, CB_FIRST_TURBO  tokens until the first audio chunk (default 12)
  CB_CTX        left context in tokens for window decoding (default 30)
  CB_DEFAULT_DE, CB_DEFAULT_EN  default voices (CB_DEFAULT_<LANG> for the others)
  CB_DEBUG      1 -> log decode window statistics
"""
import os
import re
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from server_base import Backend, log_fn, run, voices_dir  # noqa: E402

import numpy as np  # noqa: E402
import torch  # noqa: E402
import torch.nn.functional as F  # noqa: E402

from cuda_graph import StaticKV, capture  # noqa: E402

KEY = "chatterbox"
log = log_fn(KEY)
SR = 24000
TOK_SAMPLES = 960          # 25 tokens/s at 24 kHz
LOOKAHEAD = 3              # tokens the flow drops with finalize=False

ENV = os.environ.get
DE_MODEL = ENV("CB_DE_MODEL", "mtl3")
EN_MODEL = ENV("CB_EN_MODEL", "turbo")
MTL_DEC = ENV("CB_MTL_DEC", "meanflow")
T3_DTYPE = {"fp32": torch.float32, "bf16": torch.bfloat16,
            "fp16": torch.float16}[ENV("CB_T3_DTYPE", "bf16")]
STREAM = ENV("CB_STREAM", "1") == "1"
WATERMARK = ENV("CB_WATERMARK", "0") == "1"
LAZY = ENV("CB_LAZY", "0") == "1"
MAX_CONDS = int(ENV("CB_MAX_VOICES", "4"))
GRAPH = ENV("CB_GRAPH", "1") == "1"
GRAPH_LEN = int(ENV("CB_GRAPH_LEN", "1600"))

# Streaming schedule (tokens): a small first chunk for an early sound, then larger.
CHUNK_GROWTH = 1.4
MAX_CHUNK = 150
CTX_TOKENS = int(ENV("CB_CTX", "30"))
HOLD = (LOOKAHEAD + 1) * TOK_SAMPLES       # tail the next window renders again
DEBUG = ENV("CB_DEBUG", "0") == "1"
XFADE = 480                                # 20 ms crossfade
NOISE_PROMPT = 1200                        # prompt part of the fixed noise buffer (mel frames)

# Languages of the multilingual sub-model (chatterbox.mtl_tts SUPPORTED_LANGUAGES).
MTL_LANGS = ("ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "it", "ja", "ko",
             "ms", "nl", "no", "pl", "pt", "ru", "sv", "sw", "tr", "zh")

DEFAULTS = {  # sampling defaults per engine kind
    "turbo": dict(temperature=0.8, top_p=0.95, top_k=1000, repetition_penalty=1.2),
    "cfg": dict(temperature=0.8, top_p=1.0, min_p=0.05, repetition_penalty=1.2,
                cfg_weight=0.5, exaggeration=0.5),
}

# Turbo tokenizer (added_tokens.json): sounds and style tags. English/Turbo only.
TURBO_TAGS = ["[laugh]", "[chuckle]", "[sigh]", "[gasp]", "[cough]", "[clear throat]",
              "[sniff]", "[groan]", "[shush]", "[crying]",
              "[whispering]", "[happy]", "[angry]", "[sarcastic]", "[surprised]",
              "[fear]", "[dramatic]", "[narration]", "[advertisement]"]


def vram():
    if not torch.cuda.is_available():
        return "cpu"
    return (f"{torch.cuda.memory_allocated() / 2**20:.0f} MB allocated, "
            f"{torch.cuda.max_memory_allocated() / 2**20:.0f} MB peak")


# ----------------------------------------------------------------- Text --
_SENT = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÄÖÜ0-9\"„“(\[])")


def split_text(text, limit=220):
    """Split long input at sentence boundaries (commas if needed) into pieces
    of about `limit` characters; the model loses track beyond ~20 s of audio."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return [text]
    pieces = []
    for s in _SENT.split(text):
        while len(s) > limit:
            cut = max(s.rfind(", ", 0, limit), s.rfind("; ", 0, limit),
                      s.rfind(": ", 0, limit))
            if cut < limit // 3:
                cut = s.rfind(" ", 0, limit)
            if cut <= 0:
                break
            pieces.append(s[:cut + 1].strip())
            s = s[cut + 1:].strip()
        if s:
            pieces.append(s)
    out = []
    for piece in pieces:
        if out and len(out[-1]) + 1 + len(piece) <= limit:
            out[-1] += " " + piece
        else:
            out.append(piece)
    return out


_TAG = re.compile(r"\s*\[[a-z][a-z ]{1,20}\]")
_TIME_DE = re.compile(r"\b(\d{1,2}):(\d{2})(?:\s*Uhr\b)?")
_TIME_EN = re.compile(r"\b(\d{1,2}):(\d{2})(\s*[AaPp]\.?[Mm]\b\.?)?")


def prep_text(text, lang, kind):
    """Make clock times speakable (upstream turns ':' into a comma, so '9:30'
    would be read as a decimal) and drop Turbo tags for the other sub-models,
    which would read them as words."""
    def de_time(m):
        h, mi = m.group(1), m.group(2)
        return f"{h} Uhr" if mi == "00" else f"{h} Uhr {int(mi)}"

    def en_time(m):
        h, mi, ampm = m.group(1), m.group(2), m.group(3) or ""
        if mi == "00":
            return f"{h}{ampm}" if ampm else f"{h} o'clock"
        return f"{h} {'oh ' if mi.startswith('0') else ''}{int(mi)}{ampm}"

    if lang in ("de", "en"):
        text = (_TIME_DE if lang == "de" else _TIME_EN).sub(de_time if lang == "de" else en_time, text)
    if kind != "turbo":
        text = _TAG.sub("", text).strip()
    return text


def parse_instruct(instruct):
    """'exaggeration=0.7 cfg=0.3 temp=0.6 model=mtl3' -> dict. Free text maps
    a few keywords (dramatic/calm, German or English) to presets."""
    alias = {"exaggeration": "exaggeration", "exag": "exaggeration",
             "emotion": "exaggeration", "cfg": "cfg_weight", "cfg_weight": "cfg_weight",
             "temperature": "temperature", "temp": "temperature", "top_p": "top_p",
             "top_k": "top_k", "min_p": "min_p", "rep": "repetition_penalty",
             "repetition_penalty": "repetition_penalty", "seed": "seed", "model": "model"}
    out = {}
    for k, v in re.findall(r"([A-Za-z_]+)\s*[=:]\s*([^\s,;]+)", instruct or ""):
        k = alias.get(k.lower())
        if not k:
            continue
        if k == "model":
            out[k] = v.lower()
        else:
            try:
                out[k] = int(v) if k in ("top_k", "seed") else float(v)
            except ValueError:
                pass
    low = (instruct or "").lower()
    if not out and low:
        if any(w in low for w in ("dramat", "expressiv", "emotional", "aufgeregt", "excited")):
            out.update(exaggeration=0.8, cfg_weight=0.3)
        elif any(w in low for w in ("ruhig", "calm", "neutral", "sachlich", "monoton")):
            out.update(exaggeration=0.3, cfg_weight=0.5)
    return out


class _NoiseShim:
    """Stands in for torch inside the flow matching module: randn_like returns
    noise from a fixed buffer (prompt part plus the part at the absolute mel
    position), so overlapping decode windows produce the same mel frames."""

    def __init__(self):
        self.buf = None

    def set(self, buf, prompt_len, gen_off):
        self.buf, self.p, self.off = buf, prompt_len, gen_off

    def clear(self):
        self.buf = None

    def __getattr__(self, name):
        return getattr(torch, name)

    def randn_like(self, x, *a, **k):
        if self.buf is None or x.dim() != 3 or x.shape[0] != 1:
            return torch.randn_like(x, *a, **k)
        M = x.shape[2]
        p = min(self.p, M)
        g = self.buf[:, :, NOISE_PROMPT + self.off:NOISE_PROMPT + self.off + (M - p)]
        if g.shape[2] < M - p:
            return torch.randn_like(x, *a, **k)
        z = torch.cat([self.buf[:, :, :p], g], dim=2)
        return z.to(device=x.device, dtype=x.dtype)


_NOISE = _NoiseShim()


# --------------------------------------------------------------- Engines --
class Engine:
    """One T3 (text -> speech tokens), a decoder (tokens -> audio) and a voice cache."""
    kind = "cfg"
    first_chunk = int(ENV("CB_FIRST_CFG", "12"))   # tokens until the first sound (40 ms each)

    def __init__(self, name, t3, s3gen, ve, device):
        self.name, self.t3, self.s3gen, self.ve, self.device = name, t3, s3gen, ve, device
        self.conds = {}           # voice -> Conditionals, computed once per voice
        self.builtin = None       # Conditionals from conds.pt
        self.n_cfm = 2 if s3gen.meanflow else 10
        g = torch.Generator(device="cpu").manual_seed(1234)
        self.noise = torch.randn(1, 80, NOISE_PROMPT + 2 * 1100, generator=g).to(device)
        import chatterbox.models.s3gen.flow_matching as fm
        fm.torch = _NOISE
        fm.tqdm = lambda it, *a, **k: it          # no progress bars in the log
        fm.print = lambda *a, **k: None

    def conditionals(self, voice, wav_path):
        c = self.conds.pop(voice, None)
        if c is None:
            # At most MAX_CONDS voices per sub-model in VRAM, least recently used out.
            while len(self.conds) >= MAX_CONDS:
                old = next(iter(self.conds))
                del self.conds[old]
                import gc
                gc.collect()
                torch.cuda.empty_cache()
                log(f"{self.name}: voice '{old}' evicted")
            c = self.make_conds(wav_path)
            log(f"{self.name}: voice '{voice}' conditioned")
        self.conds[voice] = c                 # re-insert = most recently used
        return c

    @torch.inference_mode()                   # otherwise the graph stays attached to the profile
    def make_conds(self, wav_path):
        import librosa
        from chatterbox.models.s3tokenizer import S3_SR
        from chatterbox.models.s3gen import S3GEN_SR
        from chatterbox.models.t3.modules.cond_enc import T3Cond
        from chatterbox.mtl_tts import Conditionals
        wav24, _ = librosa.load(str(wav_path), sr=S3GEN_SR)
        if self.kind == "turbo":
            try:
                import pyloudnorm as ln
                meter = ln.Meter(S3GEN_SR)
                gain = 10.0 ** ((-27 - meter.integrated_loudness(wav24)) / 20.0)
                if np.isfinite(gain) and gain > 0:
                    wav24 = wav24 * gain
            except Exception:
                pass
        wav16 = librosa.resample(wav24, orig_sr=S3GEN_SR, target_sr=S3_SR)
        ref_dict = self.s3gen.embed_ref(wav24[:10 * S3GEN_SR], S3GEN_SR, device=self.device)
        plen = self.t3.hp.speech_cond_prompt_len
        toks, _ = self.s3gen.tokenizer.forward([wav16[:(15 if self.kind == "turbo" else 6) * S3_SR]],
                                               max_len=plen)
        toks = torch.atleast_2d(toks).to(self.device)
        emb = torch.from_numpy(self.ve.embeds_from_wavs([wav16], sample_rate=S3_SR))
        emb = emb.mean(axis=0, keepdim=True).to(self.device)
        t3c = T3Cond(speaker_emb=emb, cond_prompt_speech_tokens=toks,
                     emotion_adv=0.5 * torch.ones(1, 1, 1)).to(device=self.device)
        return Conditionals(t3c, ref_dict)

    def tokens(self, text, cond, p):
        """Generator of speech tokens, one per step."""
        raise NotImplementedError

    def max_tokens(self, text):
        return int(min(1000, 60 + 2.6 * len(text)))

    @torch.inference_mode()
    def decode(self, toks, start, gen_dict, final):
        """toks: LongTensor (T,) from token index `start` -> float32 audio."""
        if final and self.kind == "turbo":
            from chatterbox.models.s3gen.const import S3GEN_SIL
            toks = torch.cat([toks, torch.full((3,), S3GEN_SIL, dtype=toks.dtype,
                                               device=toks.device)])
        # Flow noise from the fixed buffer, aligned to the absolute token
        # position, so overlapping windows yield the same mel frames.
        P = int(gen_dict["prompt_feat"].shape[1])
        _NOISE.set(self.noise, P, 2 * start)
        try:
            mel = self.s3gen.forward(toks.unsqueeze(0), ref_wav=None, ref_sr=None,
                                     ref_dict=gen_dict, n_cfm_timesteps=self.n_cfm,
                                     finalize=True, skip_vocoder=True)
        finally:
            _NOISE.clear()
        wav, _ = self.s3gen.hift_inference(mel.to(self.s3gen.dtype), None)
        wav = wav.squeeze(0).float()
        if start == 0:
            tf = self.s3gen.trim_fade
            n = min(len(tf), wav.shape[0])
            wav[:n] *= tf[:n].to(wav.device)
        return wav.cpu().numpy()

    def stream(self, text, cond, p, streaming=True):
        """Generate tokens and decode them window by window -> float32 pieces."""
        return self.stream_tokens(self.tokens(text, cond, p), cond, streaming)

    def stream_tokens(self, token_iter, cond, streaming=True):
        toks = []
        emitted = 0          # absolute sample position emitted so far
        tail = None          # crossfade piece from `emitted` on, not emitted yet
        chunk = self.first_chunk
        next_at = chunk + LOOKAHEAD + 1
        fade_in = np.linspace(0, 1, XFADE, dtype=np.float32)
        stats = []

        def run_window(final):
            nonlocal emitted, tail
            n = len(toks)
            if n == 0:
                return None
            t0 = time.perf_counter()
            start = max(0, emitted // TOK_SAMPLES - CTX_TOKENS)
            seg = torch.tensor(toks[start:], dtype=torch.long, device=self.device)
            wav = self.decode(seg, start, cond.gen, final)
            off = start * TOK_SAMPLES
            if final:
                # The last token before EOS often sounds like noise (upstream
                # multilingual cuts it as well).
                end = off + len(wav) - (TOK_SAMPLES if self.kind == "cfg" else 0)
            else:
                # Upstream finalize=False is broken (mask mismatch), so every
                # window is finalized and the unreliable tail (lookahead plus
                # vocoder) is held back; the next window renders it again.
                end = off + len(wav) - HOLD
            stats.append((n - start, time.perf_counter() - t0))
            if end <= emitted + XFADE and not final:
                return None
            piece = wav[emitted - off:max(end, emitted) - off]
            if tail is not None and len(piece) >= len(tail):
                k = len(tail)
                piece = piece.copy()
                piece[:k] = tail * fade_in[::-1][:k] + piece[:k] * fade_in[:k]
            if final:
                tail = None
                emitted = end
                return piece
            out, tail = piece[:-XFADE], piece[-XFADE:].copy()
            emitted = end - XFADE
            return out

        for t in token_iter:
            toks.append(t)
            if streaming and len(toks) >= next_at:
                out = run_window(False)
                # Windows grow geometrically: small at first (early sound),
                # larger later (fewer decode passes while the buffer grows).
                if len(stats) > 1:
                    chunk = min(MAX_CHUNK, int(chunk * CHUNK_GROWTH) + 1)
                else:
                    chunk = max(8, int(chunk * 0.8))
                next_at = len(toks) + chunk
                if out is not None and len(out):
                    yield out
        out = run_window(True)
        if DEBUG:
            log(f"{self.name}: {len(toks)} tokens, decodes (window, s): "
                + " ".join(f"{w}/{d:.2f}" for w, d in stats))
        if out is not None and len(out):
            yield out


class TurboEngine(Engine):
    kind = "turbo"
    first_chunk = int(ENV("CB_FIRST_TURBO", "12"))

    def __init__(self, ckpt, ve, device):
        from safetensors.torch import load_file
        from transformers import AutoTokenizer
        from chatterbox.models.t3 import T3
        from chatterbox.models.t3.modules.t3_config import T3Config
        from chatterbox.tts_turbo import Conditionals
        hp = T3Config(text_tokens_dict_size=50276)
        hp.llama_config_name = "GPT2_medium"
        hp.speech_tokens_dict_size = 6563
        hp.input_pos_emb = None
        hp.speech_cond_prompt_len = 375
        hp.use_perceiver_resampler = False
        hp.emotion_adv = False
        t3 = T3(hp)
        st = load_file(Path(ckpt) / "t3_turbo_v1.safetensors")
        if "model" in st:
            st = st["model"][0]
        t3.load_state_dict(st)
        del t3.tfmr.wte
        t3.to(device, dtype=T3_DTYPE).eval()
        s3 = load_meanflow(ckpt, device)
        super().__init__("turbo", t3, s3, ve, device)
        self.tok = AutoTokenizer.from_pretrained(str(ckpt))
        if self.tok.pad_token is None:
            self.tok.pad_token = self.tok.eos_token
        cp = Path(ckpt) / "conds.pt"
        if cp.exists():
            self.builtin = Conditionals.load(cp, map_location=device).to(device)
        self.n_cfm = 2
        self.graph = make_decode_graph("turbo", t3, 1, device)

    @torch.inference_mode()
    def tokens(self, text, cond, p):
        from transformers.generation.logits_process import (
            LogitsProcessorList, RepetitionPenaltyLogitsProcessor,
            TemperatureLogitsWarper, TopKLogitsWarper, TopPLogitsWarper)
        from chatterbox.tts_turbo import punc_norm
        t3 = self.t3
        procs = LogitsProcessorList()
        if p["temperature"] > 0 and p["temperature"] != 1.0:
            procs.append(TemperatureLogitsWarper(p["temperature"]))
        if p["top_k"] > 0:
            procs.append(TopKLogitsWarper(int(p["top_k"])))
        if p["top_p"] < 1.0:
            procs.append(TopPLogitsWarper(p["top_p"]))
        if p["repetition_penalty"] != 1.0:
            procs.append(RepetitionPenaltyLogitsProcessor(p["repetition_penalty"]))
        ids = self.tok(punc_norm(text), return_tensors="pt").input_ids.to(self.device)
        start = t3.hp.start_speech_token * torch.ones_like(ids[:, :1])
        with torch.autocast("cuda", dtype=T3_DTYPE, enabled=T3_DTYPE != torch.float32):
            embeds, _ = t3.prepare_input_embeds(t3_cond=cond.t3, text_tokens=ids,
                                                speech_tokens=start, cfg_weight=0.0)
            out = t3.tfmr(inputs_embeds=embeds.to(T3_DTYPE), use_cache=True)
            past = out.past_key_values
            logits = t3.speech_head(out[0][:, -1:])[:, -1, :].float()
            n_max = self.max_tokens(text)
            g = self.graph if (self.graph is not None
                               and embeds.shape[1] + n_max + 1 <= self.graph.max_len) else None
            if g is not None:
                g.start(past)
            hist = start
            for _ in range(n_max):
                pl = procs(hist, logits)
                nxt = torch.multinomial(F.softmax(pl, dim=-1), 1)
                v = int(nxt)
                if v == t3.hp.stop_speech_token:
                    break
                hist = torch.cat([hist, nxt], dim=1)
                if v < 6561:
                    yield v
                xe = t3.speech_emb(nxt).to(T3_DTYPE)
                if g is not None:
                    logits = g.step(xe).float()
                else:
                    out = t3.tfmr(inputs_embeds=xe, past_key_values=past, use_cache=True)
                    past = out.past_key_values
                    logits = t3.speech_head(out[0])[:, -1, :].float()


class DecodeGraph:
    """One decode step of the T3 transformer (Llama or GPT-2, batch 2 for CFG)
    as a CUDA graph with a static KV cache. Eagerly each token costs ~29 ms,
    almost all Python and kernel launch overhead. Prefill and sampling stay
    outside the graph."""

    def __init__(self, llama, head, batch, max_len, dtype, dev):
        self.llama, self.head, self.max_len = llama, head, max_len
        cfg = llama.config                  # LlamaConfig (mtl/kartoffel) or GPT2Config (Turbo)
        hd = getattr(cfg, "head_dim", None) or cfg.hidden_size // cfg.num_attention_heads
        kvh = getattr(cfg, "num_key_value_heads", None) or cfg.num_attention_heads
        self.kv = StaticKV(cfg.num_hidden_layers, kvh, max_len, hd, dtype, dev, batch=batch)
        self.x = torch.zeros(batch, 1, cfg.hidden_size, dtype=dtype, device=dev)
        self.cache_pos = torch.zeros(1, dtype=torch.long, device=dev)
        self.pos = torch.zeros(1, 1, dtype=torch.long, device=dev)
        self.mask = torch.full((1, 1, 1, max_len), float("-inf"), dtype=dtype, device=dev)
        self.mask[..., 0] = 0
        self.n = 0
        self.graph = capture(self._body, autocast_dtype=dtype)

    def _body(self):
        out = self.llama(inputs_embeds=self.x, attention_mask=self.mask,
                         position_ids=self.pos, cache_position=self.cache_pos,
                         past_key_values=self.kv, use_cache=True)
        self.logits = self.head(out.last_hidden_state)[:, -1, :]

    def start(self, pkv):
        """Take over the KV cache of the regular prefill (DynamicCache)."""
        n = self.kv.load(pkv)
        self.mask.fill_(float("-inf"))
        self.mask[..., :n] = 0
        self.n = n

    def step(self, x):
        """Advance one token -> logits (a static buffer, overwritten by the
        next step)."""
        p = self.n
        self.x.copy_(x)
        self.cache_pos.fill_(p)
        self.pos.fill_(p)
        self.mask[..., p] = 0
        self.graph.replay()
        self.n = p + 1
        return self.logits


def make_decode_graph(name, t3, batch, device):
    """DecodeGraph for a T3, or None (disabled, no CUDA, or capture failed;
    decoding then runs eagerly, only slower)."""
    if not (GRAPH and str(device).startswith("cuda")):
        return None
    try:
        t0 = time.perf_counter()
        g = DecodeGraph(t3.tfmr, t3.speech_head, batch, GRAPH_LEN, T3_DTYPE, device)
        log(f"{name}: decode step as CUDA graph ({GRAPH_LEN} positions, "
            f"{time.perf_counter() - t0:.1f} s)")
        return g
    except Exception as exc:
        log(f"{name}: CUDA graph not possible ({type(exc).__name__}: {exc})")
        return None


class CfgEngine(Engine):
    """Multilingual V2/V3 or Kartoffelbox (original Chatterbox architecture with CFG)."""
    kind = "cfg"

    def __init__(self, name, t3_file, multilingual, tokenizer_file, s3, ve, device,
                 builtin=None):
        from safetensors.torch import load_file
        from chatterbox.models.t3 import T3
        from chatterbox.models.t3.modules.t3_config import T3Config
        from chatterbox.models.t3.inference.t3_hf_backend import T3HuggingfaceBackend
        from chatterbox.models.tokenizers import EnTokenizer, MTLTokenizer
        from chatterbox.mtl_tts import Conditionals
        t3 = T3(T3Config.multilingual() if multilingual else T3Config.english_only())
        st = load_file(t3_file)
        if "model" in st:
            st = st["model"][0]
        t3.load_state_dict(st)
        t3.to(device, dtype=T3_DTYPE).eval()
        super().__init__(name, t3, s3, ve, device)
        self.multilingual = multilingual
        self.tok = (MTLTokenizer if multilingual else EnTokenizer)(str(tokenizer_file))
        self.hf = T3HuggingfaceBackend(config=t3.cfg, llama=t3.tfmr,
                                       speech_enc=t3.speech_emb, speech_head=t3.speech_head)
        if builtin and Path(builtin).exists():
            self.builtin = Conditionals.load(builtin, map_location=device).to(device)
        self.graph = make_decode_graph(name, t3, 2, device)

    def max_tokens(self, text):
        return int(min(1000, 60 + 2.8 * len(text)))

    @torch.inference_mode()
    def tokens(self, text, cond, p):
        from transformers.generation.logits_process import (
            MinPLogitsWarper, RepetitionPenaltyLogitsProcessor, TopPLogitsWarper)
        from chatterbox.models.t3.modules.cond_enc import T3Cond
        from chatterbox.mtl_tts import punc_norm
        t3 = self.t3
        c = cond.t3
        tc = T3Cond(speaker_emb=c.speaker_emb, cond_prompt_speech_tokens=c.cond_prompt_speech_tokens,
                    emotion_adv=float(p["exaggeration"]) * torch.ones(1, 1, 1)).to(device=self.device)
        text = punc_norm(text)
        if self.multilingual:
            ids = self.tok.text_to_tokens(text, language_id=p["lang"]).to(self.device)
        else:
            ids = self.tok.text_to_tokens(text).to(self.device)
        cfg = float(p["cfg_weight"])
        ids = torch.cat([ids, ids], dim=0)
        ids = F.pad(ids, (1, 0), value=t3.hp.start_text_token)
        ids = F.pad(ids, (0, 1), value=t3.hp.stop_text_token)
        bos = torch.tensor([[t3.hp.start_speech_token]], dtype=torch.long, device=self.device)
        with torch.autocast("cuda", dtype=T3_DTYPE, enabled=T3_DTYPE != torch.float32):
            embeds, _ = t3.prepare_input_embeds(t3_cond=tc, text_tokens=ids,
                                                speech_tokens=bos.expand(2, 1), cfg_weight=cfg)
            bos_e = t3.speech_emb(bos) + t3.speech_pos_emb.get_fixed_embedding(0)
            x = torch.cat([embeds, torch.cat([bos_e, bos_e])], dim=1).to(T3_DTYPE)
            out = self.hf(inputs_embeds=x, past_key_values=None, use_cache=True,
                          output_attentions=False, output_hidden_states=True, return_dict=True)
            past = out.past_key_values
            lgt = out.logits[:, -1, :]
            n_max = self.max_tokens(text)
            g = self.graph if (self.graph is not None
                               and x.shape[1] + n_max + 1 <= self.graph.max_len) else None
            if g is not None:
                g.start(past)
            top_p = TopPLogitsWarper(top_p=p["top_p"])
            min_p = MinPLogitsWarper(min_p=p["min_p"])
            rep = RepetitionPenaltyLogitsProcessor(penalty=float(p["repetition_penalty"]))
            hist = bos
            cfgt = None
            for i in range(n_max):
                lg = lgt.float()
                if cfgt is None:
                    cfgt = torch.as_tensor(cfg, device=lg.device, dtype=lg.dtype)
                lg = lg[0:1] + cfgt * (lg[0:1] - lg[1:2])
                lg = rep(hist, lg)
                if p["temperature"] != 1.0:
                    lg = lg / p["temperature"]
                lg = min_p(hist, lg)
                lg = top_p(hist, lg)
                nxt = torch.multinomial(torch.softmax(lg, dim=-1), 1)
                v = int(nxt)
                if v == t3.hp.stop_speech_token:
                    break
                hist = torch.cat([hist, nxt], dim=1)
                if v < 6561:
                    yield v
                e = t3.speech_emb(nxt) + t3.speech_pos_emb.get_fixed_embedding(i + 1)
                xe = torch.cat([e, e]).to(T3_DTYPE)
                if g is not None:
                    lgt = g.step(xe)
                else:
                    out = self.hf(inputs_embeds=xe, past_key_values=past,
                                  output_attentions=False, output_hidden_states=True,
                                  return_dict=True)
                    past = out.past_key_values
                    lgt = out.logits[:, -1, :]


# ---------------------------------------------------------------- Loading --
_S3_CACHE = {}


def load_meanflow(turbo_ckpt, device):
    if "meanflow" not in _S3_CACHE:
        from safetensors.torch import load_file
        from chatterbox.models.s3gen import S3Gen
        s3 = S3Gen(meanflow=True)
        s3.load_state_dict(load_file(Path(turbo_ckpt) / "s3gen_meanflow.safetensors"), strict=True)
        _S3_CACHE["meanflow"] = s3.to(device).eval()
    return _S3_CACHE["meanflow"]


def load_s3gen(kind, mtl_ckpt, turbo_ckpt, device):
    if kind == "meanflow":
        return load_meanflow(turbo_ckpt, device)
    if kind not in _S3_CACHE:
        from chatterbox.models.s3gen import S3Gen
        s3 = S3Gen()
        f = Path(mtl_ckpt) / ("s3gen_v3.safetensors" if kind == "s3gen_v3" else "s3gen.pt")
        if f.suffix == ".pt":
            st = torch.load(f, map_location="cpu", weights_only=True)
        else:
            from safetensors.torch import load_file
            st = load_file(f)
        s3.load_state_dict(st, strict=False)
        _S3_CACHE[kind] = s3.to(device).eval()
    return _S3_CACHE[kind]


def snapshot(repo, patterns=None, revision=None):
    from huggingface_hub import snapshot_download
    return Path(snapshot_download(repo, allow_patterns=patterns, revision=revision))


class ChatterboxBackend(Backend):
    languages = MTL_LANGS
    name = "chatterbox"
    sample_rate = SR
    tags = TURBO_TAGS
    instruct = True

    def __init__(self):
        self.engines = {}
        self.dev = "cuda" if torch.cuda.is_available() else "cpu"
        self._lock = threading.Lock()

    def _ve(self):
        if not hasattr(self, "ve_"):
            from safetensors.torch import load_file
            from chatterbox.models.voice_encoder import VoiceEncoder
            ve = VoiceEncoder()
            ve.load_state_dict(load_file(self.turbo_ckpt / "ve.safetensors"))
            self.ve_ = ve.to(self.dev).eval()
        return self.ve_

    @property
    def mtl_ckpt(self):
        if not hasattr(self, "_mtl_ckpt"):
            self._mtl_ckpt = snapshot("ResembleAI/chatterbox",
                                      ["ve.pt", "t3_mtl23ls_v3.safetensors", "s3gen.pt",
                                       "s3gen_v3.safetensors", "grapheme_mtl_merged_expanded_v1.json",
                                       "conds.pt", "Cangjie5_TC.json"])
        return self._mtl_ckpt

    def engine(self, key):
        with self._lock:
            if key in self.engines:
                return self.engines[key]
            t0 = time.perf_counter()
            if key == "turbo":
                e = TurboEngine(self.turbo_ckpt, self._ve(), self.dev)
            elif key in ("mtl3", "mtl2"):
                f = self.mtl_ckpt / ("t3_mtl23ls_v3.safetensors" if key == "mtl3"
                                     else "t3_mtl23ls_v2.safetensors")
                if not f.exists():
                    snapshot("ResembleAI/chatterbox", [f.name])
                e = CfgEngine(key, f, True, self.mtl_ckpt / "grapheme_mtl_merged_expanded_v1.json",
                              load_s3gen(MTL_DEC, self.mtl_ckpt, self.turbo_ckpt, self.dev),
                              self._ve(), self.dev, builtin=self.mtl_ckpt / "conds.pt")
            elif key == "kartoffel":
                from huggingface_hub import hf_hub_download
                f = hf_hub_download("mister2314/kartoffelbox-de", "t3_cfg.safetensors",
                                    revision="e49b841fb2ca2685424c57a4015bff7faa00bd7e")
                tokf = hf_hub_download("ResembleAI/chatterbox", "tokenizer.json")
                e = CfgEngine(key, f, False, tokf,
                              load_s3gen(MTL_DEC, self.mtl_ckpt, self.turbo_ckpt, self.dev),
                              self._ve(), self.dev, builtin=self.mtl_ckpt / "conds.pt")
            else:
                raise ValueError(f"unknown sub-model {key}")
            self.engines[key] = e
            log(f"{key} loaded in {time.perf_counter() - t0:.1f} s, VRAM {vram()}")
            return e

    def load(self):
        self.turbo_ckpt = snapshot("ResembleAI/chatterbox-turbo",
                                   ["*.safetensors", "*.json", "*.txt", "*.pt", "*.model"])
        log(f"config: de={DE_MODEL} en={EN_MODEL} decoder(mtl)={MTL_DEC} "
            f"t3={T3_DTYPE} stream={STREAM} lazy={LAZY}")
        first, second = (EN_MODEL, DE_MODEL)
        self.engine(first)
        if not LAZY and second != first:
            self.engine(second)
        # Warm up CUDA kernels, cuDNN autotuning and the allocator; this also
        # conditions the default voice.
        for lang in (["en"] if LAZY else ["en", "de"]):
            for _ in self.synth("Hallo, kurzer Test." if lang == "de" else "Hello there, short test.",
                                "default", lang):
                pass
        # Condition the default voices up front (~0.5 s otherwise on the first
        # sentence). Only these: every further voice costs VRAM.
        for lang, vid in self.default_voices().items():
            key = EN_MODEL if lang == "en" else DE_MODEL
            if key in self.engines:
                self._cond(self.engines[key], vid, lang)
        log(f"ready, VRAM {vram()}")

    def _ref_ids(self):
        d = voices_dir()
        return sorted(p.stem for p in d.glob("*.wav")) if d.exists() else []

    def voices(self):
        v = [{"id": "builtin", "lang": "en",
              "desc": "Eingebaute Stimme des Teilmodells (weiblich, Englisch; auf Deutsch mit Akzent)"},
             {"id": "default", "lang": "de", "desc": "Standardstimme der jeweiligen Sprache"}]
        for rid in self._ref_ids():
            lang = rid[:2] if rid[:2] in MTL_LANGS and rid[2:3] in ("_", "-") else "de"
            v.append({"id": f"ref:{rid}", "lang": lang,
                      "desc": f"Geklont aus voices/{rid}.wav"})
        return v

    def default_voices(self):
        ids = set(self._ref_ids())
        de = ENV("CB_DEFAULT_DE") or next((f"ref:{i}" for i in ("de_female_1", "de_male_1")
                                           if i in ids), "builtin")
        en = ENV("CB_DEFAULT_EN") or "builtin"
        out = {"de": de, "en": en}
        for lang in MTL_LANGS:            # own recordings named <lang>_female_1 etc.
            if lang not in out:
                v = ENV(f"CB_DEFAULT_{lang.upper()}") or next(
                    (f"ref:{i}" for i in (f"{lang}_female_1", f"{lang}_male_1") if i in ids), "")
                if v:
                    out[lang] = v
        return out

    def _cond(self, eng, voice, lang, _depth=0):
        """Voice -> conditioning (cached per sub-model and voice). Unknown
        voices and "default" use the default voice of the language."""
        v = (voice or "").strip()
        if v.startswith("ref:"):
            v = v[4:]
        if v == "builtin":
            if eng.builtin is None:
                raise RuntimeError(f"{eng.name}: no built-in voice")
            return eng.builtin
        wav = voices_dir() / f"{v}.wav" if v and v != "default" else None
        if wav is not None and wav.exists():
            return eng.conditionals(v, wav)
        if _depth:
            return eng.builtin
        return self._cond(eng, self.default_voices().get(lang, "builtin"), lang, 1)

    def synth(self, text, voice, lang, speed=1.0, instruct=""):
        # No speed control in the model: speed is ignored, the daemon time-stretches.
        lang = (lang or "").lower()[:2]
        lang = lang if lang in MTL_LANGS else "en"
        opts = parse_instruct(instruct)
        other = DE_MODEL if DE_MODEL in ("mtl2", "mtl3") else "mtl3"   # neither de nor en
        key = opts.pop("model", None) or {"de": DE_MODEL, "en": EN_MODEL}.get(lang, other)
        if key in ("mtl", "multilingual"):
            key = "mtl3"
        if key == "kartoffel" and lang != "de":
            key = "mtl3"
        if key == "turbo" and lang != "en":             # Turbo is English only
            key = other
        eng = self.engine(key)
        p = dict(DEFAULTS[eng.kind])
        p.update({k: v for k, v in opts.items() if k in p or k == "seed"})
        p["lang"] = lang
        if "seed" in p:
            torch.manual_seed(int(p.pop("seed")))
        cond = self._cond(eng, voice, lang)
        wm = None
        if WATERMARK:
            import perth
            wm = perth.PerthImplicitWatermarker()
        parts = split_text(prep_text(text, lang, eng.kind))
        for i, part in enumerate(parts):
            if i:
                yield np.zeros(int(0.12 * SR), dtype=np.float32)
            for chunk in eng.stream(part, cond, p, streaming=STREAM):
                if wm is not None:
                    chunk = wm.apply_watermark(chunk, sample_rate=SR)
                yield chunk


if __name__ == "__main__":
    run(ChatterboxBackend(), KEY)
