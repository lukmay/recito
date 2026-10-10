r"""
Quality check without listening: transcribes all WAVs of a bench run with
Whisper (faster-whisper, large-v3-turbo), writes the word error rate (WER)
against the reference text per sample into report.json, then rebuilds
gallery.html.

    python neural/asr_check.py <model> [<model> ...]

Runs in its own venv <data_dir>/_asr/venv and re-executes itself there when
needed. Inline tags ([laugh], (whisper), <tag>) are removed from the reference
text before comparing. Numbers often differ only in spelling (digits vs.
words), so treat the WER of the number samples with care; they are left out of
the mean.
"""
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from server_base import data_dir, leave_store_python  # noqa: E402

VENV_PY = data_dir() / "_asr" / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
NUMBER_IDS = ("numbers", "zahlen")    # sample ids containing these are number samples


def _reexec():
    try:
        import faster_whisper  # noqa: F401
        return
    except ImportError:
        if not VENV_PY.exists() or Path(sys.executable).resolve() == VENV_PY.resolve():
            sys.exit(f"faster-whisper missing; venv: {VENV_PY}")
        sys.exit(subprocess.call([str(VENV_PY), __file__, *sys.argv[1:]]))


def _cuda_dlls():
    """Make cuBLAS/cuDNN from the nvidia-* wheels findable for CTranslate2."""
    if os.name != "nt":
        return
    import site
    for sp in site.getsitepackages():
        for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin"):
            p = Path(sp) / sub
            if p.exists():
                os.add_dll_directory(str(p))
                os.environ["PATH"] = str(p) + os.pathsep + os.environ["PATH"]


TAG_RE = re.compile(r"\[[^\]]*\]|<[^>]*>|\((?:whisper|whispering|laugh|laughing|sigh|"
                    r"breath|breathing|excited|sad|angry|pause|soft|shouting)[^)]*\)", re.I)


def norm(s):
    s = TAG_RE.sub(" ", s)
    s = unicodedata.normalize("NFKC", s).lower()
    s = s.replace("-", " ")
    s = re.sub(r"[^\w\s]", " ", s)
    return s.split()


def wer(ref, hyp):
    r, h = norm(ref), norm(hyp)
    if not r:
        return 0.0
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)] / len(r)


def load_16k(path):
    """WAV -> float32 mono 16 kHz, without PyAV (recent PyAV versions do not
    work with faster-whisper)."""
    import wave
    import numpy as np
    from math import gcd
    from scipy.signal import resample_poly
    with wave.open(str(path), "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype={2: "<i2", 4: "<i4"}[sw]).astype(np.float32)
    a /= 32768.0 if sw == 2 else 2147483648.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != 16000:
        g = gcd(16000, sr)
        a = resample_poly(a, 16000 // g, sr // g).astype(np.float32)
    return a


def main():
    _reexec()
    _cuda_dlls()
    from faster_whisper import WhisperModel
    models = sys.argv[1:]
    if not models:
        sys.exit(__doc__)
    try:
        wm = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")
    except Exception as exc:
        print(f"CUDA not usable ({exc}) -> CPU", flush=True)
        wm = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")
    for m in models:
        rp = data_dir() / "samples" / m / "report.json"
        rep = json.loads(rp.read_text(encoding="utf-8"))
        vals = {"de": [], "en": []}
        for r in rep["results"]:
            if "wav" not in r:
                continue
            segs, _ = wm.transcribe(load_16k(rp.parent / r["wav"]), language=r["lang"],
                                    beam_size=5, vad_filter=False)
            hyp = " ".join(s.text.strip() for s in segs).strip()
            r["asr"] = hyp
            r["wer"] = round(wer(r["text"], hyp), 3)
            if r.get("group") == "common" and not any(n in r["id"] for n in NUMBER_IDS):
                vals.setdefault(r["lang"], []).append(r["wer"])
            flag = "  !!" if r["wer"] > 0.15 else ""
            print(f"  {m}/{r['id']:<22} WER {r['wer']:.2f}{flag}  | {hyp[:90]}", flush=True)
        rep["wer_mean"] = {k: round(sum(v) / len(v), 3) for k, v in vals.items() if v}
        rp.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{m}: mean WER (common samples without numbers) {rep['wer_mean']}",
              flush=True)
    import gallery
    gallery.build()


if __name__ == "__main__":
    leave_store_python()
    main()
