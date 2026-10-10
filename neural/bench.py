r"""
Benchmark and sample generator for the local neural TTS servers.

    python neural/bench.py <model> [--keep] [--only id1,id2] [--voice v] [--no-start]

Starts the model server (from its venv, unless it is already running),
synthesizes all common test texts (samples_common.json) and the model-specific
samples (neural/<model>/samples.json, with and without tags), measures time to
first audio (TTFA), total time and real-time factor (RTF), and writes WAVs plus
report.json to <data_dir>/samples/<model>/. Then rebuilds gallery.html.

Each sample is synthesized twice; the second (warm) run is measured, the first
counts as "cold". Standard library only, runs with any Python.
"""
import argparse
import json
import sys
import time
import urllib.request
import wave
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from server_base import data_dir, leave_store_python  # noqa: E402
import launcher  # noqa: E402


def synth(port, payload, timeout=600):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/synth", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.perf_counter()
    ttfa = None
    buf = bytearray()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        sr = int(r.headers.get("X-Sample-Rate") or 24000)
        while True:
            b = r.read(4096)
            if not b:
                break
            if ttfa is None:
                ttfa = time.perf_counter() - t0
            buf += b
    total = time.perf_counter() - t0
    return bytes(buf), sr, ttfa or total, total


def load_samples(model):
    items = json.loads((HERE / "samples_common.json").read_text(encoding="utf-8"))["samples"]
    for it in items:
        it.setdefault("group", "common")
    spec = HERE / launcher.REGISTRY[model]["dir"] / "samples.json"
    if spec.exists():
        for it in json.loads(spec.read_text(encoding="utf-8"))["samples"]:
            it.setdefault("group", "model")
            items.append(it)
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--keep", action="store_true", help="keep the server running afterwards")
    ap.add_argument("--only", default="")
    ap.add_argument("--voice", default="", help="force this voice for all samples")
    ap.add_argument("--no-start", action="store_true")
    a = ap.parse_args()

    port = launcher.port_of(a.model)
    started_here = False
    if not launcher.healthy(port):
        if a.no_start:
            sys.exit(f"server {a.model} is not running")
        t0 = time.perf_counter()
        launcher.start(a.model, wait=True)
        started_here = True
        print(f"server started in {time.perf_counter() - t0:.1f} s", flush=True)
        port = launcher.port_of(a.model)
    meta = json.loads(urllib.request.urlopen(
        f"http://127.0.0.1:{port}/voices", timeout=30).read())
    defaults = meta.get("default", {})

    out = data_dir() / "samples" / a.model
    out.mkdir(parents=True, exist_ok=True)
    only = {s for s in a.only.split(",") if s}
    results = []
    for it in load_samples(a.model):
        if only and it["id"] not in only:
            continue
        voice = a.voice or it.get("voice") or defaults.get(it["lang"], "")
        payload = {"text": it["text"], "voice": voice, "lang": it["lang"],
                   "speed": it.get("speed", 1.0), "instruct": it.get("instruct", "")}
        try:
            _, _, cold_ttfa, _ = synth(port, payload)
            pcm, sr, ttfa, total = synth(port, payload)
        except Exception as exc:
            print(f"  {it['id']:<22} ERROR {exc}", flush=True)
            results.append({**it, "voice": voice, "error": str(exc)})
            continue
        dur = len(pcm) / 2 / sr
        wav = out / f"{it['id']}.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(pcm)
        r = {**it, "voice": voice, "wav": wav.name, "ttfa_ms": round(ttfa * 1000),
             "cold_ttfa_ms": round(cold_ttfa * 1000), "total_s": round(total, 2),
             "audio_s": round(dur, 2), "rtf": round(total / max(dur, 1e-6), 2)}
        results.append(r)
        print(f"  {it['id']:<22} TTFA {r['ttfa_ms']:>5} ms  (cold {r['cold_ttfa_ms']:>5})"
              f"  {dur:5.1f} s audio  RTF {r['rtf']:.2f}", flush=True)

    vram = launcher.vram_used_mb()
    (out / "report.json").write_text(json.dumps(
        {"model": a.model, "title": launcher.REGISTRY[a.model]["title"],
         "meta": meta, "vram_total_used_mb": vram, "results": results,
         "time": time.strftime("%Y-%m-%d %H:%M")},
        ensure_ascii=False, indent=1), encoding="utf-8")
    import gallery
    gallery.build()
    print(f"-> {out}  (total VRAM in use: {vram} MB)", flush=True)
    if started_here and not a.keep:
        launcher.stop(a.model)


if __name__ == "__main__":
    leave_store_python()
    main()
