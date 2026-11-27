# -*- coding: utf-8 -*-
"""
Records a shortcut on Windows through a low-level keyboard hook
(WH_KEYBOARD_LL), the way shortcut editors of other apps do it.

Qt key events are not good enough for this:
- On many layouts Ctrl+Alt is AltGr, so Ctrl+Alt+E arrives as "€" and
  Ctrl+Alt+Q as "@"; Shift+1 arrives as "!".
- A combination another program holds through RegisterHotKey never reaches
  the window at all, so pressing it seemed to do nothing.
- Windows itself grabs some Win combinations.

The hook sees the physical key (virtual-key code) before all of that. While
recording, and only while one of our windows is in the foreground, it
swallows every key, so neither the other program nor Windows reacts, and it
reports what was pressed through a callback:

    ("mods", "Ctrl+Alt")     modifiers held so far ("" when released)
    ("combo", "Ctrl+Alt+E")  a finished combination, Qt portable text
    ("clear", "")            Esc, Delete or Backspace without modifiers
    ("reject", "")           a key that cannot be a global shortcut here
    ("tab", "next"|"back")   Tab or Shift+Tab: move on to the next field

The hook runs on the thread that installs it (the Qt GUI thread, which pumps
messages). The callback must return quickly; hand the event on with a queued
signal or a zero timer."""

import ctypes
import ctypes.wintypes as wintypes
import os

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105

# virtual-key code -> modifier name (Qt portable text)
_MOD_VK = {
    0x10: "Shift", 0xA0: "Shift", 0xA1: "Shift",
    0x11: "Ctrl", 0xA2: "Ctrl", 0xA3: "Ctrl",
    0x12: "Alt", 0xA4: "Alt", 0xA5: "Alt",
    0x5B: "Meta", 0x5C: "Meta",
}
_MOD_ORDER = ("Meta", "Ctrl", "Alt", "Shift")        # how Qt portable text orders them

# Keys win_hotkeys can register, named as Qt portable text does
_NAMED = {
    0x20: "Space", 0x2D: "Ins", 0x2E: "Del", 0x24: "Home", 0x23: "End",
    0x21: "PgUp", 0x22: "PgDown", 0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
}
VK_TAB, VK_ESCAPE, VK_DELETE, VK_BACK = 0x09, 0x1B, 0x2E, 0x08


def vk_name(vk):
    """Name of a virtual key in Qt portable text, None if it cannot be a
    shortcut key (modifiers, punctuation that depends on the layout, ...)."""
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        return chr(vk)
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x6F}"
    return _NAMED.get(vk)


def combo_text(mods, key):
    """{'Alt', 'Ctrl'}, 'E' -> 'Ctrl+Alt+E'."""
    return "+".join([m for m in _MOD_ORDER if m in mods] + ([key] if key else []))


def scancode_to_vk(scancode):
    """Physical key of a Qt key event (nativeScanCode) -> virtual-key code,
    0 if unknown. Used when the hook is not available."""
    if not scancode:
        return 0
    try:
        return ctypes.windll.user32.MapVirtualKeyW(scancode, 3)   # MAPVK_VSC_TO_VK_EX
    except Exception:
        return 0


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


_LRESULT = ctypes.c_ssize_t
# WINFUNCTYPE exists on Windows only; the pure helpers above also load elsewhere (tests)
_HOOKPROC = (ctypes.WINFUNCTYPE(_LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
             if hasattr(ctypes, "WINFUNCTYPE") else None)


class KeyRecorder:
    """One hook at a time. start() returns a token, stop(token) ends that
    recording (a stale token from a field that already lost focus is
    ignored, so focus moving between two fields cannot stop the new one)."""

    def __init__(self, callback):
        self._cb = callback
        self._hook = None
        self._proc = None          # keeps the ctypes callback alive
        self._token = 0
        self._held = set()         # left/right modifier vks held right now
        self._down = set()         # non-modifier vks pressed while recording
        self._pid = os.getpid()
        u = ctypes.windll.user32
        u.SetWindowsHookExW.argtypes = (ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
        u.SetWindowsHookExW.restype = wintypes.HHOOK
        u.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        u.CallNextHookEx.restype = _LRESULT
        u.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
        u.GetAsyncKeyState.restype = ctypes.c_short
        self._u = u

    @property
    def active(self):
        return self._hook is not None

    def start(self):
        if self._hook is None:
            self._proc = _HOOKPROC(self._on_key)
            k = ctypes.windll.kernel32
            k.GetModuleHandleW.restype = wintypes.HMODULE
            self._hook = self._u.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc,
                                                   k.GetModuleHandleW(None), 0)
            if not self._hook:
                self._proc = None
                return 0
        # modifiers already held when the field got focus (e.g. Shift+Tab);
        # nothing is swallowed yet, so the async state is still true
        self._held = {vk for vk in _MOD_VK if vk not in (0x10, 0x11, 0x12)
                      and self._u.GetAsyncKeyState(vk) & 0x8000}
        self._down = set()
        self._token += 1
        return self._token

    def stop(self, token=None):
        if token is not None and token != self._token:
            return
        if self._hook is not None:
            self._u.UnhookWindowsHookEx(self._hook)
        self._hook = None
        self._proc = None
        self._held = set()
        self._down = set()

    def _mods(self):
        return {_MOD_VK[vk] for vk in self._held}

    def _ours_in_front(self):
        hwnd = self._u.GetForegroundWindow()
        if not hwnd:
            return False
        pid = wintypes.DWORD()
        self._u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value == self._pid

    def _next(self, code, wparam, lparam):
        return self._u.CallNextHookEx(None, code, wparam, lparam)

    def _emit(self, kind, value=""):
        try:
            self._cb(kind, value)
        except Exception:
            pass

    def _on_key(self, code, wparam, lparam):
        if code < 0 or self._hook is None or not self._ours_in_front():
            return self._next(code, wparam, lparam)
        kb = ctypes.cast(lparam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
        vk = kb.vkCode
        down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
        mod = _MOD_VK.get(vk)
        if mod:
            # Swallowed keys do not update the key state Windows keeps, so the
            # modifiers are tracked here. AltGr sends a fake LeftCtrl before
            # RightAlt; together they read Ctrl+Alt, which is also how
            # RegisterHotKey sees AltGr.
            before = self._mods()
            (self._held.add if down else self._held.discard)(vk)
            if self._mods() != before:
                self._emit("mods", combo_text(self._mods(), ""))
            return 1
        if not down:
            self._down.discard(vk)
            return 1
        if vk in self._down:               # auto-repeat while held
            return 1
        self._down.add(vk)
        mods = self._mods()
        if vk == VK_TAB and not mods - {"Shift"}:
            self._emit("tab", "back" if mods else "next")
            return 1
        if not mods and vk in (VK_ESCAPE, VK_DELETE, VK_BACK):
            self._emit("clear")
            return 1
        name = vk_name(vk)
        if name is None:
            self._emit("reject")
            return 1
        self._emit("combo", combo_text(mods, name))
        return 1
