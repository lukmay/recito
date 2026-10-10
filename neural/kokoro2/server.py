r"""
Kokoro TTS server: PyTorch on CUDA, streamed sentence by sentence, German and
English. Protocol: ../server_base.py.

English: the official hexgrad/Kokoro-82M v1.0 (13 voices, US 'a' and UK 'b').
German: German Kokoro fine-tunes, each voice its own 82M model (Thorsten-Voice,
    kikiri Martin/Victoria, Kerstin). G2P through the misaki fork with German
    text normalization (numbers, dates, times, currencies).

Pronunciation markup (German and English): [word](/IPA/) replaces the
pronunciation of a word, e.g. "[Kokoro](/kˈOkəɹO/)".

Environment variables:
  KOKORO2_MAX_DE   max. German models loaded at the same time (default 2, LRU)
  KOKORO2_PRELOAD  "all" loads every German model at startup
"""
import os
import re
import sys
import time
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from server_base import Backend, log_fn, run  # noqa: E402
import fetch_models as fm  # noqa: E402

KEY = "kokoro2"
log = log_fn(KEY)

DEFAULT_DE = "thorsten"
DEFAULT_EN = "af_heart"
SR = 24000
PAUSE_S = 0.12            # pause between sentences of one request
MAX_PS = 480              # phonemes per inference (context 512 including margin)

MARKUP_RE = re.compile(r"\[([^\]]+)\]\(/([^/]+)/\)")
SENT_RE = re.compile(r"(?<=[.!?…])[\"'»«“”)]*\s+|\n+")
CLAUSE_RE = re.compile(r"(?<=[,;:—–])\s+")
# espeak reads German compounds with an English last part
# ("Echtzeitsprachverarbeitungssoftware") entirely as English. A hyphen before
# the last part keeps the German part German and only the last part English.
EN_TAIL_RE = re.compile(
    r"\b([A-Za-zÄÖÜäöüß]{4,}?)(software|hardware|dashboard|server|interface|framework|"
    r"workflow|deployment|cluster|browser|plugin|account|feedback|meeting|update|"
    r"upload|download|repository|backend|frontend|support)\b", re.I)


def fix_compounds(text):
    return EN_TAIL_RE.sub(lambda m: f"{m.group(1)}-{m.group(2).capitalize()}", text)


def split_text(text, limit=220):
    """Sentences; overlong sentences split at punctuation, else at spaces."""
    out = []
    for sent in SENT_RE.split(text):
        sent = sent.strip()
        if not sent:
            continue
        if len(sent) <= limit:
            out.append(sent)
            continue
        cur = ""
        for part in CLAUSE_RE.split(sent):
            if cur and len(cur) + 1 + len(part) > limit:
                out.append(cur)
                cur = part
            else:
                cur = f"{cur} {part}".strip()
        while len(cur) > limit:
            cut = cur.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            out.append(cur[:cut])
            cur = cur[cut:].strip()
        if cur:
            out.append(cur)
    return out


def split_ps(ps):
    """Split a phoneme string longer than MAX_PS at spaces (last resort)."""
    out = []
    while len(ps) > MAX_PS:
        cut = ps.rfind(" ", 0, MAX_PS)
        cut = cut if cut > 0 else MAX_PS
        out.append(ps[:cut].strip())
        ps = ps[cut:].strip()
    if ps:
        out.append(ps)
    return out


class Kokoro2(Backend):
    name = "kokoro2"
    sample_rate = SR
    tags = ["[Wort](/IPA/)"]
    instruct = False

    def load(self):
        import torch
        from kokoro import KModel, KPipeline
        self.torch = torch
        self.KModel = KModel
        self.dev = "cuda" if torch.cuda.is_available() else "cpu"
        torch.backends.cudnn.benchmark = False      # variable lengths
        self.p = fm.paths(local_only=True)
        self.max_de = max(1, int(os.environ.get("KOKORO2_MAX_DE", "2")))

        t0 = time.perf_counter()
        self.en_model = KModel(repo_id="hexgrad/Kokoro-82M", config=self.p["config"],
                               model=self.p["en_model"]).to(self.dev).eval()
        self.g2p = {
            "a": KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M", model=False),
            "b": KPipeline(lang_code="b", repo_id="hexgrad/Kokoro-82M", model=False),
            "d": KPipeline(lang_code="d", repo_id="hexgrad/Kokoro-82M", model=False).g2p,
        }
        self.packs = {}
        for v, path in self.p["en_voice"].items():
            self.packs[v] = torch.load(path, map_location="cpu", weights_only=True).to(self.dev)
        for v, (_, vpath) in self.p["de"].items():
            self.packs[v] = torch.load(vpath, map_location="cpu", weights_only=True).to(self.dev)
        self.de_models = OrderedDict()
        preload = list(self.p["de"]) if os.environ.get("KOKORO2_PRELOAD") == "all" \
            else [DEFAULT_DE]
        for v in preload:
            self._de_model(v)
        # Warm-up: the first CUDA kernel calls are slow.
        for txt, lang, v in (("Hello there, this is a warm up.", "en", DEFAULT_EN),
                             ("Hallo, das ist ein Aufwärmen.", "de", DEFAULT_DE)):
            for _ in range(2):
                for _a in self.synth(txt, v, lang):
                    pass
        if self.dev == "cuda":
            torch.cuda.synchronize()
            log(f"loaded in {time.perf_counter() - t0:.1f} s, VRAM allocated "
                f"{torch.cuda.memory_allocated() / 2**20:.0f} MB, reserved "
                f"{torch.cuda.memory_reserved() / 2**20:.0f} MB")

    # -- models --------------------------------------------------------------
    def _de_model(self, vid):
        if vid in self.de_models:
            self.de_models.move_to_end(vid)
            return self.de_models[vid]
        while len(self.de_models) >= self.max_de:
            old, m = self.de_models.popitem(last=False)
            del m
            import gc
            gc.collect()                       # break cycles before emptying the cache
            self.torch.cuda.empty_cache()
            log(f"unloading German model {old}")
        t0 = time.perf_counter()
        mpath, _ = self.p["de"][vid]
        m = self.KModel(repo_id="hexgrad/Kokoro-82M", config=self.p["config"],
                        model=mpath).to(self.dev).eval()
        self.de_models[vid] = m
        log(f"German model {vid} loaded in {time.perf_counter() - t0:.1f} s")
        return m

    def voices(self):
        out = [{"id": v, "lang": "en", "desc": d} for v, (_, d) in fm.EN_VOICES.items()]
        out += [{"id": v, "lang": "de", "desc": s["desc"]} for v, s in fm.DE_VOICES.items()]
        return out

    def default_voices(self):
        return {"de": DEFAULT_DE, "en": DEFAULT_EN}

    # -- G2P -----------------------------------------------------------------
    def _phonemize_de(self, text):
        text = fix_compounds(text)
        parts, pos = [], 0
        for m in MARKUP_RE.finditer(text):
            if text[pos:m.start()].strip():
                parts.append(self.g2p["d"](text[pos:m.start()])[0])
            parts.append(m.group(2))
            pos = m.end()
        if text[pos:].strip():
            parts.append(self.g2p["d"](text[pos:])[0])
        ps = " ".join(p.strip() for p in parts if p and p.strip())
        # misaki.de can emit 'ʏ' (short ü), which is not in the Kokoro vocabulary
        return [ps.replace("ʏ", "y")]

    def _phonemize_en(self, text, code):
        pipe = self.g2p[code]
        _, tokens = pipe.g2p(text)
        return [ps for _gs, ps, _tk in pipe.en_tokenize(tokens) if ps]

    # -- synthesis -----------------------------------------------------------
    def synth(self, text, voice, lang, speed=1.0, instruct=""):
        torch = self.torch
        lang = "de" if str(lang).lower().startswith("de") else "en"
        if lang == "de":
            voice = voice if voice in fm.DE_VOICES else DEFAULT_DE
            model = self._de_model(voice)
        else:
            voice = voice if voice in fm.EN_VOICES else DEFAULT_EN
            model = self.en_model
        pack = self.packs[voice]
        speed = min(2.0, max(0.5, float(speed or 1.0)))
        pause = torch.zeros(int(PAUSE_S * SR / speed))
        first = True
        for chunk in split_text(text):
            if lang == "de":
                pss = self._phonemize_de(chunk)
            else:
                pss = self._phonemize_en(chunk, fm.EN_VOICES[voice][0])
            for ps in (p for raw in pss for p in split_ps(raw)):
                with torch.inference_mode():
                    audio = model(ps, pack[len(ps) - 1], speed)
                if audio is None or audio.numel() == 0:
                    continue
                if not first:
                    yield pause
                first = False
                yield audio


if __name__ == "__main__":
    run(Kokoro2(), KEY)
