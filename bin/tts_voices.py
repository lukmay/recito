"""
tts_voices: create, check, list and delete reference voices for cloning.

A voice is a short, clean reference recording (10-15 s) stored as
<voices_dir>/<id>.wav, plus <id>.txt (transcript, for models that need one)
and <id>.json (name, language, source, date). Cloning models offer every file
there as voice "ref:<id>". voices_dir() follows TTS_NEURAL_DIR.

Sources: an audio file (anything ffmpeg reads), a microphone, or PC audio
(loopback of the output device). Recording uses the soundcard library;
playback happens in the settings window (Qt Multimedia).

Exception texts and the notes from check() are shown in the settings window.
"""

import json
import os
import re
import threading
import time
import unicodedata
import warnings
import wave
from pathlib import Path

import numpy as np

import tts_neural
import tts_platform
from tts_i18n import tr

SR = 24000
REC_SR = 48000
MAX_REF_S = 15.0
MAX_REC_S = 60.0
TMP_MAX_AGE_S = 86400
# Loopback capture gets no data during silence on Windows and soundcard warns
# for every block. Harmless: only silence is missing.
warnings.filterwarnings("ignore", message="data discontinuity")


def voices_dir() -> Path:
    if tts_neural.launcher is not None:
        return tts_neural.launcher.data_dir() / "voices"
    if os.environ.get("TTS_NEURAL_DIR"):
        return Path(os.environ["TTS_NEURAL_DIR"]) / "voices"
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    else:
        base = Path.home() / ".local/share"
    return base / "tts-neural" / "voices"


# --------------------------------------------------------------------------- #
#  Preparation and checks
# --------------------------------------------------------------------------- #
def decode_file(path) -> np.ndarray:
    """Any audio file -> float32 mono at SR (via ffmpeg). Without ffmpeg only
    16-bit PCM WAV files work."""
    import shutil
    if shutil.which("ffmpeg") is None:
        if str(path).lower().endswith(".wav"):
            x, sr = read_wav(path)
            return resample(x, sr)
        raise RuntimeError(tr("voices.err.noFfmpeg"))
    out = tts_platform.run_hidden(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1",
                                   "-ar", str(SR), "-f", "f32le", "pipe:1"], capture_output=True)
    if out.returncode != 0 or not out.stdout:
        raise RuntimeError(tr("voices.err.unreadable",
                              detail=out.stderr.decode("utf-8", "replace").strip()[:160]))
    return np.frombuffer(out.stdout, dtype=np.float32).copy()


def resample(x, sr_in, sr_out=SR):
    if sr_in == sr_out:
        return x.astype(np.float32)
    from math import gcd
    g = gcd(sr_in, sr_out)
    up, down = sr_out // g, sr_in // g
    try:
        from scipy.signal import resample_poly
        return resample_poly(x, up, down).astype(np.float32)
    except ImportError:                      # no scipy: linear interpolation
        n = int(len(x) * sr_out / sr_in)
        return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def _frame_db(x, hop):
    fr = x[: len(x) // hop * hop].reshape(-1, hop)
    return 20 * np.log10(np.sqrt((fr ** 2).mean(1) + 1e-12))


def prepare(x):
    """Trim silence at both ends, shorten pauses to 0.3 s, cut to MAX_REF_S
    (at the first pause after 10 s if there is one) and normalize to
    -20 dBFS RMS with a peak of at most 0.97."""
    if not len(x):
        raise RuntimeError(tr("voices.err.empty"))
    hop = int(0.02 * SR)
    db = _frame_db(x, hop)
    if not len(db):
        raise RuntimeError(tr("voices.err.tooShort"))
    thr = max(np.percentile(db, 95) - 35, -60)
    voiced = db > thr
    if not voiced.any():
        raise RuntimeError(tr("voices.err.silence"))
    keep, silent = [], 0
    for i, v in enumerate(voiced):
        silent = 0 if v else silent + 1
        if v or silent <= 15:                      # keep up to 0.3 s of pause
            keep.append(i)
    idx = np.array(keep)
    first, last = np.argmax(voiced), len(voiced) - np.argmax(voiced[::-1])
    idx = idx[(idx >= max(0, first - 5)) & (idx < min(len(voiced), last + 8))]
    y = np.concatenate([x[i * hop:(i + 1) * hop] for i in idx])
    if len(y) > MAX_REF_S * SR:
        cut = int(MAX_REF_S * SR)
        db2 = _frame_db(y[:cut], hop)
        quiet = np.where(db2[int(10 * SR / hop):] < thr)[0]
        if len(quiet):
            cut = (int(10 * SR / hop) + quiet[0]) * hop
        y = y[:cut]
    rms = np.sqrt((y ** 2).mean()) + 1e-9
    y = y * (10 ** (-20 / 20) / rms)
    peak = np.abs(y).max()
    if peak > 0.97:
        y *= 0.97 / peak
    return y.astype(np.float32)


def _decimal_comma(x):
    return f"{x:.1f}".replace(".", ",")


def check(raw, y):
    """Rate a recording as reference: raw = input, y = prepare(raw).
    -> (ok, [notes for the user])."""
    notes = []
    dur = len(y) / SR
    if dur < 5:
        notes.append(tr("voices.check.tooShort", s=_decimal_comma(dur)))
    elif dur < 8:
        notes.append(tr("voices.check.short", s=_decimal_comma(dur)))
    if len(raw) and np.mean(np.abs(raw) > 0.99) > 0.001:
        notes.append(tr("voices.check.clipped"))
    peak = float(np.abs(raw).max()) if len(raw) else 0.0
    if peak < 0.05:
        notes.append(tr("voices.check.quiet"))
    hop = int(0.02 * SR)
    db = _frame_db(y, hop)
    if len(db) > 10:
        snr = float(np.percentile(db, 95) - np.percentile(db, 10))
        if snr < 25:
            notes.append(tr("voices.check.noisy", db=f"{snr:.0f}"))
    ok = dur >= 5
    return ok, notes


# --------------------------------------------------------------------------- #
#  Recording
# --------------------------------------------------------------------------- #
def devices():
    """{'mic': [names], 'pc': [names]}; the default device comes first."""
    import soundcard as sc
    mic_def = sc.default_microphone().name
    spk_def = sc.default_speaker().name
    mics = [m.name for m in sc.all_microphones() if not m.isloopback]
    spk = [s.name for s in sc.all_speakers()]
    mics.sort(key=lambda n: n != mic_def)
    spk.sort(key=lambda n: n != spk_def)
    return {"mic": mics, "pc": spk}


class Recorder:
    """Records in a thread until stop() or MAX_REC_S. kind is 'mic' or 'pc'.
    level and seconds feed the UI meter."""

    def __init__(self, kind, device_name=""):
        self.kind, self.device_name = kind, device_name
        self.level, self.seconds, self.error = 0.0, 0.0, ""
        self._stop = threading.Event()
        self._blocks = []
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def _source(self, sc):
        if self.kind == "pc":
            name = self.device_name or sc.default_speaker().name
            return sc.get_microphone(id=str(name), include_loopback=True)
        if self.device_name:
            return sc.get_microphone(id=str(self.device_name))
        return sc.default_microphone()

    def _run(self):
        try:
            import soundcard as sc               # initializes COM itself
            src = self._source(sc)
            block = REC_SR // 10
            with src.recorder(samplerate=REC_SR, channels=1, blocksize=block) as rec:
                t0 = time.time()
                while not self._stop.is_set() and time.time() - t0 < MAX_REC_S:
                    data = rec.record(numframes=block)
                    mono = data.mean(axis=1) if data.ndim > 1 else data
                    self._blocks.append(mono.astype(np.float32))
                    self.level = float(min(1.0, np.sqrt((mono ** 2).mean()) * 6))
                    self.seconds = time.time() - t0
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"

    def stop(self):
        self._stop.set()
        self.thread.join(timeout=3)
        if not self._blocks:
            raise RuntimeError(self.error or tr("voices.err.nothing"))
        return resample(np.concatenate(self._blocks), REC_SR)


def tmp_dir() -> Path:
    """Folder for unsaved recordings. Leftovers older than a day (e.g. from a
    crash during the dialog) are deleted. The settings window names its files
    aufnahme_<ms>.wav."""
    d = voices_dir().parent / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    for f in d.glob("aufnahme_*.wav"):
        try:
            if time.time() - f.stat().st_mtime > TMP_MAX_AGE_S:
                f.unlink()
        except OSError:
            pass
    return d


# --------------------------------------------------------------------------- #
#  Save, list, delete
# --------------------------------------------------------------------------- #
def slug(name):
    """Voice id from a display name: ASCII, lowercase, underscores, max 40 chars."""
    s = unicodedata.normalize("NFKD", name.replace("ß", "ss")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return s[:40] or "voice"


def write_wav(path, y, sr=SR):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())


def read_wav(path):
    """16-bit PCM WAV -> (float32 mono, sample rate)."""
    with wave.open(str(path), "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise RuntimeError(tr("voices.err.wav16"))
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def save(name, lang, y, source):
    """Store a voice -> id. The transcript is written in the background (Whisper)."""
    d = voices_dir()
    d.mkdir(parents=True, exist_ok=True)
    vid = base = slug(name)
    n = 2
    while (d / f"{vid}.wav").exists():
        vid, n = f"{base}_{n}", n + 1
    write_wav(d / f"{vid}.wav", y)
    (d / f"{vid}.json").write_text(json.dumps(
        {"name": name.strip() or vid, "lang": lang, "source": source,
         "created": time.strftime("%Y-%m-%d %H:%M"), "seconds": round(len(y) / SR, 1)},
        ensure_ascii=False, indent=1), encoding="utf-8")
    threading.Thread(target=transcribe, args=(d / f"{vid}.wav", lang), daemon=True).start()
    return vid


def transcribe(wav, lang):
    """Write <id>.txt with Whisper (CPU, separate venv <data_dir>/_asr) if that
    venv exists. Failures are ignored: Chatterbox needs no transcript; Fish
    and Step-Audio skip voices without one."""
    if tts_neural.launcher is None:
        return
    py = tts_neural.launcher.data_dir() / "_asr" / "venv" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not py.exists():
        return
    # Read the WAV here; faster-whisper would otherwise use PyAV, whose recent
    # versions are incompatible with it.
    code = ("import sys, wave, numpy as np\n"
            "from scipy.signal import resample_poly\n"
            "from faster_whisper import WhisperModel\n"
            "w = wave.open(sys.argv[1]); sr = w.getframerate()\n"
            "x = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').astype(np.float32) / 32768\n"
            "x = resample_poly(x, 2, 3).astype(np.float32) if sr == 24000 else x\n"
            "m = WhisperModel('large-v3-turbo', device='cpu', compute_type='int8', cpu_threads=4)\n"
            "segs, _ = m.transcribe(x, language=sys.argv[2], beam_size=5)\n"
            "open(sys.argv[3], 'w', encoding='utf-8').write(' '.join(s.text.strip() for s in segs))\n")
    try:
        tts_platform.run_hidden([str(py), "-c", code, str(wav), lang,
                                 str(Path(wav).with_suffix(".txt"))],
                                timeout=600, capture_output=True)
    except Exception:
        pass


def _guess_lang(stem):
    s = stem.lower()
    if s.startswith(("en_", "cast_hifi", "cast_expresso")):
        return "en"
    return "de"


def list_voices():
    """[{id, name, lang, seconds, source, created, own}] of all reference
    voices. own = created in the app (has a .json file)."""
    d = voices_dir()
    out = []
    for wav in sorted(d.glob("*.wav")) if d.exists() else []:
        meta = {}
        mj = wav.with_suffix(".json")
        if mj.exists():
            try:
                meta = json.loads(mj.read_text(encoding="utf-8"))
            except ValueError:
                pass
        try:
            with wave.open(str(wav)) as w:
                secs = w.getnframes() / w.getframerate()
        except Exception:
            secs = 0
        out.append({"id": wav.stem, "name": meta.get("name", wav.stem),
                    "lang": meta.get("lang") or _guess_lang(wav.stem),
                    "seconds": round(secs, 1), "source": meta.get("source", ""),
                    "created": meta.get("created", ""), "own": bool(meta)})
    return out


def delete(vid):
    """Delete the files of a voice. Returns the voices.conf keys that pointed
    to it; the caller resets them."""
    d = voices_dir()
    for ext in (".wav", ".txt", ".json"):
        p = d / f"{vid}{ext}"
        if p.exists():
            p.unlink()
    import tts_platform
    return [k for k, v in tts_platform.conf_read().items() if v == f"ref:{vid}"]
