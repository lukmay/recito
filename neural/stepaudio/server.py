r"""
Step-Audio-EditX (StepFun, 3.5B) TTS server, native on Windows.
Protocol: ../server_base.py.

Clones a voice from reference audio plus transcript (zero-shot) and can edit
the result in further LLM passes (emotion, style, speed, paralinguistic tags).
No vLLM: step_llm.py runs the Step1 LLM itself (static KV cache, one CUDA
graph per decode step).

Per sentence: prefill (voice prefix from the cache, only the target text is
computed) -> sample audio tokens -> CosyVoice flow + HiFT in streaming mode
-> 24 kHz PCM to the client in pieces.

Started by neural/launcher.py as: python server.py --port <port>
"""
import hashlib
import os
import re
import sys
import threading
import time
import types
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from server_base import Backend, data_dir, log_fn, run, voices_dir  # noqa: E402

KEY = "stepaudio"
log = log_fn(KEY)
ROOT = data_dir() / KEY
SRC = Path(os.environ.get("STEPAUDIO_SRC", ROOT / "src"))
MODELS = Path(os.environ.get("STEPAUDIO_MODELS", ROOT / "models"))
LLM_DIR = MODELS / "Step-Audio-EditX"
TOK_DIR = MODELS / "Step-Audio-Tokenizer"
CACHE = ROOT / "voicecache"

sys.path.insert(0, str(SRC))

# vLLM is not available on Windows; the repo modules import it only for types
# and loaders that are not used here.
_v = types.ModuleType("vllm")
_v.LLM = object
_v.SamplingParams = object
sys.modules.setdefault("vllm", _v)

import soundfile as sf  # noqa: E402
import torch  # noqa: E402
import torch.nn.functional as F  # noqa: E402
import torchaudio  # noqa: E402


def _ta_load(f, *a, **k):
    """Replacement for torchaudio.load: torchaudio>=2.9 loads only through
    torchcodec, which is unreliable on Windows."""
    if hasattr(f, "seek"):
        f.seek(0)
    data, sr = sf.read(f, dtype="float32", always_2d=True)
    return torch.from_numpy(data.T.copy()), sr


torchaudio.load = _ta_load

SR = 24000
AUDIO0 = 65536            # first audio token
AUDIO_N = 2048            # vq02 (1024) + vq06 (1024)
EOT = 3
TOK_PER_S = 25 / 3 * 5    # 41.7 mixed tokens per second
TEMPERATURE = float(os.environ.get("STEPAUDIO_TEMP", "0.7"))
MAX_REF_S = 15.0

TAGS = ["[Laughter]", "[Breathing]", "[Sigh]", "[Uhm]", "[Surprise-oh]",
        "[Surprise-ah]", "[Surprise-wa]", "[Confirmation-en]", "[Question-ei]",
        "[Dissatisfaction-hnn]", "[laugh]", "[chuckle]", "[giggle]", "[sigh]",
        "[inhale]", "[exhale]", "[breath]", "[cough]", "[clears throat]",
        "[snort]", "[uhm]"]
TAG_RE = re.compile(r"\[[A-Za-z][A-Za-z \-]*\]")

EMOTIONS = {'happy', 'angry', 'sad', 'humour', 'confusion', 'disgusted', 'empathy',
            'embarrass', 'fear', 'surprised', 'excited', 'depressed', 'coldness',
            'admiration', 'remove'}
STYLES = {'serious', 'arrogant', 'child', 'older', 'girl', 'pure', 'sister', 'sweet',
          'ethereal', 'whisper', 'gentle', 'recite', 'generous', 'act_coy', 'warm',
          'shy', 'comfort', 'authority', 'chat', 'radio', 'soulful', 'story', 'vivid',
          'program', 'news', 'advertising', 'roar', 'murmur', 'shout', 'deeply',
          'loudly', 'remove', 'exaggerated'}
SPEEDS = {'faster', 'slower', 'more faster', 'more slower'}

# References shipped with the repo (transcripts in examples/text.txt)
REPO_VOICES = {
    "en_repo_male": ("zero_shot_en_prompt.wav", "en",
                     "Repo-Beispiel, englisch, männlich, neutral"),
    "en_repo_happy": ("en_happy_prompt.wav", "en",
                      "Repo-Beispiel, englisch, fröhlich"),
}
SHARED = {"de_female_1": "de", "de_male_1": "de", "en_female_1": "en", "en_male_1": "en"}


def repo_transcripts():
    out = {}
    p = SRC / "examples" / "text.txt"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            parts = re.split(r"\s+", line.strip(), maxsplit=1)
            if len(parts) == 2:
                out[parts[0]] = parts[1].strip()
    return out


# ---------------------------------------------------------------- text
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")


def normalize(text, lang):
    """Spell out numbers; the model does not know German and otherwise reads
    digits in English or Chinese. Tags stay untouched."""
    try:
        from num2words import num2words
    except ImportError:
        return text
    if lang == "de":
        text = re.sub(r"(\d{1,2}):(\d{2})\s*Uhr",
                      lambda m: (f"{m.group(1)} Uhr {int(m.group(2))}" if int(m.group(2))
                                 else f"{m.group(1)} Uhr"), text)
        text = re.sub(r"(\d+),(\d{2})\s*Euro", lambda m: f"{m.group(1)} Euro {int(m.group(2))}", text)
        text = re.sub(r"(\d+)\s*%", r"\1 Prozent", text)

        def de(m):
            s = m.group(0)
            nxt = text[m.end():m.end() + 1]
            try:
                if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
                    return num2words(int(s.replace(".", "")), lang="de")
                if "," in s:
                    a, b = s.split(",", 1)
                    return num2words(int(a), lang="de") + " Komma " + " ".join(
                        num2words(int(c), lang="de") for c in b)
                if s.endswith(".") or nxt == ".":
                    pass
                return num2words(int(s.replace(".", "")), lang="de")
            except Exception:
                return s
        # ordinals such as "14. November"
        text = re.sub(r"\b(\d{1,2})\.\s+(?=[A-ZÄÖÜ])",
                      lambda m: num2words(int(m.group(1)), lang="de", to="ordinal") + " ", text)
        text = _NUM_RE.sub(de, text)
    return text


def split_sentences(text, maxlen=220):
    """Split long texts into sentences (the model is trained on < 30 s of audio)."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= maxlen:
        return [text]
    parts = re.split(r"(?<=[.!?;:])\s+", text)
    out, cur = [], ""
    for p in parts:
        if cur and len(cur) + len(p) + 1 > maxlen:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}".strip()
    if cur:
        out.append(cur)
    res = []
    for s in out:   # last resort: split at commas
        while len(s) > maxlen * 1.6:
            cut = s.rfind(", ", 0, maxlen)
            if cut < 40:
                break
            res.append(s[:cut + 1])
            s = s[cut + 2:]
        res.append(s)
    return res


def parse_instruct(instruct):
    """'emotion=happy, style=whisper x2, speed=faster, para' -> [(type, info), ...]."""
    edits = []
    for part in re.split(r"[;,]\s*", instruct or ""):
        part = part.strip().lower()
        if not part:
            continue
        reps = 1
        m = re.search(r"\s*[x×](\d)$", part)
        if m:
            reps = int(m.group(1))
            part = part[:m.start()].strip()
        if "=" in part:
            k, v = [s.strip() for s in part.split("=", 1)]
        elif part in EMOTIONS:
            k, v = "emotion", part
        elif part in STYLES:
            k, v = "style", part
        elif part in SPEEDS:
            k, v = "speed", part
        else:
            k, v = part, ""
        v = v.replace("_", " ") if k == "speed" else v
        if k in ("emotion", "style", "speed") and v:
            edits += [(k, v)] * reps
        elif k in ("para", "paralinguistic"):
            edits.append(("paralinguistic", ""))
    return edits


# ---------------------------------------------------------------- backend
class StepAudioBackend(Backend):
    name = "stepaudio"
    sample_rate = SR
    tags = TAGS
    instruct = True

    def __init__(self):
        self._voices = {}       # id -> (wav, transcript, lang, desc)
        self._prompt = {}       # id -> dict (tokens, mel, embedding)
        self._prefix = {}       # id -> (ids, kv)
        self._tokenizer = None
        self.speed = 1.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------ loading
    def load(self):
        t0 = time.perf_counter()
        torch.backends.cuda.matmul.allow_tf32 = True
        from transformers import AutoTokenizer
        from step_llm import StepLM
        from stepvocoder.cosyvoice2.cli.cosyvoice import CosyVoice
        from config.prompts import AUDIO_EDIT_CLONE_SYSTEM_PROMPT_TPL, AUDIO_EDIT_SYSTEM_PROMPT
        self.clone_tpl = AUDIO_EDIT_CLONE_SYSTEM_PROMPT_TPL
        self.edit_sys = AUDIO_EDIT_SYSTEM_PROMPT

        self.tok = AutoTokenizer.from_pretrained(str(LLM_DIR), trust_remote_code=True)
        assert self.tok.convert_tokens_to_ids("<audio_0>") == AUDIO0, "audio token offset"
        self.lm = StepLM(str(LLM_DIR), max_len=int(os.environ.get("STEPAUDIO_MAXLEN", "4096")),
                         out_ids=[EOT] + list(range(AUDIO0, AUDIO0 + AUDIO_N)),
                         quant=(os.environ.get("STEPAUDIO_QUANT", "") or None))
        log(f"LLM loaded ({self.lm.quant or 'bf16'}, {time.perf_counter() - t0:.1f} s), VRAM "
            f"{torch.cuda.memory_allocated() / 2**20:.0f} MB")
        cg = os.environ.get("STEPAUDIO_GRAPH", "1") == "1"
        if cg:
            self.lm.capture()
            log("decode CUDA graph captured")
        self.cosy = CosyVoice(str(LLM_DIR / "CosyVoice-300M-25Hz"), dtype=torch.bfloat16,
                              enable_cuda_graph=False)
        self._wrap_flow_speed()
        log(f"CosyVoice loaded, VRAM {torch.cuda.memory_allocated() / 2**20:.0f} MB")

        self._scan_voices()
        # Encode the prompts of all voices up front (disk cache), then free the tokenizer.
        for vid in list(self._voices):
            try:
                self._get_prompt(vid)
            except Exception as exc:
                log(f"voice {vid} unusable: {exc}")
                self._voices.pop(vid, None)
        self._free_tokenizer()
        self._fit_flow_cache()
        # Fill the prefix KV of the default voices and warm up the kernels.
        warm = {} if os.environ.get("STEPAUDIO_NOWARM") else self.default_voices()
        for lang, vid in warm.items():
            try:
                for _ in self.synth("Hello." if lang == "en" else "Hallo.", vid, lang):
                    pass
            except Exception as exc:
                log(f"warm-up {vid}: {exc}")
        log(f"ready after {time.perf_counter() - t0:.1f} s, VRAM "
            f"{torch.cuda.memory_allocated() / 2**20:.0f} MB (max "
            f"{torch.cuda.max_memory_allocated() / 2**20:.0f} MB)")

    def _fit_flow_cache(self, frames=None):
        """CosyVoice reserves attention caches for 16 flow steps x 1000 frames
        (~1.1 GB). Needed are n_timesteps (10) x (prompt frames + 2 s context
        + chunk). Sizing them to the longest reference saves ~0.5 GB VRAM."""
        if frames is None:
            frames = max((len(self._prompt[v]["vq0206"]) // 5 * 6 for v in self._prompt), default=600)
        L = frames + 240
        fm = self.cosy.cosy_impl.flow.decoder
        cur = getattr(self, "_flow_L", 0)
        if L <= cur:
            return
        n = self.cosy.cosy_impl.n_timesteps
        a = fm.att_cache_buffer
        fm.att_cache_buffer = torch.zeros((n,) + tuple(a.shape[1:4]) + (L, a.shape[5]),
                                          dtype=a.dtype, device=a.device)
        e = fm.estimator.att_cache_buffer
        fm.estimator.att_cache_buffer = torch.zeros(tuple(e.shape[:3]) + (L, e.shape[4]),
                                                    dtype=e.dtype, device=e.device)
        del a, e
        torch.cuda.empty_cache()
        self._flow_L = L
        log(f"flow cache sized to {L} frames, VRAM "
            f"{torch.cuda.memory_allocated() / 2**20:.0f} MB")

    def _wrap_flow_speed(self):
        flow = self.cosy.cosy_impl.flow
        orig = flow.inference_chunk
        be = self

        def chunk(*a, **k):
            mel, cache = orig(*a, **k)
            sp = be.speed
            if abs(sp - 1.0) > 1e-3 and mel.shape[2] > 1:
                be._mel_carry += mel.shape[2] / sp
                n = int(be._mel_carry)
                be._mel_carry -= n
                mel = F.interpolate(mel.float(), size=max(n, 1), mode="linear").to(mel.dtype)
            return mel, cache
        flow.inference_chunk = chunk
        self._mel_carry = 0.0

    # ------------------------------------------------------------ voices
    def _scan_voices(self):
        tr = repo_transcripts()
        for vid, (fn, lang, desc) in REPO_VOICES.items():
            wav = SRC / "examples" / fn
            if wav.exists() and fn in tr:
                self._voices[vid] = (wav, tr[fn], lang, desc)
        vd = voices_dir()
        if vd.exists():
            for wav in sorted(vd.glob("*.wav")):
                txt = wav.with_suffix(".txt")
                if not txt.exists():
                    continue
                vid = wav.stem
                lang = SHARED.get(vid) or (vid[:2] if vid[:2] in ("de", "en") else "en")
                self._voices[vid] = (wav, txt.read_text(encoding="utf-8").strip(), lang,
                                     "gemeinsame Referenzstimme (geklont)")

    def voices(self):
        return [{"id": k, "lang": v[2], "desc": v[3]} for k, v in self._voices.items()]

    def default_voices(self):
        pref = {"de": ["de_female_1", "de_male_1", "en_repo_male"],
                "en": ["en_female_1", "en_repo_male", "en_male_1"]}
        env = {"de": os.environ.get("STEPAUDIO_DEFAULT_DE"),
               "en": os.environ.get("STEPAUDIO_DEFAULT_EN")}
        out = {}
        for lang, cands in pref.items():
            cands = ([env[lang]] if env[lang] else []) + cands
            out[lang] = next((c for c in cands if c in self._voices),
                             next(iter(self._voices), ""))
        return out

    def _tokenizer_obj(self):
        if self._tokenizer is None:
            t0 = time.perf_counter()
            from tokenizer import StepAudioTokenizer
            from model_loader import ModelSource
            self._tokenizer = StepAudioTokenizer(str(TOK_DIR), model_source=ModelSource.LOCAL)
            log(f"audio tokenizer loaded ({time.perf_counter() - t0:.1f} s)")
        return self._tokenizer

    def _free_tokenizer(self):
        if self._tokenizer is not None and os.environ.get("STEPAUDIO_KEEP_TOKENIZER") != "1":
            self._tokenizer = None
            import gc
            gc.collect()
            torch.cuda.empty_cache()

    def _get_prompt(self, vid):
        if vid in self._prompt:
            return self._prompt[vid]
        wav, text, lang, _ = self._voices[vid]
        raw = Path(wav).read_bytes()
        h = hashlib.sha1(raw + text.encode("utf-8") + b"v2").hexdigest()[:16]
        cp = CACHE / f"{vid}-{h}.pt"
        if cp.exists():
            d = torch.load(cp, map_location="cpu")
        else:
            audio, sr = _ta_load(str(wav))
            audio = audio.mean(0, keepdim=True)
            if audio.shape[1] > MAX_REF_S * sr:
                log(f"{vid}: reference is {audio.shape[1] / sr:.1f} s, using the first {MAX_REF_S} s")
                audio = audio[:, :int(MAX_REF_S * sr)]
            norm = audio.abs().max()
            if norm > 0.6:
                audio = audio / norm * 0.6
            fe = self.cosy.frontend
            speech_feat, _ = fe.extract_speech_feat(audio, sr)
            emb = fe.extract_spk_embedding(audio, sr)
            tk = self._tokenizer_obj()
            vq0206, vq02, vq06 = tk.wav2token(audio, sr)
            d = {"vq0206": list(vq0206),
                 "tokens": tk.merge_vq0206_to_token_str(vq02, vq06),
                 "feat": speech_feat.cpu(), "emb": emb.cpu(), "text": text}
            CACHE.mkdir(parents=True, exist_ok=True)
            torch.save(d, cp)
            log(f"{vid}: prompt encoded ({len(d['vq0206'])} tokens) -> {cp.name}")
        d["prompt_token"] = torch.tensor([d["vq0206"]], dtype=torch.long) - AUDIO0
        self._prompt[vid] = d
        if getattr(self, "_flow_L", 0):
            self._fit_flow_cache(len(d["vq0206"]) // 5 * 6)
        return d

    # ------------------------------------------------------------ LLM
    def _chat_ids(self, system, user):
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        ids = self.tok.apply_chat_template(msgs, tokenize=True, add_generation_prompt=True)
        if isinstance(ids, dict) or hasattr(ids, "input_ids"):
            ids = ids["input_ids"]
        return list(ids)

    def _clone_ids(self, vid, text):
        p = self._get_prompt(vid)
        sys_prompt = self.clone_tpl.format(speaker="debug", prompt_text=p["text"],
                                           prompt_wav_tokens=p["tokens"])
        return self._chat_ids(sys_prompt, text)

    def _prefill(self, ids, cache_key=None):
        """Prefill with a reusable prefix (up to and including the first <|EOT|>)."""
        if cache_key is not None:
            hit = self._prefix.get(cache_key)
            if hit and ids[:len(hit[0])] == hit[0]:
                n = self.lm.load_prefix(hit[1])
                return self.lm.prefill(ids[n:], pos0=n), len(ids)
            n = ids.index(EOT) + 1 if EOT in ids else 0
            if n:
                self.lm.prefill(ids[:n], pos0=0)
                self._prefix[cache_key] = (ids[:n], self.lm.save_prefix(n))
                return self.lm.prefill(ids[n:], pos0=n), len(ids)
        return self.lm.prefill(ids, pos0=0), len(ids)

    def _sample(self, logits):
        """Logits over [EOT, audio0..audio2047] -> index (0 = EOT)."""
        p = torch.softmax(logits[0] / TEMPERATURE, -1)
        return int(torch.multinomial(p, 1))

    def _generate(self, ids, max_new, cache_key=None):
        """Yields audio token ids (without EOT)."""
        if len(ids) + max_new > self.lm.max_len:
            max_new = self.lm.max_len - len(ids)
            if max_new < 50:
                raise RuntimeError("prompt too long for the KV cache")
        logits, pos = self._prefill(ids, cache_key)
        for _ in range(max_new):
            i = self._sample(logits)
            if i == 0:
                return
            yield AUDIO0 + i - 1
            logits = self.lm.step(i, pos)
            pos += 1

    def _edit_ids(self, tokens, text, etype, info):
        if etype == "paralinguistic":
            plain = TAG_RE.sub("", text).strip()
            pre = (f"Add some non-verbal sounds to make the audio more natural, the new text is : "
                   f"{text}\n  The text corresponding to the audio is: {plain}\n")
        elif info == "remove":
            what = "emotion" if etype == "emotion" else "speaking styles"
            pre = f"Remove any {what} in the following audio and the reference text is: {text}\n"
        elif etype == "style":
            pre = (f"Make the following audio more {info} style. "
                   f"The text corresponding to the audio is: {text}\n")
        else:
            pre = f"Make the following audio more {info}. The text corresponding to the audio is: {text}\n"
        n = len(tokens) - len(tokens) % 5
        audio = "".join(f"<audio_{t - AUDIO0}>" for t in tokens[:n])
        return self._chat_ids(self.edit_sys, f"{pre}\n{audio}\n")

    # ------------------------------------------------------------ vocoder
    def _vocode_stream(self, token_iter, p):
        sid = uuid.uuid4().hex
        self._mel_carry = 0.0
        prompt_feat = p["feat"].to(torch.bfloat16)
        emb = p["emb"].to(torch.bfloat16)
        buf = []
        try:
            for t in token_iter:
                buf.append(t - AUDIO0)
                if len(buf) >= 5:
                    w = self.cosy.token2wav_stream(buf, p["prompt_token"], prompt_feat, emb,
                                                   sid, False)
                    buf = []
                    if w is not None and w.numel():
                        yield w.squeeze(0).numpy()
            w = self.cosy.token2wav_stream(buf, p["prompt_token"], prompt_feat, emb, sid, True)
            if w is not None and w.numel():
                yield w.squeeze(0).numpy()
        finally:
            self.cosy.clean_up(sid)

    # ------------------------------------------------------------ synthesis
    def synth(self, text, voice, lang, speed=1.0, instruct=""):
        lang = lang if lang in ("de", "en") else "en"
        if voice not in self._voices:
            voice = self.default_voices().get(lang, "")
        if voice not in self._voices:
            raise RuntimeError("no voice available")
        p = self._get_prompt(voice)
        edits = parse_instruct(instruct)
        self.speed = min(max(float(speed or 1.0), 0.5), 2.0)
        text = normalize(text, lang)
        sents = split_sentences(text)
        with torch.inference_mode():
            for s in sents:
                has_tags = bool(TAG_RE.search(s))
                if edits and any(e[0] == "paralinguistic" for e in edits) and not has_tags:
                    edits = [e for e in edits if e[0] != "paralinguistic"]
                base_text = TAG_RE.sub("", s).strip() if any(
                    e[0] == "paralinguistic" for e in edits) else s
                base_text = re.sub(r"\s{2,}", " ", base_text)
                max_new = int(TOK_PER_S * (4 + 0.14 * len(base_text)))
                gen = self._generate(self._clone_ids(voice, base_text), max_new, cache_key=voice)
                if not edits:
                    yield from self._vocode_stream(gen, p)
                    continue
                tokens = list(gen)
                for etype, info in edits:
                    t0 = time.perf_counter()
                    ids = self._edit_ids(tokens, s, etype, info)
                    new = list(self._generate(ids, int(len(tokens) * 1.8) + 200))
                    log(f"edit {etype}={info}: {len(tokens)} -> {len(new)} tokens "
                        f"in {time.perf_counter() - t0:.1f} s")
                    if len(new) > 25:
                        tokens = new
                yield from self._vocode_stream(iter(tokens), p)


if __name__ == "__main__":
    run(StepAudioBackend(), KEY)
