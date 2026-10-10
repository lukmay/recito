r"""
Generates the shared synthetic reference voices for cloning
(<data_dir>/voices/<id>.wav + <id>.txt) with Qwen3-TTS VoiceDesign.

    python neural/gpulock.py <venv-python> neural/qwen3/make_ref_voices.py [--cands 4] [--only de_male_1]

Several candidates per voice (sampling), each checked with Whisper; the best
one (lowest WER, duration 9 to 16 s) is kept. All candidates stay in
voices/_candidates/ for listening.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server_base import voices_dir  # noqa: E402

VOICES = {
    "de_female_1": {
        "lang": "German",
        "text": "Willkommen zu dieser kurzen Einführung. Heute schauen wir uns gemeinsam an, "
                "wie ein Computer Sprache versteht, und warum dabei Wahrscheinlichkeiten "
                "eine so große Rolle spielen.",
        "design": "A warm, calm female voice in her early thirties, a native German speaker "
                  "with clear standard High German pronunciation and no accent. She reads at a "
                  "relaxed, steady pace like a professional audiobook narrator, friendly and "
                  "natural, medium pitch. Dry, close-miked studio recording without reverb or "
                  "background noise.",
    },
    "de_male_1": {
        "lang": "German",
        "text": "Am Anfang steht immer eine einfache Frage. Was wissen wir bereits, und was "
                "müssen wir noch herausfinden? Mit dieser Haltung lässt sich fast jedes "
                "Problem Schritt für Schritt lösen.",
        "design": "A calm, pleasant male voice in his late thirties, a native German speaker "
                  "with clear standard High German pronunciation and no accent. Medium-low, "
                  "warm timbre, reading at a relaxed, steady pace like a professional "
                  "audiobook narrator, natural and unhurried. Dry, close-miked studio "
                  "recording without reverb or background noise.",
    },
    "en_female_1": {
        "lang": "English",
        "text": "Welcome to this short introduction. Today we will look at how a computer "
                "learns to understand language, and why probabilities play such an "
                "important role along the way.",
        "design": "A warm, calm female voice in her early thirties with a neutral American "
                  "accent. She reads at a relaxed, steady pace like a professional audiobook "
                  "narrator, clear articulation, friendly and natural, medium pitch. Dry, "
                  "close-miked studio recording without reverb or background noise.",
    },
    "en_male_1": {
        "lang": "English",
        "text": "Every good explanation starts with a simple question. What do we already "
                "know, and what do we still need to find out? With that mindset, almost any "
                "problem can be solved step by step.",
        "design": "A calm, pleasant male voice in his late thirties with a neutral American "
                  "accent. Medium-low, warm timbre, clear articulation, reading at a relaxed, "
                  "steady pace like a professional audiobook narrator, natural and unhurried. "
                  "Dry, close-miked studio recording without reverb or background noise.",
    },
}


def norm_words(s):
    s = s.lower()
    s = re.sub(r"[^\wäöüß ]+", " ", s)
    return s.split()


def wer(ref, hyp):
    r, h = norm_words(ref), norm_words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)] / max(len(r), 1)


def trim_and_normalize(wav, sr):
    a = wav.astype(np.float32)
    thr = 10 ** (-45 / 20) * max(np.abs(a).max(), 1e-6)
    idx = np.where(np.abs(a) > thr)[0]
    if len(idx):
        pad = int(0.15 * sr)
        a = a[max(0, idx[0] - pad): idx[-1] + pad]
    a = a / max(np.abs(a).max(), 1e-6) * 10 ** (-1.5 / 20)   # Peak -1.5 dBFS
    fade = int(0.01 * sr)
    a[:fade] *= np.linspace(0, 1, fade)
    a[-fade:] *= np.linspace(1, 0, fade)
    return a


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cands", type=int, default=4)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    only = {s for s in a.only.split(",") if s}

    from qwen_tts import Qwen3TTSModel
    import os
    os.add_dll_directory(str(Path(torch.__file__).parent / "lib"))  # cuBLAS/cuDNN for CTranslate2
    from faster_whisper import WhisperModel

    out = voices_dir()
    cand_dir = out / "_candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)

    from huggingface_hub import snapshot_download
    model = Qwen3TTSModel.from_pretrained(
        snapshot_download("Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign", local_files_only=True), device_map="cuda:0",
        dtype=torch.bfloat16, attn_implementation="sdpa")
    asr = WhisperModel("mobiuslabsgmbh/faster-whisper-large-v3-turbo", device="cuda",
                       compute_type="float16")

    summary = {}
    for vid, spec in VOICES.items():
        if only and vid not in only:
            continue
        best = None
        for c in range(a.cands):
            torch.manual_seed(1000 + c)
            wavs, sr = model.generate_voice_design(
                text=spec["text"], language=spec["lang"], instruct=spec["design"])
            w = trim_and_normalize(wavs[0], sr)
            dur = len(w) / sr
            import librosa
            w16 = librosa.resample(w, orig_sr=sr, target_sr=16000)
            code = {"German": "de", "English": "en"}[spec["lang"]]
            segs, _ = asr.transcribe(w16, language=code, beam_size=5)
            hyp = " ".join(s.text.strip() for s in segs)
            e = wer(spec["text"], hyp)
            p = cand_dir / f"{vid}_c{c}.wav"
            sf.write(p, w, sr, subtype="PCM_16")
            score = e + (0 if 9 <= dur <= 16 else 1)
            print(f"{vid} c{c}: {dur:.1f} s  WER {e:.2%}  | {hyp}", flush=True)
            if best is None or score < best[0]:
                best = (score, c, w, sr, e, dur, hyp)
        _, c, w, sr, e, dur, hyp = best
        if sr != 24000:
            import librosa
            w = librosa.resample(w, orig_sr=sr, target_sr=24000)
            sr = 24000
        sf.write(out / f"{vid}.wav", w, sr, subtype="PCM_16")
        (out / f"{vid}.txt").write_text(spec["text"], encoding="utf-8")
        summary[vid] = {"candidate": c, "wer": round(e, 4), "dur_s": round(dur, 2),
                        "asr": hyp, "design_prompt": spec["design"]}
        print(f"==> {vid}: candidate {c}, WER {e:.2%}, {dur:.1f} s", flush=True)
    prev = {}
    sp = out / "_qwen3_selection.json"
    if sp.exists():
        prev = json.loads(sp.read_text(encoding="utf-8"))
    prev.update(summary)
    sp.write_text(json.dumps(prev, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
