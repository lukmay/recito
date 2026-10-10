"""
tts_previews: precomputed voice previews for the setup assistant and the
settings window, so "Listen" plays at once instead of waiting for synthesis.

One short sample sentence per (engine, voice, language), in the language of
the voice (tts_langs.SAMPLE), stored as a 16-bit mono WAV in a per-user cache:

  TTS_PREVIEW_DIR                if set (tests, screenshots)
  %LOCALAPPDATA%\\tts-previews    on Windows
  $XDG_CACHE_HOME/tts-previews   elsewhere (default ~/.cache/tts-previews)

index.json in that folder lists every file with engine, voice, language,
speed and model. File names are derived from these fields and the sample
text, so a changed sample sentence never plays an old recording.

Nothing is shipped in the repository: audio of commercial voices must not
be redistributed, so every preview is rendered on the user's own machine.

How the cache fills:
  install.ps1 / install.sh   build --engine piper --engine edge, in the
                             background; offline it simply renders nothing
  neural/<key>/setup.*       build --engine <key> once after the install
  first live preview         the daemon saves what it rendered (Job in
                             piper-read-daemon.py, request flag "cache")

Model servers are never started to build previews while another model server
runs, so building cannot evict a model the daemon is using.

CLI:
  python tts_previews.py build [--engine KEY ...] [--lang CODE ...] [--force] [--log FILE]
                             (default languages: read_langs from voices.conf)
  python tts_previews.py list
  python tts_previews.py clear
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import tts_langs

VERSION = 1
INDEX = "index.json"

# Listen samples stay in the language of the voice, not of the interface.
SAMPLE = tts_langs.SAMPLE

# Cheap engines built by the installers. GPU models only via --engine.
DEFAULT_ENGINES = ("piper", "edge")
# The classic ONNX Kokoro is on its way out; "kokoro2" is the current Kokoro.
EXCLUDED = {"kokoro"}
# Voices that differ on every call (Fish picks a random speaker per sentence).
LIVE_ONLY_VOICES = {"random"}
# Stop rendering an engine after this many failures in a row without any
# success (offline, missing ffmpeg, broken model).
GIVE_UP_AFTER = 3

# Edge voices used until the online list has loaded once.
DEFAULT_EDGE = tts_langs.edge_defaults()
DEFAULT_EDGE["de"] += ["de-DE-SeraphinaMultilingualNeural"]
DEFAULT_EDGE["en"] += ["en-US-JennyNeural"]
EDGE_CACHE = Path.home() / ".cache/tts-edge-voices.json"
EDGE_RATE = 24000

_lock = threading.Lock()


class Skip(RuntimeError):
    """An engine cannot be built right now (not installed, GPU busy)."""


# --------------------------------------------------------------------------- #
#  Paths and keys
# --------------------------------------------------------------------------- #
def cache_dir() -> Path:
    env = os.environ.get("TTS_PREVIEW_DIR", "").strip()
    if env:
        return Path(env)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData/Local")
        return Path(base) / "tts-previews"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "tts-previews"


def sample(lang: str) -> str:
    return SAMPLE.get(lang, SAMPLE["en"])


def _scale(scale) -> float:
    try:
        return round(float(scale or 1.0), 2)
    except (TypeError, ValueError):
        return 1.0


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")[:48] or "voice"


def relpath(engine, voice, lang, scale=1.0, model="") -> str:
    """Path inside the cache folder, unique for these fields and the sample text."""
    raw = "\0".join([engine, voice, lang, f"{_scale(scale):.2f}", model or "", sample(lang)])
    h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"{_slug(engine)}/{lang}-{_slug(voice)}-{h}.wav"


def path_for(engine, voice, lang, scale=1.0, model="") -> Path:
    return cache_dir() / relpath(engine, voice, lang, scale, model)


def cacheable(engine, voice, lang, text=None, instruct="", pause_ms=None) -> bool:
    """Only the plain sample of a named voice is cached. Own voices (ref:)
    can be re-recorded under the same id, and custom texts, pauses or style
    instructions change the audio, so those always play live."""
    return bool(engine and voice and lang in SAMPLE
                and not str(voice).startswith("ref:")
                and voice not in LIVE_ONLY_VOICES
                and text in (None, "", sample(lang))
                and not (instruct or "").strip()
                and pause_ms is None)


def from_request(req: dict):
    """Listen request of the settings window -> lookup fields, or None if
    this request is not served from the cache."""
    eng, voice, lang = req.get("engine", ""), req.get("voice", ""), req.get("lang", "")
    if not cacheable(eng, voice, lang, req.get("text"), req.get("instruct") or "",
                     req.get("pauseMs")):
        return None
    return {"engine": eng, "voice": voice, "lang": lang, "scale": _scale(req.get("scale")),
            "model": req.get("model", "") if eng == "elevenlabs" else ""}


def lookup(engine, voice, lang, scale=1.0, model="") -> Path | None:
    p = path_for(engine, voice, lang, scale, model)
    try:
        return p if p.stat().st_size > 44 else None
    except OSError:
        return None


# --------------------------------------------------------------------------- #
#  Index and storage
# --------------------------------------------------------------------------- #
def read_index() -> dict:
    try:
        data = json.loads((cache_dir() / INDEX).read_text(encoding="utf-8"))
        if data.get("version") == VERSION and isinstance(data.get("items"), dict):
            return data
    except (OSError, ValueError, AttributeError):
        pass
    return {"version": VERSION, "items": {}}


def _write_index(data: dict):
    d = cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"{INDEX}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, d / INDEX)


def write_wav(path: Path, pcm: bytes, rate: int, channels: int = 1):
    """Atomic write: a reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm[: len(pcm) - len(pcm) % (2 * channels)])
    os.replace(tmp, path)


def store(engine, voice, lang, pcm: bytes, rate: int, channels=1, scale=1.0, model="") -> Path:
    """Save one rendered sample and record it in the index."""
    if not pcm:
        raise ValueError("empty audio")
    rel = relpath(engine, voice, lang, scale, model)
    p = cache_dir() / rel
    if channels == 1:
        try:                       # same loudness as live playback (tts_audio)
            import tts_audio
            pcm = tts_audio.normalize_pcm(pcm, rate)
        except Exception:
            pass
    write_wav(p, pcm, rate, channels)
    with _lock:
        idx = read_index()
        idx["items"][rel] = {"engine": engine, "voice": voice, "lang": lang,
                             "scale": _scale(scale), "model": model or "",
                             "seconds": round(len(pcm) / (2 * channels * rate), 2),
                             "created": time.strftime("%Y-%m-%d %H:%M:%S")}
        _write_index(idx)
    return p


def clear():
    d = cache_dir()
    for p in list(d.rglob("*.wav")) + [d / INDEX]:
        try:
            p.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
#  Voice lists
# --------------------------------------------------------------------------- #
def edge_voices(refresh: bool = False) -> dict:
    """Edge voice names {lang: [...]} for every reading language, from the
    cache or online. A cache from before a language was added lacks it; that
    language uses DEFAULT_EDGE until the next refresh."""
    data = {}
    if not refresh and EDGE_CACHE.exists():
        try:
            data = json.loads(EDGE_CACHE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            data = {}
    if not data:
        try:
            import asyncio

            import edge_tts
            voices = asyncio.run(edge_tts.list_voices())
            data = {lang: [] for lang in tts_langs.SUPPORTED}
            for v in voices:
                code = v.get("Locale", "")[:2]
                if code in data and v.get("Locale", "")[2:3] == "-":
                    data[code].append(v["ShortName"])
            data = {k: sorted(v) for k, v in data.items()}
            EDGE_CACHE.parent.mkdir(parents=True, exist_ok=True)
            EDGE_CACHE.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            data = DEFAULT_EDGE
    return {lang: data.get(lang) or DEFAULT_EDGE[lang] for lang in tts_langs.SUPPORTED}


def _piper_dir() -> Path:
    import tts_platform
    return tts_platform.piper_dir()


def configured_langs():
    """read_langs from voices.conf (de,en when unset or unreadable)."""
    try:
        import tts_platform
        return tts_langs.parse_read_langs(tts_platform.conf_read().get("read_langs"))
    except Exception:
        return list(tts_langs.DEFAULT_READ)


def voice_lists(engine, langs=None) -> dict:
    """{lang: [voice ids]} the pickers offer for an engine, for `langs`
    (default: every language)."""
    langs = list(langs or tts_langs.SUPPORTED)
    if engine == "piper":
        d = _piper_dir()
        return {lang: tts_langs.piper_installed(d, lang) for lang in langs}
    if engine == "edge":
        ev = edge_voices()
        return {lang: ev.get(lang, []) for lang in langs}
    import tts_neural
    if engine in tts_neural.ENGINES:
        spoken = [lang for lang in langs if lang in tts_neural.langs(engine)]
        lists = tts_neural.merge_voice_lists(tts_neural.voices_cache(engine), [], spoken)
        return {lang: [v for v in ids if not v.startswith("ref:")] for lang, ids in lists.items()}
    return {}


# --------------------------------------------------------------------------- #
#  Renderers: (voice, lang, text) -> (pcm s16le mono bytes, sample rate)
# --------------------------------------------------------------------------- #
_PIPER = {}


def render_piper(voice, lang, text):
    from piper import PiperVoice, SynthesisConfig
    if voice not in _PIPER:
        path = _piper_dir() / f"{voice}.onnx"
        if not path.exists():
            raise FileNotFoundError(path)
        _PIPER[voice] = PiperVoice.load(str(path), use_cuda=False)
    chunks = list(_PIPER[voice].synthesize(text, syn_config=SynthesisConfig(length_scale=1.0)))
    if not chunks:
        raise RuntimeError("Piper returned no audio")
    return b"".join(c.audio_int16_bytes for c in chunks), chunks[0].sample_rate


def render_edge(voice, lang, text):
    import asyncio

    import edge_tts

    async def fetch():
        mp3 = bytearray()
        async for part in edge_tts.Communicate(text, voice).stream():
            if part["type"] == "audio" and part["data"]:
                mp3 += part["data"]
        return bytes(mp3)

    mp3 = asyncio.run(fetch())
    if not mp3:
        raise RuntimeError("Edge returned no audio")
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-f", "s16le", "-ar", str(EDGE_RATE), "-ac", "1", "pipe:1"],
        input=mp3, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    return out.stdout, EDGE_RATE


def render_neural(engine):
    import tts_neural

    def render(voice, lang, text):
        parts, rate = [], 24000
        for pcm, sr in tts_neural.stream(engine, text, voice, lang, 1.0, ""):
            parts.append(pcm)
            rate = sr
        return b"".join(parts), rate
    return render


class _NeuralSession:
    """Runs a model server for the build and stops it again if it was
    started here. Refuses when another model server is running: starting
    would stop it, and that one belongs to the daemon."""

    def __init__(self, engine):
        self.engine = engine
        self.started = False

    def __enter__(self):
        import tts_neural
        L = tts_neural.launcher
        if L is None or not tts_neural.installed(self.engine):
            raise Skip(f"{self.engine} is not installed")
        busy = [k for k in L.running() if k != self.engine]
        if busy:
            raise Skip(f"{', '.join(busy)} is running; previews of {self.engine} "
                       "are saved on first listen instead")
        if not L.healthy(L.port_of(self.engine)):
            try:
                L.start(self.engine, wait=True)
            except Exception as exc:
                raise Skip(f"{self.engine} did not start: {exc}") from None
            self.started = True
        return self

    def __exit__(self, *exc):
        if self.started:
            import tts_neural
            tts_neural.launcher.stop(self.engine)
        return False


def _default_renderer(engine):
    if engine == "piper":
        return render_piper
    if engine == "edge":
        return render_edge
    import tts_neural
    if engine in tts_neural.ENGINES:
        return render_neural(engine)
    return None


class _Null:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# --------------------------------------------------------------------------- #
#  Build
# --------------------------------------------------------------------------- #
def build(engines=None, *, voices=None, renderers=None, force=False, log=print,
          langs=None) -> dict:
    """Render missing previews. voices ({engine: {lang: [ids]}}) and renderers
    ({engine: fn}) default to the real lists and engines; tests pass fakes.
    langs limits the real lists (default: read_langs from voices.conf).
    Returns {"rendered", "skipped", "failed"} counts; never raises for a
    single voice or an unavailable engine."""
    stats = {"rendered": 0, "skipped": 0, "failed": 0}
    voices, renderers = voices or {}, renderers or {}
    langs = langs or configured_langs()
    for eng in engines or DEFAULT_ENGINES:
        if eng in EXCLUDED:
            log(f"{eng}: no previews for this engine")
            continue
        render = renderers.get(eng) or _default_renderer(eng)
        if render is None:
            log(f"{eng}: unknown engine")
            continue
        session = _Null()
        if eng not in renderers and eng not in DEFAULT_ENGINES:
            session = _NeuralSession(eng)
        try:
            with session:
                _build_engine(eng, voices.get(eng) or voice_lists(eng, langs), render, force,
                              stats, log)
        except Skip as exc:
            log(f"{eng}: skipped ({exc})")
    return stats


def _build_engine(eng, lists, render, force, stats, log):
    done = misses = 0
    for lang, ids in lists.items():
        for vid in dict.fromkeys(ids):
            if not cacheable(eng, vid, lang):
                continue
            if not force and lookup(eng, vid, lang) is not None:
                stats["skipped"] += 1
                continue
            try:
                pcm, rate = render(vid, lang, sample(lang))
                store(eng, vid, lang, pcm, rate)
            except Exception as exc:
                stats["failed"] += 1
                misses += 1
                log(f"{eng} {lang} {vid}: {exc}")
                if not done and misses >= GIVE_UP_AFTER:
                    log(f"{eng}: giving up for now; missing previews are saved on first listen")
                    return
                continue
            stats["rendered"] += 1
            done += 1
            misses = 0
            log(f"{eng} {lang} {vid}: ok")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="tts_previews", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="render missing previews")
    b.add_argument("--engine", action="append", help="engine key (repeatable); default: piper, edge")
    b.add_argument("--lang", action="append", help="language code (repeatable); default: read_langs")
    b.add_argument("--force", action="store_true", help="render again even if cached")
    b.add_argument("--log", help="append messages to this file")
    sub.add_parser("list", help="show cached previews")
    sub.add_parser("clear", help="delete every cached preview")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        for rel, it in sorted(read_index()["items"].items()):
            mark = "" if (cache_dir() / rel).exists() else "  (missing)"
            print(f"{it['engine']:10} {it['lang']} {it['voice']}{mark}")
        print(cache_dir())
        return 0
    if args.cmd == "clear":
        clear()
        return 0

    log_file = args.log
    if sys.stdout is None:                               # pythonw: no console
        sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
        log_file = log_file or str(cache_dir() / "build.log")

    def log(msg):
        line = f"[tts-previews] {msg}"
        if log_file:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {line}\n")
        else:
            print(line, flush=True)

    stats = build(args.engine, force=args.force, log=log, langs=args.lang)
    log(f"done: {stats['rendered']} rendered, {stats['skipped']} already there, "
        f"{stats['failed']} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
