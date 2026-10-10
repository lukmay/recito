# -*- coding: utf-8 -*-
"""
Global hotkeys on Windows through the native Win32 API `RegisterHotKey`.

The low-level hook of the `keyboard` library mishandles AltGr on Windows
(right Alt produces a phantom LeftCtrl and the key press gets lost).
RegisterHotKey handles AltGr as Ctrl+Alt, so a Ctrl+Alt+P hotkey also fires
on AltGr+P. It needs no system-wide hook and no admin rights.

RegisterHotKey is bound to a thread: WM_HOTKEY arrives in the message queue
of the registering thread. A dedicated thread therefore runs a GetMessage
loop, registers and unregisters on that same thread (triggered via
PostThreadMessage) and calls the handler on a hit. The handler is expected to
hand over to the GUI thread itself (e.g. via a Qt signal)."""

import ctypes
import ctypes.wintypes as wintypes
import threading

_u = ctypes.windll.user32
_k = ctypes.windll.kernel32          # GetCurrentThreadId lives in kernel32

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_APP_REREG = 0x8001          # custom message: register again
WM_APP_QUIT = 0x8002

_MODS = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL, "strg": MOD_CONTROL,
    "alt": MOD_ALT, "altgr": MOD_ALT, "alt gr": MOD_ALT,
    "shift": MOD_SHIFT, "umschalt": MOD_SHIFT,
    "win": MOD_WIN, "windows": MOD_WIN, "meta": MOD_WIN, "super": MOD_WIN,
}
_NAMED_VK = {
    "space": 0x20, "leertaste": 0x20, "enter": 0x0D, "return": 0x0D,
    "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "backspace": 0x08,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "ins": 0x2D, "insert": 0x2D, "del": 0x2E, "delete": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
}


def _parse(combo):
    """'Ctrl+Alt+P' / 'ctrl+alt+shift+p' -> (modifiers, vk) or (None, None)."""
    mods, vk = 0, None
    for raw in combo.replace(" ", "").split("+"):
        if not raw:
            continue
        t = raw.lower()
        if t in _MODS:
            mods |= _MODS[t]
        elif len(raw) == 1:
            vk = ord(raw.upper())               # A-Z, 0-9 == ASCII-VK
        elif t in _NAMED_VK:
            vk = _NAMED_VK[t]
        elif t.startswith("f") and t[1:].isdigit() and 1 <= int(t[1:]) <= 24:
            vk = 0x70 + int(t[1:]) - 1           # F1..F24
    return (mods, vk) if vk is not None else (None, None)


class _HotkeyThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self._lock = threading.Lock()
        self._pending = []                       # [(combo, fn)]
        self._handlers = {}                      # hotkey-id -> fn
        self._tid = None
        self._ready = threading.Event()

    def run(self):
        self._tid = _k.GetCurrentThreadId()
        msg = wintypes.MSG()
        _u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # creates the queue
        self._ready.set()
        self._reregister()
        while True:
            r = _u.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r in (0, -1):
                break
            if msg.message == WM_HOTKEY:
                fn = self._handlers.get(int(msg.wParam))
                if fn:
                    try:
                        fn()
                    except Exception:
                        pass
            elif msg.message == WM_APP_REREG:
                self._reregister()
            elif msg.message == WM_APP_QUIT:
                break
        self._unregister_all()

    def _unregister_all(self):
        for hid in list(self._handlers):
            _u.UnregisterHotKey(None, hid)
        self._handlers.clear()

    def _reregister(self):
        self._unregister_all()
        with self._lock:
            pending = list(self._pending)
        hid = 1
        for combo, fn in pending:
            mods, vk = _parse(combo)
            if vk is None:
                continue
            if _u.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
                self._handlers[hid] = fn
                hid += 1

    def set_hotkeys(self, items):
        with self._lock:
            self._pending = list(items)
        if self._ready.wait(2) and self._tid:
            _u.PostThreadMessageW(self._tid, WM_APP_REREG, 0, 0)


_thread = None


def register(items):
    """Set the hotkeys, replacing previous ones. items: list of (combo, callable)."""
    global _thread
    if _thread is None:
        _thread = _HotkeyThread()
        _thread.start()
        _thread._ready.wait(2)
    _thread.set_hotkeys(items)
