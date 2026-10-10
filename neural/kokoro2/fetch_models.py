r"""
Download the kokoro2 weights (pinned HF revisions) and convert Kerstin to the
Kokoro format once. Called by setup.ps1; idempotent, loads nothing onto the
GPU. server.py imports the voice tables and paths() from here.
"""
import os
import sys
from pathlib import Path

# Pinned Hugging Face revisions
REV = {
    "hexgrad/Kokoro-82M": "f3ff3571791e39611d31c381e3a41a3af07b4987",
    "Thorsten-Voice/Kokoro": "734e593d320a3d876bede7020f773dfd481a0cc7",
    "kikiri-tts/kikiri-german-martin": "1e9dcd16ed48fda0a7a1f62e5e37130a5fdf10d9",
    "kikiri-tts/kikiri-german-victoria": "ce81e200ff9203e1a3b042cd678c48e3ffb85cef",
    "cryptomilk/kokoro-german-kerstin": "1979d9ef12aff18b3c77cd19a52926ae85c661d3",
}

EN_VOICES = {
    # id: (pipeline 'a' = US, 'b' = UK; description)
    "af_heart": ("a", "US, weiblich, warm (beste offizielle Stimme)"),
    "af_bella": ("a", "US, weiblich, lebhaft"),
    "af_nicole": ("a", "US, weiblich, leise/nah (Kopfhörer)"),
    "af_aoede": ("a", "US, weiblich, ruhig"),
    "af_kore": ("a", "US, weiblich, klar"),
    "af_sarah": ("a", "US, weiblich, neutral"),
    "am_michael": ("a", "US, männlich, ruhig"),
    "am_fenrir": ("a", "US, männlich, tief"),
    "am_puck": ("a", "US, männlich, lebendig"),
    "bf_emma": ("b", "UK, weiblich, klar"),
    "bf_isabella": ("b", "UK, weiblich"),
    "bm_george": ("b", "UK, männlich, sonor"),
    "bm_fable": ("b", "UK, männlich, erzählend"),
}

# German voices: each one is its own fine-tuned 82M model plus voice pack.
DE_VOICES = {
    "thorsten": {"repo": "Thorsten-Voice/Kokoro", "model": "model.pth",
                 "voice": "voices/thorsten.pt",
                 "desc": "männlich, Thorsten-Voice (12k echte Aufnahmen, Epoche 5, 2026-07)"},
    "thorsten_ep10": {"repo": "Thorsten-Voice/Kokoro", "model": "model_ep10.pth",
                      "voice": "voices/thorsten_ep10.pt",
                      "desc": "männlich, Thorsten Epoche 10 (straffer, etwas schneller)"},
    "martin": {"repo": "kikiri-tts/kikiri-german-martin", "model": "kikiri_german_martin_ep10.pth",
               "voice": "voices/martin.pt",
               "desc": "männlich, kikiri Martin (= bisheriges kokoro-martin.onnx)"},
    "victoria": {"repo": "kikiri-tts/kikiri-german-victoria", "model": "kikiri_german_victoria_ep10.pth",
                 "voice": "voices/victoria.pt", "desc": "weiblich, kikiri Victoria"},
    "kerstin": {"repo": "cryptomilk/kokoro-german-kerstin",
                "convert": "data/kikiri_german_kerstin_stage2_ep10.pth",
                "voice": "voices/df_kerstin.pt",
                "desc": "weiblich, Kerstin (Open-Home-Datensatz, 2,3 h)"},
}


def data_root() -> Path:
    """Same root as server_base.data_dir(), plus "kokoro2"."""
    if os.environ.get("TTS_NEURAL_DIR"):
        base = os.environ["TTS_NEURAL_DIR"]
    elif os.name == "nt":
        base = os.path.join(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")),
                            "tts-neural")
    else:
        base = str(Path.home() / ".local/share/tts-neural")
    return Path(base) / "kokoro2"


def _dl(repo, fn, local_only):
    from huggingface_hub import hf_hub_download
    return hf_hub_download(repo, fn, revision=REV[repo], local_files_only=local_only)


def paths(local_only=True):
    """-> {"config": p, "en_model": p, "en_voice": {id: p}, "de": {id: (model, voice)}}"""
    out = {"config": _dl("hexgrad/Kokoro-82M", "config.json", local_only),
           "en_model": _dl("hexgrad/Kokoro-82M", "kokoro-v1_0.pth", local_only),
           "en_voice": {v: _dl("hexgrad/Kokoro-82M", f"voices/{v}.pt", local_only)
                        for v in EN_VOICES},
           "de": {}}
    for vid, s in DE_VOICES.items():
        if "convert" in s:
            model = data_root() / "models" / f"{vid}.pth"
            if not model.exists():
                if local_only:
                    raise FileNotFoundError(f"{model} missing, run setup.ps1")
                _convert(_dl(s["repo"], s["convert"], False), model)
        else:
            model = _dl(s["repo"], s["model"], local_only)
        out["de"][vid] = (str(model), _dl(s["repo"], s["voice"], local_only))
    return out


def _convert(ckpt_path, out_path: Path):
    """StyleTTS2 stage 2 checkpoint -> Kokoro KModel format (as in kikiri-tts
    scripts/test_inference.py: convert_checkpoint)."""
    import torch
    print(f"converting {ckpt_path} -> {out_path}", flush=True)
    net = torch.load(ckpt_path, map_location="cpu", weights_only=False)["net"]
    w = {k: {(n if n.startswith("module.") else "module." + n): t for n, t in net[k].items()}
         for k in ("bert", "bert_encoder", "predictor", "text_encoder", "decoder")}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp")
    torch.save(w, str(tmp))
    tmp.replace(out_path)


def main():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    p = paths(local_only=False)
    print("Kokoro v1.0:", p["en_model"])
    for vid, (m, _voice) in p["de"].items():
        print(f"de/{vid}: {m}")
    import torch  # noqa
    import kokoro  # noqa
    from misaki import de, en  # noqa
    g2p = de.DEG2P()
    print("DEG2P:", g2p("Über die Brücke, am 14.11.2026 um 9:30 Uhr.")[0])
    print("torch", torch.__version__, "cuda", torch.cuda.is_available())


if __name__ == "__main__":
    sys.exit(main())
