#!/usr/bin/env python3
"""
piper-read daemon: keeps the voices warm and reads text aloud that arrives
over IPC (Unix socket on Linux, TCP on localhost on Windows; see tts_platform).
Text is split into sentences and streamed sentence by sentence, so the first
sentence plays while the rest is still being synthesized.

IPC protocol (one message per connection, UTF-8):
  {"text": "...", "voice": "de", "length_scale": 1.0, "pause_ms": 0}
                         read aloud; cancels the current job
  voice "auto"           detect the language (lingua, restricted to read_langs)
                         and pick the voice of that language
  {"cmd": "test", "engine": ..., "voice": ..., "key": "<lang>", "model": ...,
   "instruct": ..., "cache": true}
                         preview one engine/voice, no fallback; with "cache"
                         a complete render is kept in tts_previews
  {"cmd": "pause"}       toggle pause
  {"cmd": "stop"}        or the raw text __STOP__: stop the current job
  {"cmd": "quit"}        exit (the tray restarts the daemon)
  {"cmd": "status"}      reply {"state": "idle"|"speaking"|"paused"} on the same
                         connection (after the client's write shutdown)
  raw text (not JSON)    read with the default voice

Configuration: voices.conf in tts_platform.piper_dir() (see config/voices.conf).
Engines: piper (always loaded, the fallback), edge, sapi (Windows),
mssp (Linux, balcon under Wine), elevenlabs, and the neural servers from
neural/models.json. If the chosen engine fails for a sentence, Piper reads it
(unless fallback=off). A language the engine cannot speak goes to the
language fallback of tts_langs.speak_route (Piper voice of the language, then
Edge's default voice of the language, then the default Piper voice).
"""

import asyncio
import json
import math
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Callable

# Under pythonw with a redirected log, stdout gets the locale encoding
# (cp1252 on Windows) and a print() of e.g. U+2011 in the text would raise
# and kill the daemon. Force UTF-8 and replace what cannot be encoded.
for _stream in (sys.stdout, sys.stderr):
    try:
        if _stream is not None:
            _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from piper import PiperVoice, SynthesisConfig  # noqa: E402

import tts_audio  # noqa: E402
import tts_config  # noqa: E402
import tts_elevenlabs as eleven  # noqa: E402
import tts_langs  # noqa: E402
import tts_neural  # noqa: E402
import tts_platform  # noqa: E402
import tts_previews  # noqa: E402

PIPER_DIR = tts_platform.piper_dir()
CONF_PATH = PIPER_DIR / "voices.conf"
# Mirror the paths into the environment so tts_rules (rules.json, own code
# rules in tts_filters.py) uses the same folder on every platform.
os.environ["PIPER_DIR"] = str(PIPER_DIR)

PCM_STEP = 8192                 # bytes per chunk for engines that return a whole sentence


def log(msg):
    print(f"[piper-read] {msg}", flush=True)


def load_config():
    values, note = tts_config.migrate(tts_config.read(CONF_PATH), tts_neural.installed)
    if note:
        log(note)
    cfg = tts_config.daemon_config(values, tts_neural.ENGINES)
    # Reading languages without a configured Piper voice use an installed one
    # of that language (for example after a download in the settings).
    for lang in cfg.read_langs:
        if lang not in cfg.piper_voices:
            pick = tts_langs.piper_pick(PIPER_DIR, lang)
            if pick:
                cfg.piper_voices[lang] = pick
    if not cfg.piper_voices:
        # No Piper voice configured: take any .onnx in the folder.
        for onnx in sorted(PIPER_DIR.glob("*.onnx")):
            cfg.piper_voices[tts_langs.norm(onnx.stem[:2]) or "de"] = onnx.stem
            break
    return cfg


log(f"voice folder: {PIPER_DIR}")
CFG = load_config()
ENGINE = CFG.engine
VOICE_MAP = CFG.piper_voices
EDGE_MAP = CFG.voices("edge")
SAPI_MAP = CFG.voices("sapi")
MSSP_MAP = CFG.voices("mssp")
MSSP_CMD = CFG.mssp_cmd
PAUSE_SENTENCE_MS = CFG.pause_ms
LANG_DETECT = CFG.lang_detect
DEVICE = CFG.device
PREBUFFER_MS = CFG.prebuffer_ms
FALLBACK = CFG.fallback
READ_LANGS = CFG.read_langs

VOICES = {}        # key -> PiperVoice (warm)

# --- Output processing (tts_audio) -------------------------------------------
# loudness_norm=on|off and loudness_target=<LUFS> in voices.conf. Speed that an
# engine cannot render natively is applied as a pitch-preserving time stretch.
LOUDNESS = None
if CFG.values.get("loudness_norm", "on").lower() not in ("off", "0", "aus", "no", "false"):
    try:
        _target = float(CFG.values.get("loudness_target") or tts_audio.TARGET_LUFS)
    except ValueError:
        _target = tts_audio.TARGET_LUFS
    LOUDNESS = tts_audio.Leveler(_target, path=tts_audio.state_path())
    log(f"loudness normalization: {_target:g} LUFS")
else:
    log("loudness normalization off")


class _Chunk:
    """Audio piece with the same attributes as Piper's AudioChunk, so the
    playback loop treats every engine alike."""
    __slots__ = ("audio_int16_bytes", "sample_rate", "sample_channels")

    def __init__(self, audio: bytes, rate: int, channels: int):
        self.audio_int16_bytes = audio
        self.sample_rate = rate
        self.sample_channels = channels


def _pcm_chunks(pcm, rate, channels):
    return [_Chunk(pcm[i:i + PCM_STEP], rate, channels) for i in range(0, len(pcm), PCM_STEP)]


# --- Compute device (Piper's onnxruntime) ------------------------------------
# device=gpu runs Piper on CUDA when onnxruntime has the CUDA provider
# (onnxruntime-gpu), else on the CPU. CUDA/cuDNN come from the nvidia-* pip
# wheels via ort.preload_dlls(), no system CUDA needed.
PIPER_USE_CUDA = False
if DEVICE == "gpu":
    try:
        import onnxruntime as _ort
        if hasattr(_ort, "preload_dlls"):
            try:
                _ort.preload_dlls()
            except Exception as _e:
                log(f"preload_dlls warning: {_e}")
        PIPER_USE_CUDA = "CUDAExecutionProvider" in _ort.get_available_providers()
        log("device: GPU (CUDA)" if PIPER_USE_CUDA
            else "device: GPU requested, but onnxruntime has no CUDA provider -> CPU")
    except Exception as exc:
        log(f"GPU init failed ({exc}) -> CPU")
else:
    log("device: CPU")


def _piper_load_safe(path):
    """Load a Piper voice; on CUDA validate it with a tiny synthesis, else
    CPU. (Piper supports CUDA only, not DirectML.)"""
    if PIPER_USE_CUDA:
        try:
            v = PiperVoice.load(str(path), use_cuda=True)
            for _ in v.synthesize("Test."):
                break
            return v
        except Exception as exc:
            log(f"Piper GPU not usable ({exc}) -> CPU")
    return PiperVoice.load(str(path), use_cuda=False)


for key, base in VOICE_MAP.items():
    path = PIPER_DIR / f"{base}.onnx"
    if not path.exists():
        log(f"WARNING: voice missing: {path}")
        continue
    log(f"loading '{key}' <- {path.name}")
    voice = _piper_load_safe(path)
    try:  # the first inference is slow (ORT JIT)
        for _ in voice.synthesize("Bereit."):
            break
    except Exception as exc:
        log(f"warmup warning ({key}): {exc}")
    VOICES[key] = voice

if not VOICES:
    raise SystemExit("[piper-read] No voice loaded, check voices.conf.")

# Default voice key: the first reading language with a Piper voice.
DEFAULT_KEY = next((k for k in READ_LANGS if k in VOICES), next(iter(VOICES)))
log(f"reading languages: {', '.join(READ_LANGS)} (default {DEFAULT_KEY})")


# --- Edge-TTS (online, Microsoft neural voices) ----------------------------
# Edge returns MP3; ffmpeg decodes it to 24 kHz mono PCM for the same sink.
EDGE_RATE = 24000
EDGE_OK = False


def _edge_usable():
    """'' if edge_tts and ffmpeg are there, else the reason."""
    try:
        import edge_tts  # noqa: F401
    except Exception as exc:
        return f"edge_tts not available ({exc})"
    return "" if shutil.which("ffmpeg") else "ffmpeg missing"


def _edge_voice(key):
    """Edge voice for a language: the configured one, else the curated default."""
    return EDGE_MAP.get(key) or tts_langs.edge_default(key)


if ENGINE == "edge":
    _why = _edge_usable()
    if _why:
        log(f"Edge: {_why} -> staying with Piper")
    else:
        EDGE_OK = True
        log("Edge-TTS active: " + ", ".join(f"{k}->{_edge_voice(k)}" for k in READ_LANGS)
            + "  (fallback: Piper)")
elif EDGE_MAP:
    log(f"Edge voices configured, but engine={ENGINE} -> not used")

# Edge as the language fallback for languages the engine cannot speak and
# Piper has no voice for. Not with engine=piper: that choice means offline.
EDGE_LANG_OK = EDGE_OK or (ENGINE != "piper" and not _edge_usable())


def _rate_percent(length_scale):
    """Piper length_scale (smaller = faster) -> Edge rate, e.g. '+25%'."""
    ls = length_scale or 1.0
    return f"{round((1.0 / ls - 1.0) * 100):+d}%"


def edge_chunks(sentence: str, edge_voice: str, length_scale):
    """One sentence from Edge as a generator of chunks, streamed through
    ffmpeg so playback starts with the first decoded frame.

    If the connection fails before any audio arrived, nothing was played and
    the exception lets Piper read the sentence. A failure mid-sentence cannot
    fall back."""
    rate_arg = _rate_percent(length_scale)
    # Hidden: the daemon runs under pythonw, so a plain Popen of ffmpeg would
    # open a console window for every sentence on Windows.
    ff = tts_platform.popen_hidden(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-f", "s16le", "-ar", str(EDGE_RATE), "-ac", "1", "pipe:1"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    state = {"err": None}

    def feed():
        import edge_tts

        async def go():
            comm = edge_tts.Communicate(sentence, edge_voice, rate=rate_arg)
            async for part in comm.stream():
                if part["type"] == "audio" and part["data"]:
                    ff.stdin.write(part["data"])
            ff.stdin.flush()

        try:
            asyncio.run(go())
        except Exception as exc:
            state["err"] = exc
        finally:
            try:
                ff.stdin.close()            # tells ffmpeg the input is complete
            except Exception:
                pass

    feeder = threading.Thread(target=feed, daemon=True)
    feeder.start()

    yielded = False
    try:
        while True:
            buf = ff.stdout.read(4096)
            if not buf:
                break
            yielded = True
            yield _Chunk(buf, EDGE_RATE, 1)
    finally:
        try:
            ff.stdout.close()
        except Exception:
            pass
        feeder.join(timeout=10)
        try:
            ff.wait(timeout=5)
        except Exception:
            ff.kill()

    if not yielded:
        raise state["err"] or RuntimeError("Edge returned no audio")


# --- SAPI5 (classic Windows voices) ----------------------------------------
# Windows only (pywin32). sapi_<key>=<name substring>; without an entry a
# de/en voice is picked by language. 22 kHz PCM.
SAPI_OK = False
SAPI_LANGS = set()          # languages with an installed SAPI voice
if ENGINE == "sapi":
    if tts_platform.IS_WINDOWS:
        try:
            import win_sapi
            if win_sapi.list_voices():
                SAPI_OK = True
                SAPI_LANGS = win_sapi.languages()
                log("SAPI5 active: "
                    + (", ".join(f"{k}->{v}" for k, v in SAPI_MAP.items()) or "automatic voices")
                    + "  (fallback: Piper)")
            else:
                log("SAPI5: no voices found -> Piper")
        except Exception as exc:
            log(f"SAPI5 not available ({exc}) -> Piper")
    else:
        log("SAPI5 is Windows only -> Piper")


def sapi_pcm_chunks(sentence, voice_name, key, length_scale):
    import win_sapi
    return _pcm_chunks(*win_sapi.synth_pcm(sentence, voice_name, key, length_scale))


def sapi_chunks(key: str, sentence: str, length_scale):
    chunks = sapi_pcm_chunks(sentence, SAPI_MAP.get(key, ""), key, length_scale)
    if not chunks:
        raise RuntimeError("SAPI returned no audio")
    return chunks


# --- MS Speech Platform via balcon under Wine (Linux) ----------------------
# engine=mssp, mssp_cmd=<balcon command, e.g. 'wine /path/balcon.exe'>,
# mssp_<lang>=<voice name substring> (empty = pick by locale).
# balcon runs as a subprocess per sentence and writes a WAV.
MSSP_ARGV = shlex.split(MSSP_CMD) if MSSP_CMD else []
MSSP_LOCK = threading.Lock()   # the engine in the Wine prefix fails on parallel calls
MSSP_OK = False
MSSP_VOICES = []


def mssp_get_voices():
    """Installed voices from '<cmd> -l' (one per line)."""
    out = tts_platform.run_hidden(MSSP_ARGV + ["-l"], capture_output=True, text=True,
                                  timeout=20)
    return [ln.strip() for ln in out.stdout.splitlines()
            if ln.strip() and not ln.rstrip().endswith(":")
            and not ln.lower().startswith(("balcon", "voice"))]


def _mssp_pick(key):
    """Voice name: MSSP_MAP[key] as substring, then by locale ("fr-"), else
    '' (balcon's default voice)."""
    name = MSSP_MAP.get(key, "")
    if name:
        for v in MSSP_VOICES:
            if name.lower() in v.lower():
                return v
        return name
    if key in tts_langs.SUPPORTED:
        for v in MSSP_VOICES:
            if f"{key}-" in v.lower():
                return v
    return ""


def _parse_wav(data):
    """RIFF/WAVE bytes -> (pcm_bytes, rate, channels). Expects 16-bit PCM."""
    import struct
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise RuntimeError("no WAV produced")
    i, rate, ch, pcm = 12, 22050, 1, b""
    while i + 8 <= len(data):
        cid = data[i:i + 4]
        size = struct.unpack("<I", data[i + 4:i + 8])[0]
        body = data[i + 8:i + 8 + size]
        if cid == b"fmt " and len(body) >= 16:
            ch = struct.unpack("<H", body[2:4])[0] or 1
            rate = struct.unpack("<I", body[4:8])[0] or 22050
        elif cid == b"data":
            pcm = body
        i += 8 + size + (size & 1)
    return pcm, rate, ch


def _rate_steps(length_scale):
    """Piper length_scale (smaller = faster) -> rate -10..10 (larger = faster),
    as used by balcon -s and SAPI. Rate r speaks 3 ** (r / 10) times as fast."""
    ls = length_scale or 1.0
    return max(-10, min(10, round(10 * math.log(1.0 / ls) / math.log(3))))


def _rate_pace(pace, test=None):
    """Pace that SAPI/balcon actually render for a requested pace."""
    return 3 ** (_rate_steps(1.0 / pace) / 10)


def _mssp_synth(voice, key, sentence, length_scale):
    """Run balcon -> WAV bytes (22 kHz, 16 bit, mono)."""
    if not voice:
        voice = _mssp_pick(key)
    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    args = list(MSSP_ARGV)
    if voice:
        args += ["-n", voice]
    # The text goes through a UTF-8 file (-f), not the command line (-t): Wine
    # rebuilds a Windows command line from argv, and quotes in the text would
    # shift the parsing. The BOM makes balcon read UTF-8.
    fd2, tmptxt = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd2, "wb") as f:
        f.write(b"\xef\xbb\xbf" + sentence.encode("utf-8"))
    args += ["-s", str(_rate_steps(length_scale)),
             "-fr", "22050", "-bt", "16", "-ch", "1",
             "-f", tmptxt, "-w", tmp]
    try:
        # DEVNULL instead of capture_output: Wine helper processes inherit the
        # pipe and keep it open for about a second after balcon exits.
        with MSSP_LOCK:
            tts_platform.run_hidden(args, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=60)
            with open(tmp, "rb") as f:
                return f.read()
    finally:
        for _f in (tmp, tmptxt):
            try:
                os.unlink(_f)
            except OSError:
                pass


def mssp_preview(sentence, voice, key, length_scale):
    return _pcm_chunks(*_parse_wav(_mssp_synth(voice, key, sentence, length_scale)))


def mssp_chunks(key: str, sentence: str, length_scale):
    chunks = mssp_preview(sentence, MSSP_MAP.get(key, ""), key, length_scale)
    if not chunks:
        raise RuntimeError(f"balcon returned no audio for {sentence[:60]!r}")
    return chunks


if ENGINE == "mssp":
    if MSSP_ARGV:
        try:
            MSSP_VOICES = mssp_get_voices()
            MSSP_OK = True
            log(f"MS Speech Platform (balcon) active via '{MSSP_CMD}' "
                f"({len(MSSP_VOICES)} voices)  (fallback: Piper)")
        except FileNotFoundError:
            log(f"mssp command not found ('{MSSP_CMD}') -> Piper")
        except Exception as exc:
            log(f"mssp init failed ({exc}) -> Piper")
    else:
        log("engine=mssp, but mssp_cmd is not set -> Piper")


# --- ElevenLabs (online, API key) -------------------------------------------
# engine=elevenlabs + elevenlabs_model=<model_id> + elevenlabs_<key>=<voice_id>.
# Returns 24 kHz PCM directly. Played sentences go to an audio cache, so
# repeats cost no credits.
EL_CONF = CFG.prefixed("elevenlabs")
EL_MODEL = EL_CONF.get("model") or eleven.DEFAULT_MODEL
EL_CACHE = EL_CONF.get("cache", "on").lower() not in ("off", "0", "aus")
EL_OK = False


def _el_voice(key, override=""):
    """Voice for a language key. ElevenLabs voices are multilingual: a
    language without its own voice uses the voice of another reading
    language (then de, then en)."""
    if override or EL_CONF.get(key):
        return override or EL_CONF.get(key)
    return next((EL_CONF[k] for k in list(READ_LANGS) + ["de", "en"] if EL_CONF.get(k)), "")


if ENGINE == "elevenlabs":
    if not eleven.load_key():
        log(f"ElevenLabs: no API key ({eleven.key_path()}) -> Piper")
    elif not _el_voice(DEFAULT_KEY):
        log("ElevenLabs: no voice selected (elevenlabs_<lang>) -> Piper")
    else:
        EL_OK = True
        log("ElevenLabs active: model " + EL_MODEL + ", voices "
            + ", ".join(f"{k}->{_el_voice(k)}" for k in READ_LANGS)
            + f", cache {'on' if EL_CACHE else 'off'}  (fallback: Piper)")


def elevenlabs_chunks(key, sentence, length_scale, ctx=(None, None), voice="", model=""):
    """One sentence from ElevenLabs. Errors before the first byte (network,
    key, credits) are raised so Piper can take over."""
    lang = key if key in tts_langs.SUPPORTED else None
    for pcm in eleven.stream_pcm(sentence, _el_voice(key, voice), model or EL_MODEL,
                                 eleven.load_key(), length_scale, lang=lang,
                                 previous_text=ctx[0], next_text=ctx[1],
                                 use_cache=EL_CACHE):
        yield _Chunk(pcm, eleven.SAMPLE_RATE, 1)


# --- Local neural servers (neural/models.json) ------------------------------
# engine=<key> + <key>_<lang>=<voice> + optional <key>_instruct=<text>. The
# server runs as its own process, is prewarmed at daemon start and stops
# after neural_idle_min idle minutes; the next sentence starts it again.
NEURAL_OK = False
NEURAL_CONF = CFG.prefixed(ENGINE) if ENGINE in tts_neural.ENGINES else {}
NEURAL_LANGS = tts_neural.langs(ENGINE) if ENGINE in tts_neural.ENGINES else ()
try:
    NEURAL_IDLE_MIN = float(CFG.values.get("neural_idle_min", 30))
except ValueError:
    NEURAL_IDLE_MIN = 30.0
if ENGINE in tts_neural.ENGINES:
    if tts_neural.installed(ENGINE) and not tts_neural.usable(ENGINE):
        log(f"{tts_neural.launcher.within_budget(ENGINE)[1]} -> Piper")
    elif tts_neural.installed(ENGINE):
        NEURAL_OK = True
        log(f"{tts_neural.title(ENGINE)} active (local server, voices "
            + ", ".join(f"{k}->{NEURAL_CONF.get(k) or 'default'}"
                        for k in READ_LANGS if k in NEURAL_LANGS)
            + f", idle stop after {tts_neural.launcher.idle_min(ENGINE) or NEURAL_IDLE_MIN:g} min)"
            "  (fallback: Piper)")
    else:
        hint = tts_neural.launcher.setup_hint(ENGINE) if tts_neural.launcher else "neural/"
        log(f"engine={ENGINE}, but not installed ({hint}) -> Piper")


def _neural_startup():
    """Stop servers of other models first (also those left by an earlier
    daemon or a preview), then prewarm the active one. This keeps at most one
    model in VRAM, also when switching to a non-neural engine."""
    tts_neural.stop_others(ENGINE if NEURAL_OK else None)
    if NEURAL_OK:
        tts_neural.prewarm(ENGINE)


threading.Thread(target=_neural_startup, daemon=True).start()
tts_neural.start_idle_reaper(list(tts_neural.ENGINES), NEURAL_IDLE_MIN)


def neural_chunks(engine, key, sentence, length_scale, voice="", instruct=None):
    """One sentence from a local neural server, streamed. Errors before the
    first byte (server down, model error) are raised for the fallback."""
    conf = NEURAL_CONF if engine == ENGINE else {}
    lang = key if key in tts_langs.SUPPORTED else "en"
    if instruct is None:
        instruct = conf.get("instruct", "")
    for pcm, sr in tts_neural.stream(engine, sentence, voice or conf.get(lang, ""),
                                     lang, 1.0 / (length_scale or 1.0), instruct):
        yield _Chunk(pcm, sr, 1)


# --- Engine table ------------------------------------------------------------
def _no_native_speed(pace, test=None):
    return 1.0


@dataclass(frozen=True)
class EngineSpec:
    """How the daemon uses one engine.

    ready(key)                        engine can speak language `key` now
    speak(key, sentence, ls, ctx)     chunks for one sentence; raises before
                                      the first chunk on failure
    preview(sentence, test, ls)       chunks for a preview request (test dict)
    online      after a failure the rest of the job uses Piper, so a missing
                network does not time out on every sentence
    chunked     renders whole units before playback: gets clause-sized units
                and a prebuffer (prebuffer_ms)
    native(pace, test)  pace the engine renders itself for a requested pace
                (1.0 = no native speed control); the daemon time-stretches
                the rest, so every engine reaches the same pace"""
    ready: Callable
    speak: Callable
    preview: Callable
    online: bool = False
    chunked: bool = False
    native: Callable = _no_native_speed


def _clamp_pace(lo, hi):
    return lambda pace, test=None: min(hi, max(lo, pace))


def _el_pace(pace, test=None):
    model = (test or {}).get("model") or EL_MODEL
    if not eleven.model_info(model).get("speed"):
        return 1.0
    return eleven.speed_from_length_scale(1.0 / pace)


def _neural_spec(engine):
    return EngineSpec(
        ready=lambda key: NEURAL_OK and key in NEURAL_LANGS,
        speak=lambda key, s, ls, ctx: neural_chunks(engine, key, s, ls),
        preview=lambda s, t, ls: neural_chunks(engine, t["key"], s, ls, voice=t["voice"],
                                               instruct=t.get("instruct")),
        native=_clamp_pace(0.5, 2.0) if tts_neural.native_speed(engine) else _no_native_speed)


# Piper's length_scale does not scale the duration in proportion (measured:
# 0.67 gives 0.78 to 0.82 of the duration), so Piper is time-stretched too.
PIPER_NATIVE = _no_native_speed

ENGINES = {
    "edge": EngineSpec(
        ready=lambda key: EDGE_OK and bool(_edge_voice(key)),
        speak=lambda key, s, ls, ctx: edge_chunks(s, _edge_voice(key), ls),
        preview=lambda s, t, ls: edge_chunks(s, t["voice"], ls),
        online=True, native=_clamp_pace(0.5, 2.0)),
    "sapi": EngineSpec(          # automatic voice choice: key need not be in SAPI_MAP
        ready=lambda key: SAPI_OK and (key in SAPI_MAP or key in SAPI_LANGS
                                       or key in tts_langs.DEFAULT_READ),
        speak=lambda key, s, ls, ctx: sapi_chunks(key, s, ls),
        preview=lambda s, t, ls: sapi_pcm_chunks(s, t["voice"], t["key"], ls),
        native=_rate_pace),
    "mssp": EngineSpec(
        ready=lambda key: MSSP_OK and (key in MSSP_MAP or key in tts_langs.DEFAULT_READ
                                       or key in tts_langs.voice_langs(MSSP_VOICES)),
        speak=lambda key, s, ls, ctx: mssp_chunks(key, s, ls),
        preview=lambda s, t, ls: mssp_preview(s, t["voice"], t["key"], ls),
        native=_rate_pace),
    "elevenlabs": EngineSpec(
        ready=lambda key: EL_OK and key in tts_langs.SUPPORTED,
        speak=lambda key, s, ls, ctx: elevenlabs_chunks(key, s, ls, ctx),
        preview=lambda s, t, ls: elevenlabs_chunks(t["key"], s, ls, voice=t["voice"],
                                                   model=t.get("model", "")),
        online=True, native=_el_pace),
}
ENGINES.update({k: _neural_spec(k) for k in tts_neural.ENGINES})
ACTIVE = ENGINES.get(ENGINE)        # None for piper


def speakable(key):
    """Some voice can read language `key`: the engine, Piper or Edge."""
    return (key in VOICES or (ACTIVE is not None and ACTIVE.ready(key))
            or (EDGE_LANG_OK and bool(tts_langs.edge_default(key))))


# --- Language detection for voice="auto" -----------------------------------
# Restricted to the reading languages that some voice can read. The detector
# is preloaded; detection takes under 1 ms per sentence (lingua, Rust core).
DETECTOR = None
LANG_TO_KEY = {}
_auto = [k for k in READ_LANGS if speakable(k)]
try:
    DETECTOR, LANG_TO_KEY = tts_langs.build_detector(_auto)
    if DETECTOR is not None:
        log(f"language detection active for: {', '.join(_auto)}")
    else:
        log("language detection inactive (fewer than 2 readable languages)")
except Exception as exc:
    log(f"lingua not available ({exc}), 'auto' uses the default voice")
if ACTIVE is not None:
    for _k in READ_LANGS:
        if not ACTIVE.ready(_k):
            log(f"{ENGINE} does not read {_k} -> " + tts_langs.speak_route(
                _k, engine_ok=False, piper_langs=VOICES, edge_ok=EDGE_LANG_OK))


if FALLBACK == "off":
    log("fallback OFF: on engine errors the sentence is skipped, the voice never changes")
if PAUSE_SENTENCE_MS:
    log(f"sentence pause: {PAUSE_SENTENCE_MS} ms")
log(f"language detection: {'per sentence' if LANG_DETECT == 'sentence' else 'once per text'}")


# --- Text clean-up: the rules from rules.json ------------------------------
# One ordered list (tts_rules): the presets for Obsidian, Markdown and
# formulas, then the user's own rules. It reloads when the file changes.
# migrate() sets the list up once from replace.conf and the former filters=
# line in voices.conf.
try:
    import tts_rules
    tts_rules.migrate(log=log)
    PIPELINE = tts_rules.Pipeline(log=log)
    PIPELINE.current()                       # logs the rule count once at startup
except Exception as exc:
    PIPELINE = None
    log(f"text clean-up not available ({exc})")


def rules_lang(voice_key, text):
    """Language that decides which language-scoped rules run: the requested
    voice key, or for "auto" the detected language of the whole text."""
    key = voice_key
    if key == "auto":
        key = DEFAULT_KEY
        if DETECTOR is not None and sum(c.isalpha() for c in text) >= 3:
            key = LANG_TO_KEY.get(DETECTOR.detect_language_of(text), DEFAULT_KEY)
    return tts_langs.norm(str(key)[:2]) or None


def apply_filters(text, lang=None):
    if PIPELINE is None:
        return text
    return PIPELINE.apply(text, lang)


# --- Splitting text into playback units -------------------------------------
# Units for chunked engines: long sentences are split only at clause
# boundaries and merged up to UNIT_CAP characters; the first unit may be
# smaller (FIRST_UNIT_CAP) for an earlier first sound.
UNIT_CAP = 60
FIRST_UNIT_CAP = 28
FIRST_SPLIT_MIN = 45            # first sentences up to this length stay whole
PREBUFFER_BYTES = int(PREBUFFER_MS / 1000 * 24000 * 2)    # 24 kHz mono s16


def split_sentences(text: str):
    parts = re.split(r"(?<=[.!?…:;])\s+|\n+", text.strip())
    return [p for p in (s.strip() for s in parts) if p]


def split_first_for_latency(sentences):
    """Split a long first sentence into a short lead and the rest, so the
    first sound comes early. Prefers the first comma after 15 characters
    (within 70), else cuts after 7 words."""
    if not sentences or len(sentences[0]) <= FIRST_SPLIT_MIN:
        return sentences
    first = sentences[0]
    cut = None
    m = re.search(r",\s+", first[15:])
    if m and 15 + m.end() <= 70:
        cut = 15 + m.end()
    else:
        words = first.split(" ")
        if len(words) > 8:
            cut = len(" ".join(words[:7])) + 1
    if not cut:
        return sentences
    lead, rest = first[:cut].strip(), first[cut:].strip()
    return [lead, rest] + sentences[1:] if rest else sentences


def _word_split(text, cap):
    """Split at a word boundary near `cap` into [head, rest], never inside a word."""
    words = text.split(" ")
    lead = ""
    for i, w in enumerate(words):
        if lead and len(lead) + 1 + len(w) > cap:
            return [lead, " ".join(words[i:])]
        lead = (lead + " " + w) if lead else w
    return [text]


def split_natural(text, cap):
    """Merge clauses (split at , ; : – —) into units of at most `cap`
    characters. A clause longer than `cap` stays one unit."""
    text = text.strip()
    if len(text) <= cap:
        return [text]
    parts = re.split(r"(?<=[,;:–—])\s+", text)
    units, cur = [], ""
    for p in parts:
        if cur and len(cur) + 1 + len(p) > cap:
            units.append(cur)
            cur = p
        else:
            cur = (cur + " " + p) if cur else p
    if cur:
        units.append(cur)
    return units


# Piper voices that are not in voices.conf, loaded for previews and kept warm.
TEST_PIPER = {}


def get_test_piper(base: str):
    if base not in TEST_PIPER:
        path = PIPER_DIR / f"{base}.onnx"
        if not path.exists():
            raise FileNotFoundError(path)
        TEST_PIPER[base] = _piper_load_safe(path)
    return TEST_PIPER[base]


def _engine_safe(text: str) -> str:
    """Text for a retry with fallback=off: letters, digits, whitespace and
    common punctuation stay, everything else (emoji, symbols, control
    characters) becomes a space."""
    safe = re.sub(r"[^\w \t\n.,;:!?()\"'%/+\-–—…€§°]", " ", text)
    return re.sub(r"[ \t]{2,}", " ", safe).strip()


class Job:
    """One read-aloud job in its own thread, cancellable."""

    def __init__(self, text: str, voice_key: str, length_scale,
                 pause_ms=None, test=None):
        self.text = text
        self.voice_key = voice_key          # may be "auto"
        self.length_scale = length_scale
        self.pause_ms = pause_ms            # None -> PAUSE_SENTENCE_MS
        self.test = test                    # preview request, see handle()
        # Audio of a cacheable preview, saved once it rendered completely
        self._rec = [] if (test and test.get("cache")) else None
        self._rec_fmt = None
        self._test_failed = False
        self.pace = 1.0 / length_scale if length_scale else 1.0
        self.syn = SynthesisConfig(length_scale=self._speed(PIPER_NATIVE)[0])
        self.fx = tts_audio.Chain(LOUDNESS)
        self._src = ("", 1.0)               # loudness key and time stretch of the current chunks
        self._t0 = time.perf_counter()
        self.stop = threading.Event()
        self.sink = None                    # tts_platform audio sink, set in _run
        self._fallback = False              # online engine failed: rest of the job is Piper
        self._edge_down = False             # Edge failed in this job: no Edge fallback
        self._whole_key = None              # cached language for lang_detect=text
        self._ctx = (None, None)            # neighbouring units (ElevenLabs prosody)
        chunked = ACTIVE is not None and ACTIVE.chunked
        self.prebuf_bytes = PREBUFFER_BYTES if (chunked and not test) else 0
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.paused = False

    def toggle_pause(self):
        """Pause or resume in place. While paused, the full queue also blocks
        synthesis, so nothing is lost."""
        sink = self.sink
        if sink is not None:
            sink.pause_toggle()
            self.paused = not self.paused

    def _detect(self, text: str, fallback: str) -> str:
        """Language of a text -> voice key. Pieces with fewer than 3 letters
        (math, numbers, symbols) keep the current language."""
        if sum(c.isalpha() for c in text) < 3:
            return fallback
        key = LANG_TO_KEY.get(DETECTOR.detect_language_of(text))
        return key if key and speakable(key) else fallback

    def _pick_key(self, sentence: str, last_key: str) -> str:
        """Voice key for this unit. For "auto": lang_detect=text decides once
        for the whole text, lang_detect=sentence per unit."""
        if self.voice_key != "auto" or DETECTOR is None:
            k = self.voice_key
            return k if (k in VOICES or (k != "auto" and speakable(k))) else DEFAULT_KEY
        if LANG_DETECT == "sentence":
            return self._detect(sentence, last_key)
        if self._whole_key is None:
            self._whole_key = self._detect(self.text, DEFAULT_KEY)
        return self._whole_key

    def _engine_chunks(self, key: str):
        """Chunk factory of the active engine for this key, or None (-> Piper)."""
        if self._fallback or ACTIVE is None or not ACTIVE.ready(key):
            return None
        ls, tempo = self._speed(ACTIVE.native)
        self._src = (f"{ENGINE}|{key}|{CFG.values.get(f'{ENGINE}_{key}', '')}", tempo)
        return lambda s: ACTIVE.speak(key, s, ls, self._ctx)

    def _speed(self, native, test=None):
        """(length_scale for the engine, time stretch for the rest of the pace)."""
        if not self.length_scale:
            return self.length_scale, 1.0
        pace = native(self.pace, test)
        return 1.0 / pace, self.pace / pace

    def _test_chunks(self, sentence: str):
        """Preview: explicit engine and voice from the tray, no fallback."""
        t = self.test
        spec = ENGINES.get(t["engine"])
        ls, tempo = self._speed(spec.native if spec is not None else PIPER_NATIVE, t)
        self._src = (f"{t['engine']}|{t['key']}|{t['voice']}", tempo)
        if spec is not None:
            yield from spec.preview(sentence, t, ls)
        else:
            yield from get_test_piper(self.test["voice"]).synthesize(sentence,
                                                                      syn_config=self.syn)

    def _synth(self, sentence: str, key: str):
        """Chunks for one unit: the chosen engine, Piper on failure."""
        if self.test:
            try:
                yield from self._test_chunks(sentence)
            except Exception as exc:
                self._test_failed = True
                log(f"preview failed ({self.test.get('engine')}: {exc})")
            return
        make = self._engine_chunks(key)
        if make is not None:
            try:
                yield from make(sentence)
                return
            except Exception as exc:
                if FALLBACK == "off":
                    # Never switch the voice: retry once with sanitized text,
                    # then skip the sentence.
                    safe = _engine_safe(sentence)
                    if safe and safe != sentence:
                        try:
                            yield from make(safe)
                            return
                        except Exception:
                            pass
                    log(f"{ENGINE} error ({key}: {exc}) -> sentence skipped (fallback=off)")
                    return
                # Online engines: the rest of the job goes to Piper. Local
                # engines only fall back for this sentence, so one unreadable
                # character does not switch the whole text to the other voice.
                if ACTIVE.online:
                    self._fallback = True
                if ENGINE == "edge":
                    self._edge_down = True
                log(f"{ENGINE} error ({key}: {exc}) -> Piper fallback")
        yield from self._fallback_chunks(sentence, key)

    def _fallback_chunks(self, sentence: str, key: str):
        """A unit the active engine does not read: the Piper voice of the
        language, else Edge's voice of the language (until Edge fails in this
        job), else the default Piper voice."""
        route = tts_langs.speak_route(key, engine_ok=False, piper_langs=VOICES,
                                      edge_ok=EDGE_LANG_OK and not self._edge_down)
        if route == "edge":
            ls, tempo = self._speed(ENGINES["edge"].native)
            self._src = (f"edge|{key}|{_edge_voice(key)}", tempo)
            try:
                yield from edge_chunks(sentence, _edge_voice(key), ls)
                return
            except Exception as exc:
                self._edge_down = True
                log(f"Edge fallback for {key} failed ({exc}) -> Piper")
        pk = key if key in VOICES else DEFAULT_KEY
        self._src = (f"piper|{pk}|{VOICE_MAP.get(pk, '')}", self._speed(PIPER_NATIVE)[1])
        voice = VOICES[pk]
        yield from voice.synthesize(sentence, syn_config=self.syn)

    def _put(self, q, item):
        """Put into the queue without blocking forever after a stop."""
        while not self.stop.is_set():
            try:
                q.put(item, timeout=0.2)
                return
            except queue.Full:
                continue

    def _units(self):
        """Playback units. Streaming engines get sentences (the first one
        possibly split for latency). Chunked engines get clause-sized units
        with a small first unit."""
        sents = split_sentences(self.text)
        if ACTIVE is None or not ACTIVE.chunked:
            return split_first_for_latency(sents)
        units = []
        for sent in sents:
            units.extend(split_natural(sent, UNIT_CAP))
        # Shrink only the first unit: at a clause boundary if possible, else
        # at a word boundary near FIRST_UNIT_CAP.
        if units and len(units[0]) > FIRST_UNIT_CAP:
            head = split_natural(units[0], FIRST_UNIT_CAP)
            if len(head) == 1:
                head = _word_split(units[0], FIRST_UNIT_CAP)
            if len(head) > 1:
                units = head + units[1:]
        return units

    def _produce(self, q):
        """Synthesize ahead into the queue (audio chunks plus pause silence),
        in parallel with playback. A full queue applies backpressure."""
        last_key, lr, lc = DEFAULT_KEY, None, None
        try:
            units = self._units()
            for i, unit in enumerate(units):
                if self.stop.is_set():
                    break
                self._ctx = (units[i - 1] if i else None,
                             units[i + 1] if i + 1 < len(units) else None)
                last_key = self._pick_key(unit, last_key)
                for chunk in self._synth(unit, last_key):
                    if self.stop.is_set():
                        return
                    for part in self._process(chunk):
                        lr, lc = part.sample_rate, part.sample_channels
                        self._emit(q, part)
                for part in self._process(None):
                    lr, lc = part.sample_rate, part.sample_channels
                    self._emit(q, part)
                pause = self.pause_ms if self.pause_ms is not None else PAUSE_SENTENCE_MS
                # Pause only at a real sentence end, not between parts of one sentence.
                if (pause and lr and not self.stop.is_set()
                        and unit.rstrip()[-1:] in ".!?…:;"):
                    n = int(lr * pause / 1000)
                    self._put(q, _Chunk(b"\x00" * (n * 2 * lc), lr, lc))
            self._save_preview()
        finally:
            self._put(q, None)        # end of playback
            if LOUDNESS is not None:
                LOUDNESS.save()

    def _process(self, chunk):
        """Chunks through tts_audio (time stretch, loudness, limiter). None
        ends the unit and yields the stretch tail. Whole-sentence chunks
        (Piper, SAPI) are processed in PCM_STEP pieces, so the first piece
        plays without waiting for the rest."""
        if chunk is None:
            pcm = self.fx.end_unit()
            if pcm:
                yield _Chunk(pcm, self.fx.sr, 1)
            return
        key, tempo = self._src
        if chunk.sample_channels != 1 or not self.fx.active(tempo):
            yield chunk
            return
        raw, rate = chunk.audio_int16_bytes, chunk.sample_rate
        for i in range(0, len(raw), PCM_STEP):
            pcm = self.fx.process(raw[i:i + PCM_STEP], rate, key, tempo)
            if pcm:
                yield _Chunk(pcm, rate, 1)

    def _emit(self, q, chunk):
        if self._rec is not None:
            self._record(chunk)
        self._put(q, chunk)

    def _record(self, chunk):
        fmt = (chunk.sample_rate, chunk.sample_channels)
        if self._rec_fmt not in (None, fmt):  # format changed mid-preview: do not cache
            self._rec = None
            return
        self._rec_fmt = fmt
        self._rec.append(chunk.audio_int16_bytes)

    def _save_preview(self):
        """Keep a complete, successful preview for instant replay."""
        if not self._rec or self._test_failed or self.stop.is_set():
            return
        t = self.test
        try:
            tts_previews.store(t["engine"], t["voice"], t["key"], b"".join(self._rec),
                               self._rec_fmt[0], self._rec_fmt[1], scale=self.length_scale,
                               model=t.get("model", "") if t["engine"] == "elevenlabs" else "")
        except Exception as exc:
            log(f"preview not cached: {exc}")

    def _run(self):
        q = queue.Queue(maxsize=128)
        prod = threading.Thread(target=self._produce, args=(q,), daemon=True)
        prod.start()

        state = {"rate": None, "logged": False}

        def play(chunk):
            """Write a chunk to the sink; reopen it when the sample rate
            changes (engine or language switch). False = stop."""
            sink = self.sink
            if sink is not None and chunk.sample_rate != state["rate"]:
                sink.close()
                sink = self.sink = None
            if sink is None:
                state["rate"] = chunk.sample_rate
                sink = tts_platform.make_audio_sink(chunk.sample_rate, chunk.sample_channels)
                self.sink = sink
                if self.stop.is_set():        # cancelled while opening
                    sink.kill()
                    return False
            ok = sink.write(chunk.audio_int16_bytes)
            if ok and not state["logged"]:
                state["logged"] = True
                log(f"first audio after {(time.perf_counter() - self._t0) * 1000:.0f} ms")
            return ok

        try:
            pending, pending_bytes = [], 0
            released = self.prebuf_bytes == 0
            while not self.stop.is_set():
                try:
                    chunk = q.get(timeout=0.2)
                except queue.Empty:
                    continue
                if chunk is None:                 # end: flush the prebuffer
                    for c in pending:
                        if not play(c):
                            break
                    break
                if not released:                  # still filling the prebuffer
                    pending.append(chunk)
                    pending_bytes += len(chunk.audio_int16_bytes)
                    if pending_bytes >= self.prebuf_bytes:
                        released = True
                        for c in pending:
                            if not play(c):
                                released = None   # cancelled
                                break
                        pending = []
                    continue
                if released is None or not play(chunk):
                    break
        finally:
            self.stop.set()                       # make sure the producer ends
            if self.sink is not None:
                self.sink.close()
            prod.join(timeout=1)

    def start(self):
        self.thread.start()

    def cancel(self):
        self.stop.set()
        if self.sink is not None:
            self.sink.kill()


_lock = threading.Lock()
_current = None


def handle(payload: str):
    global _current
    text, voice_key, length_scale = payload, DEFAULT_KEY, None
    pause_ms, test = None, None

    stripped = payload.strip()
    if stripped == "__STOP__":
        cmd = "stop"
    else:
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                if obj.get("cmd") == "stop":
                    cmd = "stop"
                elif obj.get("cmd") in ("pause", "toggle"):
                    cmd = "pause"
                elif obj.get("cmd") == "quit":
                    cmd = "quit"
                else:
                    cmd = "say"
                    text = obj.get("text", "")
                    voice_key = obj.get("voice", DEFAULT_KEY) or DEFAULT_KEY
                    length_scale = obj.get("length_scale")
                    pause_ms = obj.get("pause_ms")
                    # Preview from the tray. 'key' (a language code) selects
                    # the model and language for engines that need it.
                    if obj.get("cmd") == "test" and obj.get("engine"):
                        test = {"engine": obj.get("engine"),
                                "voice": obj.get("voice", ""),
                                "key": obj.get("key", DEFAULT_KEY),
                                "model": obj.get("model", ""),
                                "instruct": obj.get("instruct")}
                        if obj.get("cache") and tts_previews.cacheable(
                                test["engine"], test["voice"], test["key"], text,
                                test["instruct"] or "", pause_ms):
                            test["cache"] = True
                        voice_key = DEFAULT_KEY
            else:
                cmd = "say"
        except (ValueError, TypeError):
            cmd = "say"  # raw text

    if cmd == "quit":
        with _lock:
            job = _current
        if job:
            job.cancel()
        os._exit(0)

    if cmd == "pause":
        with _lock:
            job = _current
        if job:
            job.toggle_pause()
        return

    if cmd == "stop":
        with _lock:
            job, _current = _current, None
        if job:
            job.cancel()
        return

    if not text.strip():
        return
    if test is None:
        text = apply_filters(text, rules_lang(voice_key, text))
        if not text.strip():
            return
    preview = text.strip().replace("\n", " ")[:50]
    log(f"job: voice={voice_key} len={len(text)} \"{preview}…\"")
    new_job = Job(text, voice_key, length_scale, pause_ms=pause_ms, test=test)
    with _lock:
        old, _current = _current, new_job
    if old:
        old.cancel()
    new_job.start()


def status() -> dict:
    """Playback state for the tray icon."""
    with _lock:
        job = _current
    if job is None or job.stop.is_set() or not job.thread.is_alive():
        return {"state": "idle"}
    return {"state": "paused" if job.paused else "speaking"}


def _is_status(text: str) -> bool:
    t = text.strip()
    if not t.startswith("{") or '"status"' not in t:
        return False
    try:
        return json.loads(t).get("cmd") == "status"
    except (ValueError, AttributeError):
        return False


def main():
    server = tts_platform.ipc_listen()
    log(f"ready on {tts_platform.ipc_address()} (voices: {', '.join(VOICES)})")

    while True:
        conn, _ = server.accept()
        with conn:
            data = bytearray()
            while True:
                buf = conn.recv(65536)
                if not buf:
                    break
                data += buf
            text = bytes(data).decode("utf-8", errors="replace")
            if _is_status(text):
                try:
                    conn.sendall(json.dumps(status()).encode("utf-8"))
                except OSError:
                    pass
                continue
        if text:
            try:
                handle(text)
            except Exception as exc:      # one bad job must not take the daemon down
                log(f"handle error: {exc!r}")


if __name__ == "__main__":
    main()
