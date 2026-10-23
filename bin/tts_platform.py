# -*- coding: utf-8 -*-
"""
Platform layer for Recito.

Holds the OS-specific parts so that daemon, client and tray run on Linux and
Windows (and in principle macOS):
  - paths        (~/.local/share vs. %APPDATA%/%LOCALAPPDATA%)
  - IPC          (Unix socket vs. TCP on localhost)
  - audio        (aplay + signals vs. sounddevice)
  - selection    (wl-paste/PRIMARY vs. clipboard)
  - config       (voices.conf read/write, comments preserved)
  - daemon       (systemd user service vs. process owned by the tray)
  - hotkeys      (KDE shortcuts vs. global hotkeys in the tray)
  - autostart    (XDG autostart vs. Startup folder shortcut)
  - OCR          (slurp/spectacle vs. Qt overlay), see win_ocr.py
  - processes    (child processes without a console window on Windows)

The engines themselves (Piper, Edge TTS, the neural servers) are platform
independent and need nothing here.

Stdlib only, plus optional packages imported lazily where they are needed
(sounddevice, pyperclip, keyboard, win_*).
"""

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import tts_config

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = not IS_WINDOWS and not IS_MAC

# Windows: the tray owns the daemon process (no systemd) and registers the
# global hotkeys in its own process. Linux: systemd and KDE do both.
TRAY_MANAGES_DAEMON = not IS_LINUX
INPROC_HOTKEYS = IS_WINDOWS


def _use_bundled_ffmpeg():
    """The Windows setup copies ffmpeg.exe next to the program. Put that folder
    at the end of PATH, so a system ffmpeg wins and ours is the fallback."""
    here = Path(__file__).resolve().parent
    if IS_WINDOWS and (here / "ffmpeg.exe").is_file() and shutil.which("ffmpeg") is None:
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + str(here)


_use_bundled_ffmpeg()


# --------------------------------------------------------------------------- #
#  Paths  (shared by daemon, client, tray and filters)
# --------------------------------------------------------------------------- #
def piper_dir() -> Path:
    """Directory for voices.conf, Piper voices, replace.conf and user filters.
    Linux: ~/.local/share/piper, Windows: %APPDATA%\\piper. The PIPER_DIR
    environment variable overrides both (custom installs, tests)."""
    env = os.environ.get("PIPER_DIR")
    if env:
        return Path(env)
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData/Roaming")
        return Path(base) / "piper"
    return Path.home() / ".local/share/piper"


# --------------------------------------------------------------------------- #
#  Child processes
# --------------------------------------------------------------------------- #
# Tray, settings and daemon run under pythonw, which has no console. On
# Windows every console program they start (ffmpeg, nvidia-smi, powershell,
# python.exe) would get a console window of its own and flash it on screen.
# Child processes therefore go through these helpers.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def hidden(kwargs=None) -> dict:
    """kwargs for subprocess.Popen/run, plus what keeps the child invisible.

    Windows: CREATE_NO_WINDOW (or-ed into existing creationflags), a hidden
    startup window, and stdin=DEVNULL unless stdin or input is given (pythonw
    has no valid standard handles to inherit). Elsewhere unchanged."""
    kw = dict(kwargs or {})
    if not IS_WINDOWS:
        return kw
    kw["creationflags"] = kw.get("creationflags", 0) | CREATE_NO_WINDOW
    if "startupinfo" not in kw and hasattr(subprocess, "STARTUPINFO"):
        si = subprocess.STARTUPINFO()
        si.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
        si.wShowWindow = 0                       # SW_HIDE
        kw["startupinfo"] = si
    if "stdin" not in kw and "input" not in kw:
        kw["stdin"] = subprocess.DEVNULL
    return kw


def popen_hidden(args, **kwargs) -> subprocess.Popen:
    """subprocess.Popen without a console window on Windows."""
    return subprocess.Popen(args, **hidden(kwargs))


def run_hidden(args, **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run without a console window on Windows."""
    return subprocess.run(args, **hidden(kwargs))


# --------------------------------------------------------------------------- #
#  Configuration  (voices.conf)
# --------------------------------------------------------------------------- #
# voices.conf is parsed by tts_config. conf_write updates keys in place and
# keeps comments and line order.
def conf_path() -> Path:
    return piper_dir() / "voices.conf"


def app_version() -> str:
    """Version of the installed app: recito-version.txt beside the modules
    (written by the install scripts), or VERSION at the root of a checkout."""
    here = Path(__file__).resolve().parent
    for f in (here / "recito-version.txt", here.parent / "VERSION"):
        try:
            v = f.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if v:
            return v
    return ""


def conf_read() -> dict:
    return tts_config.read(conf_path())


def conf_write(updates: dict):
    """Set or replace keys, keep existing lines and comments, append keys that
    are not in the file yet."""
    p = conf_path()
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    seen, out = set(), []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in updates:
                out.append(f"{k}={updates[k]}")
                seen.add(k)
                continue
        out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(out) + "\n", encoding="utf-8")


def open_file(path):
    """Open a file in the default editor (tray: edit replace.conf)."""
    path = str(path)
    if IS_WINDOWS:
        os.startfile(path)               # noqa: F821  (Windows only)
    elif IS_MAC:
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# --------------------------------------------------------------------------- #
#  IPC  (daemon socket)
# --------------------------------------------------------------------------- #
# Linux/macOS: Unix domain socket in $XDG_RUNTIME_DIR. Windows: TCP on
# 127.0.0.1, port 8765 by default (AF_UNIX is unreliable there).
#
# Hyper-V/WSL reserve whole port ranges on Windows at runtime (see
# `netsh int ipv4 show excludedportrange protocol=tcp`). If 8765 falls in one,
# bind() fails with WSAEACCES (10013). The daemon then takes a free port from
# the OS and writes "port pid" to daemon.port, where clients look it up.
_DEFAULT_PORT = 8765
_WSAEACCES = 10013


def _port_file() -> Path:
    return piper_dir() / "daemon.port"


def _pid_alive(pid: int) -> bool:
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    return bool(ok) and code.value == 259        # STILL_ACTIVE


def _published_port():
    try:
        port, pid = _port_file().read_text(encoding="ascii").split()
        if _pid_alive(int(pid)):
            return int(port)
    except (OSError, ValueError):
        pass
    return None


def ipc_address():
    if IS_WINDOWS:
        env = os.environ.get("PIPER_PORT")
        if env:
            return ("127.0.0.1", int(env))
        return ("127.0.0.1", _published_port() or _DEFAULT_PORT)
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return os.environ.get("PIPER_SOCKET", os.path.join(runtime, "piper-read.sock"))


def ipc_listen(backlog=8):
    """Create, bind and listen on the server socket."""
    if IS_WINDOWS:
        env = os.environ.get("PIPER_PORT")
        port = int(env) if env else _DEFAULT_PORT
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # No SO_REUSEADDR on Windows: there it lets a second daemon take over
        # the same port silently. Without it a second start fails with
        # WSAEADDRINUSE.
        try:
            s.bind(("127.0.0.1", port))
        except OSError as e:
            # Fall back only for a reserved port; "in use" means a daemon is running
            if env or getattr(e, "winerror", None) != _WSAEACCES:
                raise
            s.bind(("127.0.0.1", 0))
        if not env:
            pf = _port_file()
            pf.parent.mkdir(parents=True, exist_ok=True)
            pf.write_text(f"{s.getsockname()[1]} {os.getpid()}", encoding="ascii")
        s.listen(backlog)
        return s
    addr = ipc_address()
    if os.path.exists(addr):
        os.unlink(addr)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(addr)
    s.listen(backlog)
    return s


def ipc_connect(timeout=None):
    """Connect to the daemon. Raises OSError if it is not reachable."""
    addr = ipc_address()
    family = socket.AF_INET if IS_WINDOWS else socket.AF_UNIX
    s = socket.socket(family, socket.SOCK_STREAM)
    if timeout is not None:
        s.settimeout(timeout)
    s.connect(addr)
    return s


# --------------------------------------------------------------------------- #
#  Text selection  (selected text / clipboard)
# --------------------------------------------------------------------------- #
def _clip_text():
    try:
        import pyperclip
        return pyperclip.paste() or ""
    except Exception:
        return ""


def _grab_selection_windows(timeout=0.45):
    """Selected text in the foreground window, via a synthetic Ctrl+C.

    Windows has no PRIMARY selection. The clipboard is restored afterwards. If
    nothing is selected (clipboard unchanged), returns the current clipboard so
    copied text is still read."""
    import ctypes
    import time
    u = ctypes.windll.user32
    KEYUP = 0x0002
    SCAN = 0x0008
    SC_CTRL, SC_C = 0x1D, 0x2E               # scan codes Ctrl, C
    # Modifiers the hotkey may still hold. Release only keys that are actually
    # down, otherwise Ctrl+C becomes Ctrl+Alt+C; a lone Alt-up triggers nothing.
    HELD = (0x12, 0xA5, 0x11, 0xA2, 0xA3, 0x10, 0x5B, 0x5C)  # Alt,AltGr,Ctrl,LCtrl,RCtrl,Shift,LWin,RWin
    old = _clip_text()
    for vk in HELD:
        if u.GetAsyncKeyState(vk) & 0x8000:
            u.keybd_event(vk, 0, KEYUP, 0)
    time.sleep(0.02)
    seq = u.GetClipboardSequenceNumber()
    u.keybd_event(0, SC_CTRL, SCAN, 0)       # Ctrl+C via scan codes
    u.keybd_event(0, SC_C, SCAN, 0)
    u.keybd_event(0, SC_C, SCAN | KEYUP, 0)
    u.keybd_event(0, SC_CTRL, SCAN | KEYUP, 0)
    changed = False
    end = time.time() + timeout
    while time.time() < end:
        if u.GetClipboardSequenceNumber() != seq:
            changed = True
            break
        time.sleep(0.02)
    if not changed:
        return old                          # nothing selected: clipboard
    sel = _clip_text()
    try:
        import pyperclip
        pyperclip.copy(old)                 # restore previous clipboard
    except Exception:
        pass
    return sel if sel.strip() else old


def get_selection(primary=True):
    """Text to read aloud.

    Linux/Wayland: PRIMARY selection (mouse selection) or clipboard via
    wl-paste, xclip as X11 fallback. Windows: no PRIMARY, so the selection
    comes from the Ctrl+C trick (primary=True), otherwise the clipboard.
    macOS: clipboard."""
    if IS_WINDOWS:
        return _grab_selection_windows() if primary else _clip_text()
    if IS_MAC:
        return _clip_text()
    cmd = ["wl-paste", "-n"] + (["-p"] if primary else [])
    try:
        return subprocess.run(cmd, capture_output=True, text=True).stdout
    except FileNotFoundError:
        pass
    try:
        sel = "primary" if primary else "clipboard"
        return subprocess.run(["xclip", "-o", "-selection", sel],
                              capture_output=True, text=True).stdout
    except FileNotFoundError:
        return ""


def notify(title, message):
    """Short message to the user (e.g. daemon not reachable). Best effort."""
    if IS_LINUX:
        try:
            subprocess.run(["notify-send", title, message], check=False)
            return
        except FileNotFoundError:
            pass
    # sys.stderr can be None under pythonw
    try:
        print(f"{title}: {message}", file=sys.stderr)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  Daemon lifecycle
# --------------------------------------------------------------------------- #
# Linux: systemd user service (start/restart). Windows/macOS: the tray starts
# the daemon as a background process (pythonw, no console) and stops it with
# the IPC command {"cmd":"quit"}.
_daemon_proc = None


def daemon_running(timeout=0.4) -> bool:
    """Whether the daemon is listening. Connects and closes at once; the daemon
    reads EOF and ignores the empty message."""
    try:
        ipc_connect(timeout=timeout).close()
        return True
    except OSError:
        return False


def _pythonw_exe() -> str:
    """Interpreter without a console window (pythonw) for the background daemon."""
    exe = sys.executable or ("pythonw.exe" if IS_WINDOWS else "python3")
    if IS_WINDOWS and exe.lower().endswith("python.exe"):
        cand = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(cand):
            return cand
    return exe


def _daemon_script() -> str:
    return str(Path(__file__).resolve().parent / "piper-read-daemon.py")


_ensure_lock = threading.Lock()


def ensure_daemon_running():
    """Start a daemon if none is listening (tray-managed platforms only).

    No-op on Linux, where the systemd user service does this. The lock keeps
    the watchdog (main thread) and a restart after saving (worker thread) from
    starting two daemons at once."""
    if not TRAY_MANAGES_DAEMON:
        return
    with _ensure_lock:
        _ensure_daemon_running()


def _ensure_daemon_running():
    global _daemon_proc
    # A daemon we started is still alive, possibly still loading models with
    # the port not yet open. Do not start another one.
    if _daemon_proc is not None and _daemon_proc.poll() is None:
        return
    if daemon_running():
        return
    script = _daemon_script()
    # The daemon's stdout/stderr go to a log file. Without this the child uses
    # the locale encoding (cp1252) and print() raises UnicodeEncodeError on
    # characters such as U+2011.
    env = dict(os.environ, PYTHONIOENCODING="utf-8:replace")
    kwargs = {"cwd": os.path.dirname(script), "env": env}
    fh = None
    if IS_WINDOWS:
        # pythonw has no stdout/stderr, so the daemon's print() needs a log file
        try:
            log = piper_dir()
            log.mkdir(parents=True, exist_ok=True)
            fh = open(log / "daemon.log", "a", buffering=1, encoding="utf-8")
            kwargs["stdout"] = fh
            kwargs["stderr"] = subprocess.STDOUT
        except OSError:
            fh = None
    try:
        _daemon_proc = popen_hidden([_pythonw_exe(), script], **kwargs)
    finally:
        if fh is not None:
            fh.close()        # the child has its own copy of the handle


def stop_daemon():
    """Ask the daemon to quit (it stops playback and exits)."""
    try:
        s = ipc_connect(timeout=2)
        s.sendall(b'{"cmd": "quit"}')
        s.close()
    except OSError:
        pass


_restart_lock = threading.Lock()


def restart_daemon():
    """Restart the daemon so config changes take effect.

    Linux: systemd user service. Elsewhere: ask it to quit, wait until the
    port is free, then start a fresh one (voices are reloaded). The lock keeps
    two quick saves from interleaving their restarts."""
    with _restart_lock:
        if IS_LINUX:
            subprocess.run(["systemctl", "--user", "restart", "piper-read.service"],
                           check=False)
            return
        stop_daemon()
        end = time.time() + 5
        while time.time() < end and daemon_running(timeout=0.3):
            time.sleep(0.15)
        ensure_daemon_running()


# --------------------------------------------------------------------------- #
#  Global hotkeys
# --------------------------------------------------------------------------- #
# Linux: KDE shortcuts bound to .desktop launchers (keyd provides the combos).
#        Action key = launcher base name (de, de-fast, ...).
# Windows: the tray registers the hotkeys in its own process.
#        Action key = voices.conf key hotkey_<action> (read, ocr, ...).
if IS_WINDOWS:
    HOTKEY_ACTIONS = [
        ("read",      "Vorlesen (Auto-Sprache)"),
        ("read_fast", "Vorlesen, schnell"),
        ("ocr",       "OCR vorlesen (Region)"),
        ("ocr_fast",  "OCR vorlesen, schnell"),
        ("pause",     "Pause / Fortsetzen"),
        ("stop",      "Stop"),
        ("reader",    "In ElevenReader anhören"),
    ]
    _HOTKEY_DEFAULTS = {
        "read": "ctrl+alt+p", "read_fast": "ctrl+alt+shift+p",
        "ocr": "ctrl+alt+o", "ocr_fast": "ctrl+alt+shift+o",
        "pause": "ctrl+alt+l", "stop": "ctrl+alt+s",
        "reader": "ctrl+alt+e",
    }
else:
    HOTKEY_ACTIONS = [
        ("de",      "Vorlesen (Auto-Sprache)"),
        ("de-fast", "Vorlesen, schnell"),
        ("en",      "OCR vorlesen (Region)"),
        ("en-fast", "OCR vorlesen, schnell"),
        ("de-ocr",  "OCR Deutsch (forciert)"),
        ("en-ocr",  "OCR Englisch (forciert)"),
        ("pause",   "Pause / Fortsetzen"),
        ("reader",  "In ElevenReader anhören"),
    ]
    _HOTKEY_DEFAULTS = {}


def _kbd_to_qt(combo: str) -> str:
    """keyboard format ('ctrl+alt+p') -> Qt portable text ('Ctrl+Alt+P')."""
    if not combo:
        return ""
    name = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "shift": "Shift",
            "windows": "Meta", "win": "Meta", "cmd": "Meta", "esc": "Esc"}
    out = []
    for tok in combo.replace(" ", "").split("+"):
        if not tok:
            continue
        out.append(name.get(tok.lower(), tok.upper() if len(tok) == 1 else tok.capitalize()))
    return "+".join(out)


def _qt_to_kbd(qt_text: str) -> str:
    """Qt portable text ('Ctrl+Alt+P') -> keyboard format ('ctrl+alt+p')."""
    if not qt_text:
        return ""
    name = {"meta": "windows", "control": "ctrl"}
    out = []
    for tok in qt_text.split("+"):
        t = tok.strip().lower()
        if t:
            out.append(name.get(t, t))
    return "+".join(out)


def read_hotkey(action: str) -> str:
    """Current combo of an action as Qt portable text (for the GUI editor)."""
    if IS_WINDOWS:
        stored = conf_read().get(f"hotkey_{action}", _HOTKEY_DEFAULTS.get(action, ""))
        return _kbd_to_qt(stored)
    try:
        out = subprocess.run(
            ["kreadconfig6", "--file", "kglobalshortcutsrc", "--group", "services",
             "--group", f"recito-{action}.desktop", "--key", "_launch"],
            capture_output=True, text=True, check=False).stdout.strip()
    except OSError:              # no KDE (kreadconfig6 missing)
        return ""
    return out.split(",")[0].split("\t")[0].strip()


def write_hotkey(action: str, qt_combo: str):
    """Store a combo (Qt portable text) without activating it; apply_hotkeys()
    does that."""
    if IS_WINDOWS:
        conf_write({f"hotkey_{action}": _qt_to_kbd(qt_combo)})
        return
    try:
        subprocess.run(
            ["kwriteconfig6", "--file", "kglobalshortcutsrc", "--group", "services",
             "--group", f"recito-{action}.desktop", "--key", "_launch", qt_combo],
            check=False)
    except OSError:              # no KDE: still save the other settings
        pass


_hk_handlers = {}          # action -> callable  (Windows; set by the tray)


def suspend_hotkeys():
    """Unregister the global hotkeys for a while, e.g. while the settings
    window records a new combo, so the key press lands in the input field and
    does not trigger the action. apply_hotkeys() re-enables them.
    No-op on Linux."""
    if not IS_WINDOWS:
        return
    try:
        import win_hotkeys
        win_hotkeys.register([])      # unregister all, register nothing
    except Exception:
        pass


def apply_hotkeys(handlers=None):
    """Activate the stored hotkeys.

    Linux: make kglobalaccel reload its config.
    Windows: drop previous registrations and register again from voices.conf
    (handlers = {action: callable}; passed on the first call, kept after)."""
    global _hk_handlers
    if IS_LINUX:
        try:
            subprocess.run(["kquitapp6", "kglobalacceld"], check=False, capture_output=True)
            subprocess.run(["kstart", "kglobalacceld"], check=False, capture_output=True)
        except OSError:          # no KDE
            pass
        return
    if not IS_WINDOWS:
        return
    if handlers is not None:
        _hk_handlers = handlers
    # Native Win32 RegisterHotKey (see win_hotkeys) handles AltGr as Ctrl+Alt,
    # the hook-based keyboard library does not.
    try:
        import win_hotkeys
    except Exception as exc:
        notify("Recito", f"Hotkeys inaktiv ({exc}).")
        return
    items = [(read_hotkey(action), _hk_handlers[action])
             for action, _label in HOTKEY_ACTIONS
             if read_hotkey(action) and action in _hk_handlers]
    win_hotkeys.register(items)


# --------------------------------------------------------------------------- #
#  Autostart
# --------------------------------------------------------------------------- #
# Linux: install.sh creates the XDG autostart .desktop file, so this is a stub.
# Windows: shortcut in the Startup folder (win_autostart.py).
def autostart_supported() -> bool:
    return IS_WINDOWS


def autostart_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import win_autostart
        return win_autostart.enabled()
    except Exception:
        return False


def set_autostart(on: bool):
    if not IS_WINDOWS:
        return
    import win_autostart
    win_autostart.set_enabled(bool(on))
    conf_write({"autostart": "on" if on else "off"})   # mirrors the checkbox


# --------------------------------------------------------------------------- #
#  OCR  (select a region -> text)
# --------------------------------------------------------------------------- #
def sapi_voices():
    """Installed SAPI5 voices (for the tray's suggestions). Windows only."""
    if not IS_WINDOWS:
        return []
    try:
        import win_sapi
        return win_sapi.list_voices()
    except Exception:
        return []


def ocr_supported() -> bool:
    """Whether OCR is available in the tray process. Windows: built-in Windows
    OCR (winsdk), tesseract as fallback."""
    if not IS_WINDOWS:
        return False
    try:
        import winsdk  # noqa: F401
        return True
    except Exception:
        import shutil
        return shutil.which("tesseract") is not None


def capture_ocr_text(lang=None) -> str:
    """Let the user drag a screen region, run OCR on it, return the text.
    Must be called in the Qt GUI thread (opens a selection overlay). lang is
    a tesseract language string; default: the reading languages."""
    if lang is None:
        import tts_langs
        codes = tts_langs.parse_read_langs(conf_read().get("read_langs"))
        lang = "+".join(tts_langs.TESSERACT[c] for c in codes)
    if not IS_WINDOWS:
        raise NotImplementedError("OCR on Linux runs through the KDE/keyd chain")
    import win_ocr
    return win_ocr.capture_text(lang=lang)


# --------------------------------------------------------------------------- #
#  Audio output  (raw PCM, S16LE)
# --------------------------------------------------------------------------- #
# Interface used by the daemon:
#   sink = make_audio_sink(rate, channels)
#   sink.write(pcm_bytes) -> bool   (False = aborted or error)
#   sink.pause_toggle()             (pause/resume)
#   sink.close()                    (drain, then stop)
#   sink.kill()                     (stop at once)
#   sink.position() -> int          (bytes heard so far, for the follow window)

class _AplaySink:
    """Linux: ALSA `aplay` as a subprocess; pause via SIGSTOP/SIGCONT."""

    def __init__(self, rate, channels):
        self._p = subprocess.Popen(
            ["aplay", "-q", "-t", "raw", "-f", "S16_LE",
             "-r", str(rate), "-c", str(channels)],
            stdin=subprocess.PIPE)
        self._paused = False
        # aplay reports no position, so position() is a clock that runs at the
        # byte rate while written audio is left and stands still when paused.
        self._rate = rate * channels * 2
        self._written = 0
        self._pos = 0.0
        self._t = None

    def _tick(self):
        now = time.monotonic()
        if self._t is not None and not self._paused:
            self._pos = min(self._written, self._pos + (now - self._t) * self._rate)
        self._t = now

    def write(self, pcm):
        try:
            self._p.stdin.write(pcm)
            self._p.stdin.flush()
        except (BrokenPipeError, ValueError, OSError):
            return False
        self._tick()
        self._written += len(pcm)
        return True

    def position(self):
        if self._t is None:
            return 0
        self._tick()
        return int(self._pos)

    def pause_toggle(self):
        import signal
        if self._p.poll() is not None:
            return
        try:
            self._p.send_signal(signal.SIGCONT if self._paused else signal.SIGSTOP)
            self._tick()
            self._paused = not self._paused
        except Exception:
            pass

    def close(self):
        try:
            if self._p.stdin:
                self._p.stdin.close()
        except Exception:
            pass
        try:
            self._p.wait(timeout=5)
        except Exception:
            self._p.kill()

    def kill(self):
        import signal
        if self._p.poll() is None:
            try:
                self._p.send_signal(signal.SIGCONT)   # wake up if paused
            except Exception:
                pass
            self._p.terminate()


class _SoundDeviceSink:
    """Windows/macOS (and Linux without aplay): PCM through sounddevice/PortAudio,
    callback-based with its own ring buffer.

    On Windows, PortAudio abort() from one thread while another thread is
    blocked in a blocking stream.write() causes an access violation. The daemon
    calls kill() from its handler thread while the playback thread writes, so
    nothing here blocks inside PortAudio: write() only fills a buffer (with
    backpressure like the aplay pipe), the callback drains it, and stop/pause
    are plain flags. kill() can abort the stream safely because the callback
    is short and never blocks.

    Pause: the callback outputs silence and the buffer stays put, so playback
    resumes where it stopped. kill(): the callback ends at once. close(): the
    remaining buffer is played, then the stream stops."""

    def __init__(self, rate, channels):
        import sounddevice as sd
        import threading
        self._sd = sd
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._space = threading.Event()
        self._space.set()
        self._paused = False
        self._dead = False
        self._done = False                       # close() called: drain
        self._max_buf = rate * channels * 2 * 2  # ~2 s backpressure (s16)
        self._played = 0                         # bytes handed to the device
        self._stream = sd.RawOutputStream(
            samplerate=rate, channels=channels, dtype="int16",
            blocksize=0, callback=self._callback)
        self._stream.start()
        # What the callback hands over is heard one output latency later
        self._latency = int((getattr(self._stream, "latency", 0) or 0) * rate * channels * 2)

    def _callback(self, outdata, frames, time_info, status):
        need = len(outdata)
        if self._dead:
            raise self._sd.CallbackAbort()
        if self._paused:
            outdata[:] = b"\x00" * need            # silence, buffer stays put
            return
        with self._lock:
            n = min(need, len(self._buf))
            if n:
                outdata[:n] = self._buf[:n]
                del self._buf[:n]
                self._played += n
                if len(self._buf) < self._max_buf:
                    self._space.set()
        if n < need:
            outdata[n:] = b"\x00" * (need - n)     # underrun: silence
            if self._done and not self._buf:
                raise self._sd.CallbackStop()      # fully played

    def write(self, pcm):
        # Backpressure: wait until the buffer has room (like the aplay pipe)
        while not self._dead:
            with self._lock:
                if len(self._buf) < self._max_buf:
                    self._buf += pcm
                    return True
                self._space.clear()
            self._space.wait(0.1)
        return False

    def position(self):
        return max(0, self._played - self._latency)

    def pause_toggle(self):
        self._paused = not self._paused

    def close(self):
        # Play the remaining buffer, then stop (no cross-thread abort)
        self._done = True
        try:
            for _ in range(250):                   # wait at most ~5 s
                if self._dead or not self._stream.active:
                    break
                self._sd.sleep(20)
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass

    def kill(self):
        self._dead = True
        self._paused = False
        self._space.set()                          # release a blocked write()
        try:
            self._stream.abort()                   # callback ends at once (CallbackAbort)
            self._stream.close()
        except Exception:
            pass


def make_audio_sink(rate, channels):
    """Audio sink for the platform. Linux prefers aplay (exact pause/stop
    behavior), otherwise sounddevice."""
    if IS_LINUX:
        import shutil
        if shutil.which("aplay"):
            return _AplaySink(rate, channels)
    return _SoundDeviceSink(rate, channels)
