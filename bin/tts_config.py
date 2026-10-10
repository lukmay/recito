"""
tts_config: reading voices.conf.

voices.conf is a flat key=value file; '#' starts a comment line and the last
occurrence of a key wins. parse() returns the raw values, daemon_config()
interprets the keys the daemon cares about. No side effects on import.
"""

from dataclasses import dataclass, field
from pathlib import Path

import tts_langs

# Engine-specific voice maps: "<prefix>_<voice key>=<voice>".
VOICE_MAP_PREFIXES = ("edge", "sapi", "mssp")

# Removed engines -> successor engine. Their "<engine>_<key>" voices are
# ignored, or carried over to the successor by migrate().
LEGACY_ENGINES = {"kokoro": "kokoro2"}      # kokoro-onnx -> Kokoro model server

# Keys read elsewhere (client, tray) or by dedicated code in the daemon.
RESERVED_KEYS = {"engine", "pause_sentence", "speed_normal", "speed_fast",
                 "autostart", "device", "prebuffer_ms", "mssp_cmd", "fallback",
                 "lang_detect", "filters", "loudness_norm", "loudness_target",
                 "ui_language", "read_langs", "onboarded"}     # settings window and setup assistant
RESERVED_PREFIXES = ("hotkey_", "elevenlabs_", "neural_") + tuple(f"{e}_" for e in LEGACY_ENGINES)

DEFAULT_PREBUFFER_MS = 1500


def parse(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def read(path) -> dict:
    """parse() of a file; {} if it does not exist."""
    p = Path(path)
    return parse(p.read_text(encoding="utf-8")) if p.exists() else {}


def migrate(values: dict, available=lambda engine: False):
    """Map a removed engine in voices.conf to its successor, in memory only.

    engine=<legacy> becomes the successor if available(successor) is true,
    else piper. The legacy voices "<legacy>_<key>" fill the successor's
    voices where those are not set. Returns (values, note); note is '' when
    nothing changed, else a line for the log."""
    old = values.get("engine", "").strip().lower()
    new = LEGACY_ENGINES.get(old)
    if new is None:
        return values, ""
    out = dict(values)
    if available(new):
        out["engine"] = new
        prefix = old + "_"
        for key, val in values.items():
            target = new + "_" + key[len(prefix):]
            if key.startswith(prefix) and val and not out.get(target):
                out[target] = val
        return out, f"engine={old} was removed -> using {new}"
    out["engine"] = "piper"
    return out, f"engine={old} was removed and {new} is not installed -> Piper"


def _int(val, default, minimum=0):
    try:
        return max(minimum, int(val))
    except (TypeError, ValueError):
        return default


@dataclass
class DaemonConfig:
    piper_voices: dict = field(default_factory=dict)    # voice key -> Piper onnx base name
    voice_maps: dict = field(default_factory=dict)      # "edge"/"sapi"/... -> {key: voice}
    engine: str = "piper"
    mssp_cmd: str = ""
    pause_ms: int = 0                                   # silence after each sentence
    lang_detect: str = "text"                           # "text" | "sentence"
    filters: list = field(default_factory=list)
    device: str = "cpu"                                 # "cpu" | "gpu"
    prebuffer_ms: int = DEFAULT_PREBUFFER_MS
    fallback: str = "piper"                             # "piper" | "off"
    read_langs: list = field(default_factory=lambda: list(tts_langs.DEFAULT_READ))
    values: dict = field(default_factory=dict)          # all raw key/values

    def voices(self, engine):
        """Voice map of an engine with a "<engine>_<key>" map, e.g. edge."""
        return self.voice_maps.get(engine, {})

    def prefixed(self, prefix):
        """{rest: value} for all keys "<prefix>_<rest>", e.g. elevenlabs_model."""
        p = prefix + "_"
        return {k[len(p):]: v for k, v in self.values.items() if k.startswith(p)}


def daemon_config(values: dict, neural_engines=()) -> DaemonConfig:
    """Interpret parsed voices.conf values for the daemon.

    Keys without a known prefix are Piper voices (key=onnx base name, usually
    a language code). Keys of the neural engines ("<engine>_<lang>",
    "<engine>_instruct") are left to tts_neural and the daemon's neural block.
    read_langs: see tts_langs.parse_read_langs (missing -> de,en)."""
    cfg = DaemonConfig(values=dict(values),
                       voice_maps={p: {} for p in VOICE_MAP_PREFIXES})
    neural_prefixes = tuple(e + "_" for e in neural_engines)
    for key, val in values.items():
        if key == "engine":
            cfg.engine = val.lower()
        elif key == "device":
            cfg.device = val.lower()
        elif key == "mssp_cmd":
            cfg.mssp_cmd = val
        elif key == "fallback":
            cfg.fallback = "off" if val.lower() in ("off", "none", "aus", "0") else "piper"
        elif key == "prebuffer_ms":
            cfg.prebuffer_ms = _int(val, DEFAULT_PREBUFFER_MS)
        elif key == "lang_detect":
            cfg.lang_detect = val.lower()
        elif key == "filters":
            cfg.filters = [f.strip() for f in val.split(",") if f.strip()]
        elif key == "pause_sentence":
            cfg.pause_ms = _int(val, 0)
        elif key == "read_langs":
            cfg.read_langs = tts_langs.parse_read_langs(val)
        elif key in RESERVED_KEYS or key.startswith(RESERVED_PREFIXES):
            continue
        elif key in neural_engines or key.startswith(neural_prefixes):
            continue
        else:
            prefix = key.split("_", 1)[0]
            if "_" in key and prefix in VOICE_MAP_PREFIXES:
                cfg.voice_maps[prefix][key[len(prefix) + 1:]] = val
            else:
                cfg.piper_voices[key] = val
    return cfg
