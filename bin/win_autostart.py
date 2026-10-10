# -*- coding: utf-8 -*-
"""
Windows shortcuts for the tray app: autostart and Start menu.

Autostart is a shortcut in the Startup folder
(%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\TTS-Vorlesen.lnk)
that starts the tray without a console window (pythonw tts-tray.py). The tray
starts the daemon itself, so one autostart entry is enough. install.ps1 makes
the Start menu entry through start_menu_shortcut().

Both shortcuts carry the app icon (icons\\app.ico) and the tray's
AppUserModelID, so the taskbar shows the app icon for its windows.

PowerShell creates the .lnk through the standard COM component WScript.Shell.
The AppUserModelID needs pywin32 and is skipped without it.
"""

import os
import sys
from pathlib import Path

import tts_icons
import tts_platform

LNK_NAME = "TTS-Vorlesen.lnk"


def _programs_dir() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData/Roaming")
    return Path(appdata) / "Microsoft/Windows/Start Menu/Programs"


def _startup_dir() -> Path:
    return _programs_dir() / "Startup"


def _lnk_path() -> Path:
    return _startup_dir() / LNK_NAME


def _pythonw() -> str:
    exe = sys.executable or "pythonw.exe"
    if exe.lower().endswith("python.exe"):
        cand = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(cand):
            return cand
    return exe


def _tray_script() -> str:
    return str(Path(__file__).resolve().parent / "tts-tray.py")


def enabled() -> bool:
    return _lnk_path().exists()


def set_enabled(on: bool):
    if on:
        create_shortcut(_lnk_path())
    else:
        _remove()


def start_menu_shortcut(lnk=None):
    """Start menu entry 'TTS-Vorlesen' (searchable), or at lnk."""
    create_shortcut(Path(lnk) if lnk else _programs_dir() / LNK_NAME,
                    description="TTS read aloud (tray)")


def _ps_quote(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def create_shortcut(lnk: Path, description: str = ""):
    """Shortcut that starts the tray hidden, with the app icon."""
    target = _pythonw()
    script = _tray_script()
    lnk.parent.mkdir(parents=True, exist_ok=True)
    # The script path argument is quoted; working directory = bin folder
    ps = [
        "$ws = New-Object -ComObject WScript.Shell",
        f"$s = $ws.CreateShortcut({_ps_quote(lnk)})",
        f"$s.TargetPath = {_ps_quote(target)}",
        f"$s.Arguments = {_ps_quote(chr(34) + script + chr(34))}",
        f"$s.WorkingDirectory = {_ps_quote(os.path.dirname(script))}",
        "$s.WindowStyle = 7",
    ]
    ico = tts_icons.ico_path()
    if ico is not None:
        ps.append(f"$s.IconLocation = {_ps_quote(f'{ico},0')}")
    if description:
        ps.append(f"$s.Description = {_ps_quote(description)}")
    ps.append("$s.Save()")
    tts_platform.run_hidden(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", "; ".join(ps)], check=False)
    _set_app_id(lnk)


def _set_app_id(lnk: Path):
    """Give the shortcut the tray's AppUserModelID (pinning, taskbar icon)."""
    try:
        import pythoncom
        from win32com.propsys import propsys, pscon
        from win32com.shell import shellcon
        store = propsys.SHGetPropertyStoreFromParsingName(
            str(lnk), None, shellcon.GPS_READWRITE, propsys.IID_IPropertyStore)
        store.SetValue(pscon.PKEY_AppUserModel_ID,
                       propsys.PROPVARIANTType(tts_icons.APP_USER_MODEL_ID, pythoncom.VT_LPWSTR))
        store.Commit()
    except Exception:
        pass                    # no pywin32 or no shortcut: the icon still works


def _remove():
    try:
        _lnk_path().unlink()
    except FileNotFoundError:
        pass
