"""
tts_settings: the settings window and first-run setup of the tray app.

The interface lives in tts_settings.qml and bin/ui/ (Qt Quick, Material 3).
This module is the Python side: it hands the state of voices.conf and the
voice lists to QML, saves, sends listen requests to the daemon, detects the
PC for the setup assistant and sets up ElevenLabs.

QML keeps the form state and passes it to Backend.saveAsync() as one object.
All texts shown in the window come from the i18n catalogs (tts_i18n), so this
module sends keys and data, not finished sentences, wherever the text has to
follow a live language switch.
"""

import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

import tts_config
import tts_icons
import tts_platform
import tts_theme
import tts_elevenlabs as eleven
import tts_neural
import tts_previews
import tts_i18n
import tts_langs
import tts_rules
from tts_i18n import tr

from PySide6.QtCore import QKeyCombination, QObject, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

HOME = Path.home()
PIPER_DIR = tts_platform.piper_dir()
EL_CACHE_FILE = HOME / ".cache/tts-elevenlabs-voices.json"
QML_FILE = Path(__file__).resolve().parent / "tts_settings.qml"
LANGS = tts_langs.SUPPORTED             # reading languages on offer

# Listen samples stay in the language of the voice, not of the interface.
# The preview cache (tts_previews) holds recordings of exactly these sentences.
SAMPLE = tts_previews.SAMPLE
DEFAULT_EDGE = tts_previews.DEFAULT_EDGE
edge_voices = tts_previews.edge_voices

# Main shortcuts of the setup assistant: (role, Windows action, Linux action).
MAIN_HOTKEYS = [("read", "read", "de"), ("fast", "read_fast", "de-fast"),
                ("pause", "pause", "pause"), ("stop", "stop", None), ("ocr", "ocr", "en")]

# Local neural servers (neural/<key>/). Only what is installed and fits the
# VRAM budget is offered; Fish and Step-Audio need 8 to 10 GB.
NEURAL_INSTRUCT = {"kokoro2": False, "qwen3": True, "chatterbox": True, "fish": True,
                   "stepaudio": True}
NEURAL = [k for k in NEURAL_INSTRUCT if k in tts_neural.usable_engines()]

# Engines in display order: key, online, costs credits. The everyday picks
# come first. Titles, blurbs and menu labels come from the catalog
# (engine.<key>.title|blurb|menu).
ENGINE_INFO = [("elevenlabs", True, True), ("edge", True, False)]
ENGINE_INFO += [(k, False, False) for k in NEURAL if k == "kokoro2"]
ENGINE_INFO.append(("piper", False, False))
ENGINE_INFO += [(k, False, False) for k in NEURAL if k != "kokoro2"]
if tts_platform.IS_WINDOWS:
    ENGINE_INFO.append(("sapi", False, False))
if tts_platform.IS_LINUX:                 # MS Speech Platform via balcon under Wine
    ENGINE_INFO.append(("mssp", False, False))
ENGINE_KEYS = [k for k, *_ in ENGINE_INFO]


def engine_title(key):
    return tr(f"engine.{key}.title", default=tts_neural.title(key))


def engine_menu_label(key):
    return tr(f"engine.{key}.menu", default=engine_title(key))


def engine_caps(key):
    """Capability keys for the chips on cards and voice sections (cap.<key>)."""
    caps = []
    if key in NEURAL:
        if tts_neural.clones(key):
            caps.append("clone")
        if key == "kokoro2":                   # only [word](/IPA/), no emotions
            caps += ["markup", "fast"]
        elif tts_neural.voices_cache(key).get("tags"):
            caps.append("tags")
        if NEURAL_INSTRUCT[key]:
            caps.append({"chatterbox": "expr", "stepaudio": "style"}.get(key, "instruct"))
    elif key == "piper":
        caps.append("fast")
    return caps


# What the voice of an engine is called in voices.conf (Piper: bare language key).
VOICE_KEY = {"edge": "edge_{}", "piper": "{}",
             "sapi": "sapi_{}", "mssp": "mssp_{}"}
VOICE_KEY.update({k: k + "_{}" for k in NEURAL})

# Hand-picked voices for the setup assistant: id, display name, gender, trait.
# Only ids that the engine actually offers are shown.
VOICE_PICKS = {
    "kokoro2": {
        "de": [("kerstin", "Kerstin", "f", "warm"), ("thorsten", "Thorsten", "m", "clear"),
               ("victoria", "Victoria", "f", "bright"), ("martin", "Martin", "m", "calm")],
        "en": [("af_heart", "Heart", "f", "warm"), ("am_michael", "Michael", "m", "calm"),
               ("bf_emma", "Emma", "f", "british"), ("bm_fable", "Fable", "m", "storyteller")],
    },
    "edge": tts_langs.EDGE_PICKS,
}
SPEED_PRESETS = {"relaxed": 1.15, "normal": 1.0, "brisk": 0.85}


# --------------------------------------------------------------------------- #
#  Daemon and voice sources
# --------------------------------------------------------------------------- #
def send(obj: dict) -> bool:
    try:
        s = tts_platform.ipc_connect(timeout=3)
        s.sendall(json.dumps(obj).encode("utf-8"))
        s.shutdown(1)            # SHUT_WR
        s.close()
        return True
    except OSError:
        return False


def piper_voices(lang: str):
    return tts_langs.piper_installed(PIPER_DIR, lang)


def mssp_voices(cmd: str):
    """Voice names from '<cmd> -l' (balcon), [] when that fails."""
    if not cmd:
        return []
    try:
        import shlex
        out = tts_platform.run_hidden(shlex.split(cmd) + ["-l"], capture_output=True,
                                      text=True, timeout=15)
        return [ln.strip() for ln in out.stdout.splitlines()
                if ln.strip() and not ln.rstrip().endswith(":")
                and not ln.lower().startswith(("balcon", "voice"))]
    except Exception:
        return []


def el_cached():
    """Last account check (voices, prices, permissions), so the window is
    complete without network. Older cache formats are dropped."""
    try:
        data = json.loads(EL_CACHE_FILE.read_text(encoding="utf-8"))
        if all(isinstance(v, dict) for v in data.get("voices", [])):
            return data
    except (ValueError, OSError, AttributeError):
        pass
    return {"voices": [], "rates": {}, "perms": {}}


def el_store(result):
    EL_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    EL_CACHE_FILE.write_text(json.dumps({k: result[k] for k in ("voices", "rates", "perms")}),
                             encoding="utf-8")


def cache_info() -> dict:
    n, size = eleven.cache_stats()
    return {"count": n, "mb": round(size / 2**20), "maxMb": eleven.CACHE_MAX_BYTES // 2**20}


def needs_onboarding() -> bool:
    """The setup assistant runs until it has been finished or skipped once."""
    return tts_platform.conf_read().get("onboarded", "") not in ("1", "yes", "true")


def _voice_lists():
    """{engine: {lang: [voice ids]}} for every language the engine speaks."""
    conf = tts_platform.conf_read()
    lists = {
        "edge": edge_voices(),
        "piper": {lang: piper_voices(lang) for lang in LANGS},
    }
    if tts_platform.IS_WINDOWS:
        sapi = tts_platform.sapi_voices()
        lists["sapi"] = {lang: sapi for lang in engine_langs("sapi")}
    if tts_platform.IS_LINUX:
        mssp = mssp_voices(conf.get("mssp_cmd", ""))
        lists["mssp"] = {lang: mssp for lang in tts_langs.engine_langs(
            "mssp", voice_langs_=tts_langs.voice_langs(mssp))}
    for k in NEURAL:
        lists[k] = tts_neural.voice_lists(k)
    return lists


def _sapi_langs():
    try:
        import win_sapi
        return win_sapi.languages()
    except Exception:
        return set()


def engine_langs(key, conf=None):
    """Languages an engine card offers (tts_langs plus what is installed).
    Piper: the languages with an installed voice; others can be downloaded."""
    if key == "piper":
        return [lang for lang in LANGS if piper_voices(lang)]
    if key in NEURAL:
        return list(tts_neural.langs(key))
    if key == "sapi":
        return list(tts_langs.engine_langs("sapi", voice_langs_=_sapi_langs()))
    if key == "mssp":
        cmd = (conf or tts_platform.conf_read()).get("mssp_cmd", "")
        return list(tts_langs.engine_langs("mssp", voice_langs_=tts_langs.voice_langs(
            mssp_voices(cmd))))
    return list(tts_langs.engine_langs(key))


def edge_fallback_ok():
    """Edge can read languages other engines lack (edge_tts and ffmpeg)."""
    try:
        import importlib.util
        return importlib.util.find_spec("edge_tts") is not None and bool(shutil.which("ffmpeg"))
    except Exception:
        return False


def lang_info():
    """Reading languages on offer, with the Piper voice that can be downloaded."""
    return [{"code": c, "native": tts_langs.NATIVE[c], "piper": tts_langs.PIPER_DEFAULT[c],
             "piperMb": tts_langs.PIPER_MB, "edge": tts_langs.edge_default(c)} for c in LANGS]


# --------------------------------------------------------------------------- #
#  PC detection for the setup assistant (read-only, nothing gets started)
# --------------------------------------------------------------------------- #
def _run(cmd, timeout=6):
    try:
        return tts_platform.run_hidden(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _os_name():
    if tts_platform.IS_WINDOWS:
        build = int((platform.version().split(".") + ["0", "0", "0"])[2] or 0)
        return "Windows 11" if build >= 22000 else f"Windows {platform.release()}"
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.system()


def _cpu_name():
    if tts_platform.IS_WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        except OSError:
            return platform.processor()
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor()


def _ram_gb():
    if tts_platform.IS_WINDOWS:
        import ctypes

        class MemStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MemStatus()
        st.dwLength = ctypes.sizeof(MemStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return round(st.ullTotalPhys / 2**30)
        return 0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / 2**20)
    except (OSError, ValueError):
        pass
    return 0


def _other_gpu():
    """Name of a non-NVIDIA graphics chip, where that is cheap to find out."""
    if tts_platform.IS_LINUX and shutil.which("lspci"):
        for line in _run(["lspci"]).splitlines():
            m = re.search(r"(?:VGA compatible|3D|Display) controller: (.+)", line)
            if m:
                name = re.sub(r"\s*\(rev \w+\)$", "", m.group(1)).strip()
                inner = re.search(r"\[(.+)\]", name)  # marketing name, e.g. "Intel Arc Graphics"
                return inner.group(1) if inner else name
    return ""


def detect_hardware() -> dict:
    L = tts_neural.launcher
    nvidia = bool(L and L.has_nvidia())
    gpu = {"nvidia": nvidia, "name": "", "totalMb": 0, "freeMb": 0}
    if nvidia:
        out = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
                    "--format=csv,noheader,nounits"])
        try:
            name, total, free = [x.strip() for x in out.splitlines()[0].split(",")]
            gpu.update(name=name, totalMb=int(total), freeMb=int(free))
        except (IndexError, ValueError):
            gpu["freeMb"] = (L.vram_free_mb() or 0) if L else 0
    else:
        gpu["name"] = _other_gpu()
    return {"os": _os_name(), "linux": tts_platform.IS_LINUX, "cpu": _cpu_name(),
            "cores": os.cpu_count() or 0, "ramGb": _ram_gb(), "gpu": gpu,
            "budgetMb": L.budget_mb() if L else 0}


def install_command(key):
    """Shell command that installs a neural model, '' when there is none for
    this OS. The app never runs it itself."""
    if tts_neural.NEURAL_DIR is None or key not in tts_neural.REGISTRY:
        return ""
    d = tts_neural.NEURAL_DIR / tts_neural.REGISTRY[key]["dir"]
    if tts_platform.IS_WINDOWS and (d / "setup.ps1").exists():
        return f'powershell -ExecutionPolicy Bypass -File "{d / "setup.ps1"}"'
    if not tts_platform.IS_WINDOWS and (d / "setup.sh").exists():
        return f'bash "{d / "setup.sh"}"'
    return ""


def _install_list(nvidia):
    """Neural models that are not installed yet, with what they need."""
    out = []
    L = tts_neural.launcher
    for key, spec in tts_neural.REGISTRY.items():
        if tts_neural.installed(key):
            continue
        need = int(spec.get("vram_mb") or 0)
        cpu_ok = bool(spec.get("cpu_ok"))
        if L and need > L.budget_mb() and not (cpu_ok and not nvidia):
            continue                     # blocked by the GPU guard anyway
        out.append({"key": key, "title": engine_title(key), "vramMb": need,
                    "diskMb": int(spec.get("disk_mb") or 0), "cpuOk": cpu_ok,
                    "fits": cpu_ok or nvidia, "clone": bool(spec.get("clone")),
                    "command": install_command(key)})
    return out


def _split_camel(s):
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)


def voice_label(vid, refs=None):
    """Readable name for a voice id: "de_DE-thorsten-low" -> "Thorsten (low)",
    "en-US-AriaNeural" -> "Aria (en-US)", "af_heart" -> "Heart (US, female)"."""
    if vid in ("builtin", "default"):
        return tr(f"voice.{vid}")
    if vid.startswith("ref:"):
        return (refs or {}).get(vid[4:]) or vid[4:].replace("_", " ").capitalize()
    m = re.fullmatch(r"([a-z]{2})_[A-Z]{2}-(.+)-(x_low|low|medium|high)", vid)
    if m:                                                  # Piper
        return f"{m.group(2).replace('_', ' ').title()} ({tr('voice.quality.' + m.group(3))})"
    m = re.fullmatch(r"([a-z]{2}-[A-Z]{2})-(\w+?)Neural", vid)
    if m:                                                  # Edge
        return f"{_split_camel(m.group(2))} ({m.group(1)})"
    m = re.fullmatch(r"([ab])([fm])_([a-z]+)", vid)
    if m:                                                  # Kokoro
        region = "US" if m.group(1) == "a" else "UK"
        return f"{m.group(3).capitalize()} ({region}, {tr('gender.' + m.group(2))})"
    if re.fullmatch(r"[a-z][a-z0-9_]*", vid):
        return vid.replace("_", " ").capitalize()
    return vid.replace("_", " ")


def _ref_names():
    try:
        import tts_voices
        return {v["id"]: v["name"] for v in tts_voices.list_voices()}
    except Exception:
        return {}


def _picks(engine, lists):
    """Voices to offer in the setup assistant, per reading language."""
    out = {}
    refs = _ref_names()
    for lang in LANGS:
        have = (lists.get(engine) or {}).get(lang) or []
        curated = [{"id": i, "name": n, "gender": g, "trait": t}
                   for i, n, g, t in VOICE_PICKS.get(engine, {}).get(lang, []) if i in have]
        if not curated:
            curated = [{"id": i, "name": voice_label(i, refs), "gender": "", "trait": ""}
                       for i in have[:6]]
        out[lang] = curated
    return out


def main_hotkeys():
    """Shortcuts for the setup assistant: [{role, action, combo}] in display
    order. OCR only where the platform can recognize screen text."""
    have = {a for a, _lbl in tts_platform.HOTKEY_ACTIONS}
    out = []
    for role, win_action, linux_action in MAIN_HOTKEYS:
        action = win_action if tts_platform.IS_WINDOWS else linux_action
        if not action or action not in have:
            continue
        if role == "ocr" and not tts_platform.ocr_supported():
            continue
        out.append({"role": role, "action": action, "combo": tts_platform.read_hotkey(action)})
    return out


def hotkey_issue(combo):
    """Why a combo cannot work as a global shortcut: '' (fine), 'noModifier'
    (would catch normal typing) or 'taken' (another program holds it;
    Windows only, where it can be checked)."""
    parts = [p for p in combo.split("+") if p]
    if not parts:
        return ""
    mods = {p.lower() for p in parts[:-1]}
    key = parts[-1]
    if not mods & {"ctrl", "alt", "meta"} and not (key.startswith("F") and key[1:].isdigit()):
        return "noModifier"
    if tts_platform.IS_WINDOWS:
        try:
            import ctypes
            import win_hotkeys
            m, vk = win_hotkeys._parse(combo)
            if vk is None:
                return ""
            user32 = ctypes.windll.user32
            probe_id = 0xBFF0
            if not user32.RegisterHotKey(None, probe_id, m | win_hotkeys.MOD_NOREPEAT, vk):
                return "taken"
            user32.UnregisterHotKey(None, probe_id)
        except Exception:
            return ""
    return ""


def recommend(hw):
    """Engine for someone who just wants it to work: the fast local Kokoro if
    it is installed (fine on any CPU), else the free online Edge voices."""
    if "kokoro2" in NEURAL:
        return "kokoro2"
    return "edge"


# --------------------------------------------------------------------------- #
#  Backend for QML
# --------------------------------------------------------------------------- #
class Backend(QObject):
    elVerified = Signal("QVariantMap")
    elFailed = Signal(str)
    keyDetected = Signal(str)
    edgeLoaded = Signal("QVariantMap")
    selectEngine = Signal(str)                 # tray -> window (for example ElevenLabs)
    openOnboarding = Signal()                  # tray -> window
    recTick = Signal(float, float)             # recording: seconds, level 0..1
    saved = Signal(bool, str)                  # saveAsync finished: ok, message
    recAutoStopped = Signal()                  # recording ended by itself (error or max length)
    hardwareReady = Signal("QVariantMap")
    piperProgress = Signal(str, int, int)     # Piper download: lang, bytes done, bytes total
    piperDone = Signal(str, bool, str)        # lang, ok, voice name or error text
    rulesChanged = Signal()                    # rules.json changed (window, CLI, agent)

    def __init__(self):
        super().__init__()
        self._rec = None
        self._rec_kind = "mic"
        self._pending = None
        self._rec_timer = QTimer(self)
        self._rec_timer.setInterval(100)
        self._rec_timer.timeout.connect(self._rec_poll)
        self._waiting_for_key = False
        self._setup_open = False
        self._tried = ""
        QGuiApplication.clipboard().dataChanged.connect(self.checkClipboard)
        self._rules = tts_rules.RuleStore()
        tts_rules.migrate(self._rules)
        self._rules_sig = self._rules.signature()
        self._rules_timer = QTimer(self)          # picks up changes from the command line
        self._rules_timer.setInterval(1500)
        self._rules_timer.timeout.connect(self._rules_poll)
        self._rules_timer.start()
        self._downloads = {}                      # lang -> cancel flag (list)

    # --- Initial state --------------------------------------------------- #
    @Slot(result="QVariantMap")
    def initial(self):
        conf, _ = tts_config.migrate(tts_platform.conf_read(), lambda k: k in NEURAL)
        lists = _voice_lists()
        current = {eng: {lang: conf.get(fmt.format(lang), "") for lang in LANGS}
                   for eng, fmt in VOICE_KEY.items() if eng in lists}
        cached = el_cached()
        engine = conf.get("engine", "piper")
        return {
            "engine": engine if engine in ENGINE_KEYS else "piper",
            "langDetect": "sentence" if conf.get("lang_detect") == "sentence" else "text",
            "speedNormal": float(conf.get("speed_normal", 1.0) or 1.0),
            "speedFast": float(conf.get("speed_fast", 0.8) or 0.8),
            "pause": int(conf.get("pause_sentence", 0) or 0),
            "follow": conf.get("follow_window", "on").lower() not in ("off", "0", "no", "false", "aus"),
            "followOpacity": int(conf.get("follow_opacity", 100) or 100),
            "inprocHotkeys": tts_platform.INPROC_HOTKEYS,
            "linux": tts_platform.IS_LINUX,
            "version": tts_platform.app_version(),
            "onboarding": needs_onboarding(),
            "readLangs": tts_langs.parse_read_langs(conf.get("read_langs")),
            "systemLangs": tts_langs.system_read_langs(),
            "langs": lang_info(),
            "edgeFallback": edge_fallback_ok(),
            "engines": [{"key": k, "online": on, "paid": paid,
                         "title": tts_neural.title(k) if k in NEURAL else "",
                         "caps": engine_caps(k), "langs": engine_langs(k, conf)}
                        for k, on, paid in ENGINE_INFO],
            "labels": self.voiceLabels(lists),
            "lists": lists,
            "current": current,
            "neural": [{"key": k, "instruct": NEURAL_INSTRUCT[k],
                        "clone": tts_neural.clones(k),
                        "tags": (tts_neural.voices_cache(k).get("tags") or [])[:16]}
                       for k in NEURAL],
            "instructs": {k: conf.get(f"{k}_instruct", "") for k in NEURAL},
            "hotkeys": [{"action": a, "combo": tts_platform.read_hotkey(a)}
                        for a, _lbl in tts_platform.HOTKEY_ACTIONS],
            "el": {
                "hasKey": bool(eleven.load_key()),
                "model": conf.get("elevenlabs_model") or eleven.DEFAULT_MODEL,
                "cache": conf.get("elevenlabs_cache", "on") != "off",
                "voices": cached["voices"],
                "perms": cached.get("perms", {}),
                "permList": [{"key": k, "area": a, "level": lv, "required": req, "why": why}
                             for k, a, lv, req, why in eleven.PERMISSIONS],
                "keyUrl": eleven.KEY_URL,
                "current": {lang: conf.get(f"elevenlabs_{lang}", "") for lang in LANGS},
                "rates": cached.get("rates", {}),
                "models": eleven.MODELS,
                "charsPerHour": eleven.CHARS_PER_MINUTE * 60,
                "cacheStats": cache_info(),
            },
        }

    @Slot(result=str)
    def themeOverride(self):
        """'light' or 'dark' from TTS_UI_THEME (screenshots, tests), else ''."""
        v = os.environ.get("TTS_UI_THEME", "").lower()
        return v if v in ("light", "dark") else ""

    @Slot(result=bool)
    def systemDark(self):
        """Fallback when Qt does not know the color scheme (KDE with pip Qt)."""
        return tts_theme.is_dark()

    # --- Setup assistant ------------------------------------------------- #
    @Slot(str, result="QVariantList")
    def systemReadLangs(self, ui_lang):
        """Reading languages to preselect: the app language plus English."""
        return tts_langs.system_read_langs(ui_lang)

    @Slot()
    def detectHardware(self):
        """PC check in the background (nvidia-smi can take a moment)."""
        def run():
            try:
                hw = detect_hardware()
            except Exception:
                hw = {"os": platform.system(), "cpu": "", "cores": os.cpu_count() or 0,
                      "ramGb": 0, "gpu": {"nvidia": False, "name": ""}, "budgetMb": 0}
            self.hardwareReady.emit(self.onboardingInfo(hw))
        threading.Thread(target=run, daemon=True).start()

    @Slot("QVariantMap", result="QVariantMap")
    def onboardingInfo(self, hw):
        lists = _voice_lists()
        nvidia = bool((hw.get("gpu") or {}).get("nvidia"))
        rec = recommend(hw)
        choices = [rec] + [k for k in ("kokoro2", "edge", "qwen3", "chatterbox", "piper")
                           if k != rec and k in ENGINE_KEYS]
        return {
            "hw": hw,
            "recommended": rec,
            "engines": [{"key": k, "online": k == "edge", "caps": engine_caps(k),
                         "langs": engine_langs(k),
                         "clone": k in NEURAL and tts_neural.clones(k),
                         "vramMb": int(tts_neural.REGISTRY.get(k, {}).get("vram_mb") or 0)
                         if k in NEURAL and nvidia else 0,
                         "picks": _picks(k, lists)} for k in choices],
            "install": _install_list(nvidia),
            "hotkeys": [{"action": a, "combo": tts_platform.read_hotkey(a)}
                        for a, _lbl in tts_platform.HOTKEY_ACTIONS],
            "mainHotkeys": main_hotkeys(),
            "speeds": SPEED_PRESETS,
        }

    @Slot("QVariantMap")
    def finishOnboarding(self, s):
        """Write what the assistant collected and restart the daemon, in the
        background; the result arrives as saved(ok, text)."""
        def run():
            try:
                updates = {"onboarded": "1"}
                eng = s.get("engine", "")
                if eng in ENGINE_KEYS:
                    updates["engine"] = eng
                    fmt = "elevenlabs_{}" if eng == "elevenlabs" else VOICE_KEY.get(eng)
                    for lang, vid in (s.get("voices") or {}).items():
                        if vid and lang in LANGS and fmt:
                            updates[fmt.format(lang)] = vid
                    if eng == "elevenlabs" and s.get("elModel"):
                        updates["elevenlabs_model"] = s["elModel"]
                if s.get("speed"):
                    sp = float(s["speed"])
                    updates["speed_normal"] = f"{sp:.2f}"
                    updates["speed_fast"] = f"{sp * 0.8:.2f}"
                langs = [x for x in (s.get("readLangs") or []) if x in LANGS]
                if langs:
                    updates["read_langs"] = tts_langs.format_read_langs(langs)
                tts_platform.conf_write(updates)
                keys = s.get("hotkeys") or {}
                known = {a for a, _lbl in tts_platform.HOTKEY_ACTIONS}
                for action, combo in keys.items():
                    if action in known:
                        tts_platform.write_hotkey(action, combo)
                # Windows re-registers when the window closes (windowVisible)
                if keys and not tts_platform.INPROC_HOTKEYS:
                    tts_platform.apply_hotkeys()
                if len(updates) > 1:
                    tts_platform.restart_daemon()
                self.saved.emit(True, tr("onb.saved"))
            except Exception as exc:
                self.saved.emit(False, tr("save.failed", error=str(exc)))
        threading.Thread(target=run, daemon=True).start()

    @Slot(str)
    def copyText(self, text):
        QGuiApplication.clipboard().setText(text)

    # --- Listening ------------------------------------------------------- #
    @Slot("QVariantMap", result=str)
    def testInfo(self, req):
        """Hint before listening: '' or a snackbar text (the model has to
        load first, or cannot run on this PC)."""
        eng = req.get("engine", "")
        if eng not in tts_neural.ENGINES or not tts_neural.launcher:
            return ""
        L = tts_neural.launcher
        if L.healthy(L.port_of(eng), 0.3):
            return ""
        name = engine_title(eng)
        if not L.has_nvidia() and not tts_neural.REGISTRY[eng].get("cpu_ok"):
            return tr("test.needsNvidia", name=name)
        need, budget = L.need_mb(eng), L.budget_mb()
        if need > budget:
            return tr("test.overBudget", name=name, need=tts_i18n.fmt_num(need / 1024, 1),
                      budget=tts_i18n.fmt_num(budget / 1024, 1))
        return tr("test.loading", name=name)

    @Slot("QVariantMap", result=str)
    def previewUrl(self, req):
        """file: URL of a cached recording of this listen request, '' if
        there is none (then test() plays it live and the daemon caches it)."""
        f = tts_previews.from_request(dict(req))
        p = tts_previews.lookup(**f) if f else None
        return QUrl.fromLocalFile(str(p)).toString() if p else ""

    @Slot()
    def stopSpeech(self):
        """Stop what the daemon is reading (before a cached preview plays)."""
        send({"cmd": "stop"})

    @Slot("QVariantMap", result=bool)
    def test(self, req):
        """req: engine, lang, voice, scale, optional text/pauseMs/model/instruct."""
        if not req.get("voice"):
            return False
        req = dict(req)
        lang = req.get("lang", "de")
        msg = {"cmd": "test", "engine": req["engine"], "voice": req["voice"],
               "text": req.get("text") or tts_langs.sample(lang),
               "length_scale": float(req.get("scale", 1.0)), "key": lang}
        if req.get("pauseMs") is not None:
            msg["pause_ms"] = int(req["pauseMs"])
        if req.get("model"):
            msg["model"] = req["model"]
        if req.get("instruct") is not None:
            msg["instruct"] = req["instruct"]
        if tts_previews.from_request(req) is not None:
            msg["cache"] = True                  # the daemon keeps a complete render
        if send(msg):
            return True
        if tts_platform.TRAY_MANAGES_DAEMON:
            tts_platform.ensure_daemon_running()
        return False

    @Slot(str, result=str)
    def pauseSample(self, lang):
        return tts_langs.sample_pause(lang)

    # --- Saving ---------------------------------------------------------- #
    @Slot("QVariantMap")
    def saveAsync(self, s):
        """Like save(), but the daemon restart (a few seconds) runs in the
        background; the window stays usable, the result comes as saved()."""
        def run():
            try:
                self.saved.emit(True, self.save(s))
            except Exception as exc:
                self.saved.emit(False, tr("save.failed", error=str(exc)))
        threading.Thread(target=run, daemon=True).start()

    @Slot("QVariantMap", result=str)
    def save(self, s):
        updates = {
            "engine": s["engine"],
            "lang_detect": s["langDetect"],
            "pause_sentence": str(int(s["pause"])),
            "speed_normal": f"{float(s['speedNormal']):.2f}",
            "speed_fast": f"{float(s['speedFast']):.2f}",
            "elevenlabs_model": s["el"]["model"],
            "elevenlabs_cache": "on" if s["el"]["cache"] else "off",
        }
        if "follow" in s:
            updates["follow_window"] = "on" if s["follow"] else "off"
        if "followOpacity" in s:
            updates["follow_opacity"] = str(int(round(float(s["followOpacity"]))))
        langs = [x for x in (s.get("readLangs") or []) if x in LANGS]
        if langs:
            updates["read_langs"] = tts_langs.format_read_langs(langs)
        for lang in LANGS:
            if s["el"].get(lang):
                updates[f"elevenlabs_{lang}"] = s["el"][lang]
            for eng, fmt in VOICE_KEY.items():
                val = (s["voices"].get(eng) or {}).get(lang, "").strip()
                if val:
                    updates[fmt.format(lang)] = val
        for eng, txt in (s.get("instructs") or {}).items():
            if eng in NEURAL:
                updates[f"{eng}_instruct"] = str(txt).strip()
        tts_platform.conf_write(updates)

        for action, combo in s["hotkeys"].items():
            tts_platform.write_hotkey(action, combo)
        if not tts_platform.INPROC_HOTKEYS:
            tts_platform.apply_hotkeys()  # Linux: reload kglobalaccel
        # Windows: hotkeys are re-registered when the window closes, otherwise
        # the freshly set combos would fire while it is open.
        tts_platform.restart_daemon()
        if s["engine"] == "elevenlabs" and not eleven.load_key():
            return tr("save.elNoKey")
        return tr("save.doneOnClose" if tts_platform.INPROC_HOTKEYS else "save.done")

    # --- Hotkeys --------------------------------------------------------- #
    @Slot(int, int, result=str)
    def keyCombo(self, key, mods):
        """Key press from QML -> Qt portable text ('' for bare modifiers)."""
        if key in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta,
                   Qt.Key_AltGr, Qt.Key_unknown):
            return ""
        mods &= int(Qt.ShiftModifier | Qt.ControlModifier | Qt.AltModifier
                    | Qt.MetaModifier)
        combo = QKeyCombination(Qt.KeyboardModifier(mods), Qt.Key(key))
        return QKeySequence(combo).toString(QKeySequence.PortableText)

    @Slot(str, result=str)
    def hotkeyIssue(self, combo):
        """'' | 'noModifier' | 'taken' for a freshly recorded combo."""
        return hotkey_issue(combo)

    @Slot(bool)
    def windowVisible(self, visible):
        # While the window is open, global hotkeys are paused, otherwise
        # recording a combo would trigger its action (RegisterHotKey grabs it).
        if tts_platform.INPROC_HOTKEYS:
            if visible:
                tts_platform.suspend_hotkeys()
            else:
                tts_platform.apply_hotkeys()

    # --- Text rules (tts_rules) -------------------------------------------- #
    def _rules_poll(self):
        sig = self._rules.signature()
        if sig != self._rules_sig:
            self._rules_sig = sig
            self.rulesChanged.emit()

    def _rule_error(self, exc):
        """Translated message for a tts_rules error."""
        if isinstance(exc, tts_rules.RuleError):
            return tr(f"rules.err.{exc.code}", detail=exc.detail, default=str(exc))
        if isinstance(exc, tts_rules.NotFound):
            return tr("rules.err.notFound")
        return tr("rules.err.store", detail=str(exc))

    def _rules_done(self, fn):
        """Run a change; {"ok", "error", ...state} for QML."""
        try:
            extra = fn() or {}
            ok, err = True, ""
        except Exception as exc:
            extra, ok, err = {}, False, self._rule_error(exc)
        self._rules_sig = self._rules.signature()
        return {**self.rulesState(), **extra, "ok": ok, "error": err}

    @Slot(result="QVariantMap")
    def rulesState(self):
        """Rules for the list (with problem and changed fields), the groups
        with their switch and counts, and the file state."""
        try:
            rules, error = self._rules.load(), ""
        except tts_rules.StoreError as exc:
            rules, error = tts_rules.RuleList(), tr("rules.err.store", detail=str(exc))
        reg = self._rules.code_rules()
        for r in rules:
            r["problem"] = self._problem(r, reg)
            r["changed"] = tts_rules.preset_changes(r)
        state = tts_rules.group_state(rules)
        groups = []
        for g, on in state.items():
            mine = [r for r in rules if r.get("group") == g]
            groups.append({"key": g, "on": on, "count": len(mine),
                           "onCount": sum(1 for r in mine if r.get("enabled", True)),
                           "preset": g in tts_rules.GROUPS,
                           "missing": len(tts_rules.missing_presets(rules, g))
                           if g in tts_rules.GROUPS else 0,
                           "changed": sum(1 for r in mine if r["changed"])})
        restorable = any(x["missing"] or x["changed"] for x in groups)
        return {"rules": list(rules), "groups": groups, "error": error, "canRestore": restorable,
                "canUndo": self._rules.can_undo(), "path": str(self._rules.path),
                "toReview": sum(1 for r in rules if not r.get("reviewed", True))}

    def _problem(self, rule, reg=None):
        """Translated reason why a rule is skipped, '' when it works."""
        try:
            tts_rules.validate(dict(rule), reg if reg is not None else self._rules.code_rules())
            return ""
        except tts_rules.RuleError as exc:
            return self._rule_error(exc)

    @Slot("QVariantMap", result=str)
    def ruleCheck(self, fields):
        """'' when the rule is valid, else a translated message."""
        return self._problem(fields)

    @Slot("QVariantMap", result="QVariantMap")
    def ruleSave(self, fields):
        """Add (no id) or change a rule; changes from the window count as reviewed."""
        f = dict(fields)
        rid = f.pop("id", "")
        if rid:
            return self._rules_done(lambda: {"id": self._rules.update(rid, f, by="user")["id"]})
        return self._rules_done(lambda: {"id": self._rules.add(f, by="user")["id"]})

    @Slot(str, bool, result="QVariantMap")
    def ruleToggle(self, rid, on):
        return self._rules_done(lambda: {"id": self._rules.set_enabled(rid, on, by="user")["id"]})

    @Slot(str, int, result="QVariantMap")
    def ruleMove(self, rid, to):
        return self._rules_done(lambda: {"position": self._rules.move(rid, to)})

    @Slot(str, result="QVariantMap")
    def ruleRemove(self, rid):
        return self._rules_done(lambda: {"removed": self._rules.remove(rid)})

    @Slot(str, bool, result="QVariantMap")
    def ruleGroupToggle(self, group, on):
        """Switch a whole group; its rules keep their own switches."""
        return self._rules_done(lambda: {"group": group, "on": self._rules.set_group(group, on)[group]})

    @Slot(str, result="QVariantMap")
    def ruleReset(self, rid):
        """A changed preset back to how it came with the app."""
        return self._rules_done(lambda: {"id": self._rules.reset(rid, by="user")["id"]})

    @Slot(str, result="QVariantMap")
    def rulesRestore(self, group):
        """Deleted presets back and changed ones reset; group '' = all groups."""
        def run():
            done = self._rules.restore(group or None, by="user")
            return {"added": len(done["added"]), "reset": len(done["reset"])}
        return self._rules_done(run)

    @Slot(str, result="QVariantMap")
    def ruleReview(self, rid):
        return self._rules_done(lambda: {"id": self._rules.review(rid)["id"]})

    @Slot(result="QVariantMap")
    def rulesUndo(self):
        def run():
            if not self._rules.undo():
                raise tts_rules.StoreError(tr("rules.nothingToUndo"))
        return self._rules_done(run)

    @Slot(str, str, result="QVariantMap")
    def rulesTry(self, text, lang):
        """What reading would make of text: the whole list, presets included.
        before/after are runs [{text, rule}] for highlighting."""
        try:
            rules = self._rules.load()
        except tts_rules.StoreError:
            rules = tts_rules.RuleList()
        return tts_rules.trace(text, lang or None, rules, reg=self._rules.code_rules())

    @Slot("QVariantMap", str, str, result="QVariantMap")
    def ruleTryOne(self, fields, text, lang):
        """Preview of a single rule that is not saved yet (rule dialog)."""
        rule = {**dict(fields), "id": "preview", "enabled": True}
        if self.ruleCheck(rule):
            return {"output": text, "fired": [], "before": [], "after": []}
        t = tts_rules.trace(text, lang or None, [rule], reg=self._rules.code_rules())
        return {k: t[k] for k in ("output", "fired", "before", "after")}

    @Slot(result=str)
    def agentPrompt(self):
        return tts_rules.agent_prompt(self._rules.path)

    # --- Own voices (cloning) -------------------------------------------- #
    def _clone_lists(self):
        return {k: tts_neural.voice_lists(k) for k in NEURAL if tts_neural.clones(k)}

    @Slot(result="QVariantMap")
    def voiceDevices(self):
        try:
            import tts_voices
            return tts_voices.devices()
        except Exception as exc:
            return {"mic": [], "pc": [], "error": str(exc)}

    @Slot(str, str, result=str)
    def recordStart(self, kind, device):
        """Start recording ('mic' | 'pc'); '' = ok, else an error text."""
        import tts_voices
        self.recordCancel()
        try:
            self._rec = tts_voices.Recorder(kind, device)
            self._rec.start()
        except Exception as exc:
            self._rec = None
            return tr("voice.recFailed", error=str(exc))
        self._rec_kind = kind
        self._rec_timer.start()
        return ""

    def _rec_poll(self):
        r = getattr(self, "_rec", None)
        if r is None:
            self._rec_timer.stop()
            return
        self.recTick.emit(float(r.seconds), float(r.level))
        if not r.thread.is_alive():          # error or maximum length reached
            self._rec_timer.stop()
            self.recAutoStopped.emit()

    @Slot(result="QVariantMap")
    def recordStop(self):
        r = getattr(self, "_rec", None)
        self._rec_timer.stop()
        self._rec = None
        if r is None:
            return {"ok": False, "notes": [tr("voice.noRecording")]}
        try:
            raw = r.stop()
        except Exception as exc:
            return {"ok": False, "notes": [str(exc)]}
        src = tr("voice.srcMic" if self._rec_kind == "mic" else "voice.srcPc")
        return self._take(raw, f"{src} ({r.device_name or tr('voice.defaultDevice')})")

    @Slot()
    def recordCancel(self):
        r = getattr(self, "_rec", None)
        self._rec = None
        self._rec_timer.stop()
        if r is not None:
            try:
                r.stop()
            except Exception:
                pass

    @Slot(str, result="QVariantMap")
    def importFile(self, url):
        import tts_voices
        path = QUrl(url).toLocalFile() if url.startswith("file:") else url
        try:
            raw = tts_voices.decode_file(path)
        except Exception as exc:
            return {"ok": False, "notes": [str(exc)]}
        return self._take(raw, tr("voice.srcFile", name=Path(path).name))

    def _take(self, raw, source):
        """Clean up and check a recording. Until it is saved it stays a
        temporary WAV file, so QML can play it with its media player."""
        import tts_voices
        self.discardPending()
        try:
            y = tts_voices.prepare(raw)
        except Exception as exc:
            return {"ok": False, "notes": [str(exc)]}
        ok, notes = tts_voices.check(raw, y)
        tmp = tts_voices.tmp_dir() / f"aufnahme_{int(time.time() * 1000)}.wav"
        tts_voices.write_wav(tmp, y)
        self._pending = (y, source, tmp)
        return {"ok": ok, "notes": notes, "seconds": round(len(y) / tts_voices.SR, 1),
                "source": source, "url": QUrl.fromLocalFile(str(tmp)).toString()}

    @Slot()
    def discardPending(self):
        """Throw the recording away (deletes the temporary file)."""
        p = getattr(self, "_pending", None)
        self._pending = None
        if p is not None:
            try:
                p[2].unlink()
            except OSError:
                pass

    @Slot(str, str, result="QVariantMap")
    def savePending(self, name, lang):
        import tts_voices
        p = getattr(self, "_pending", None)
        if p is None:
            return {"error": tr("voice.noRecording")}
        vid = tts_voices.save(name, lang, p[0], p[1])
        self.discardPending()
        return {"id": vid, "lists": self._clone_lists()}

    @Slot(result="QVariantMap")
    def voiceLabels(self, lists=None):
        """Voice id -> readable name for the pickers ("ref:<id>" from the
        voices folder, plus fixed ids of the models)."""
        refs = _ref_names()
        out = {f"ref:{k}": v for k, v in refs.items()}
        for per_lang in (lists or _voice_lists()).values():
            for ids in per_lang.values():
                for vid in ids:
                    out.setdefault(vid, voice_label(vid, refs))
        return out

    @Slot(result="QVariantList")
    def voiceList(self):
        import tts_voices
        out = tts_voices.list_voices()
        for v in out:
            v["url"] = QUrl.fromLocalFile(
                str(tts_voices.voices_dir() / f"{v['id']}.wav")).toString()
        return out

    @Slot(str, result="QVariantMap")
    def deleteVoice(self, vid):
        """Delete; settings that pointed at the voice go back to the default."""
        import tts_voices
        keys = tts_voices.delete(vid)
        if keys:
            tts_platform.conf_write({k: "" for k in keys})
        return {"reset": keys, "lists": self._clone_lists()}

    # --- Piper voices for new reading languages ---------------------------- #
    @Slot(str, result=bool)
    def downloadPiper(self, lang):
        """Download the curated Piper voice of a language in the background;
        piperProgress while it runs, piperDone at the end. False if one for
        this language already runs or the language is unknown."""
        voice = tts_langs.PIPER_DEFAULT.get(lang)
        if not voice or lang in self._downloads:
            return False
        flag = []
        self._downloads[lang] = flag

        def run():
            last = [0.0]

            def progress(done, total):
                now = time.monotonic()
                if now - last[0] > 0.15 or done == total:
                    last[0] = now
                    self.piperProgress.emit(lang, done, total)
            try:
                if not tts_langs.piper_disk_ok(PIPER_DIR):
                    raise OSError(tr("piper.noSpace"))
                tts_langs.download_piper_voice(voice, PIPER_DIR, progress=progress,
                                               cancel=lambda: bool(flag))
                self.piperDone.emit(lang, True, voice)
            except Exception as exc:
                self.piperDone.emit(lang, False, "" if flag else str(exc))
            finally:
                self._downloads.pop(lang, None)
        threading.Thread(target=run, daemon=True).start()
        return True

    @Slot(str)
    def cancelPiper(self, lang):
        flag = self._downloads.get(lang)
        if flag is not None:
            flag.append(True)

    @Slot(result="QVariantMap")
    def piperLists(self):
        """Installed Piper voices per language, after a download."""
        lists = {lang: piper_voices(lang) for lang in LANGS}
        return {"lists": lists, "labels": self.voiceLabels({"piper": lists})}

    @Slot()
    def refreshEdge(self):
        def go():
            self.edgeLoaded.emit(edge_voices(refresh=True))
        threading.Thread(target=go, daemon=True).start()

    # --- ElevenLabs ------------------------------------------------------ #
    @Slot()
    def openUrl(self):
        """Open the key page without waiting for a new key (change permissions)."""
        webbrowser.open(eleven.KEY_URL)

    @Slot()
    def openKeyPage(self):
        self._waiting_for_key = True
        webbrowser.open(eleven.KEY_URL)

    @Slot(bool)
    def setSetupOpen(self, on):
        self._setup_open = on

    @Slot()
    def checkClipboard(self):
        """Report a copied key. Runs on dataChanged (X11, Windows) and when the
        window gets active again: on Wayland an unfocused window does not
        hear about the clipboard."""
        if not (self._waiting_for_key or self._setup_open):
            return
        text = QGuiApplication.clipboard().text().strip()
        if eleven.looks_like_key(text) and text != self._tried:
            self._tried = text
            self._waiting_for_key = False
            self.keyDetected.emit(text)

    @Slot(str, bool)
    def verifyKey(self, key, quiet):
        """Check a key: every needed permission, plus voices, prices and the
        balance. Runs in the background, result via elVerified / elFailed.
        The key is stored even with missing permissions, so after enabling
        them on the website one click on "Check again" is enough."""
        def go():
            try:
                r = eleven.check_account(key)
            except eleven.ElevenLabsError as exc:
                self.elFailed.emit(str(exc))
                return
            first = eleven.load_key() != key
            eleven.save_key(key)
            el_store(r)
            self.elVerified.emit({
                "perms": r["perms"], "sub": r["sub"] or {}, "voices": r["voices"],
                "rates": r["rates"], "defaultVoice": eleven.pick_default_voice(r["voices"]),
                "first": first, "quiet": quiet,
            })
        threading.Thread(target=go, daemon=True).start()

    @Slot(result=str)
    def storedKey(self):
        return eleven.load_key()

    @Slot(result="QVariantMap")
    def clearCache(self):
        eleven.cache_clear()
        return cache_info()


# --------------------------------------------------------------------------- #
#  Window
# --------------------------------------------------------------------------- #
def init_style():
    """Call before the QML engine is created (once per process)."""
    QQuickStyle.setStyle("Material")


class SettingsWindow:
    """Holds engine and backend; the tray calls show()/select_engine()."""

    def __init__(self):
        self.engine = QQmlApplicationEngine()
        # Backend as a child of the engine: it is destroyed AFTER the QML tree,
        # otherwise QML sees "backend = null" during teardown.
        self.backend = Backend()
        self.backend.setParent(self.engine)
        ctx = self.engine.rootContext()
        ctx.setContextProperty("backend", self.backend)
        ctx.setContextProperty("i18n", tts_i18n.bridge())
        logo = tts_icons.logo_path()        # sidebar mark; "" keeps the glyph tile
        ctx.setContextProperty("brandLogo", QUrl.fromLocalFile(str(logo)).toString() if logo else "")
        self.engine.load(QUrl.fromLocalFile(str(QML_FILE)))
        roots = self.engine.rootObjects()
        if not roots:
            raise RuntimeError(f"QML could not be loaded: {QML_FILE}")
        self.window = roots[0]
        self.window.setIcon(tts_icons.app_icon())

    def isVisible(self):
        return self.window.isVisible()

    def show(self):
        self.window.show()
        self.window.raise_()
        self.window.requestActivate()

    def select_engine(self, key):
        self.backend.selectEngine.emit(key)

    def open_onboarding(self):
        self.backend.openOnboarding.emit()

    def close(self):
        self.window.close()
        self.engine.deleteLater()
