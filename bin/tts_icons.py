"""
tts_icons: app, window and tray icons.

The rasters come from tools/build_icons.py (assets/icons/ in the repo,
icons/ next to the installed scripts). Linux additionally installs them into
the hicolor theme, so the theme names below resolve there first.

Tray icon per platform:
  Windows  tray-light / tray-dark rasters, picked from the taskbar theme
           (SystemUsesLightTheme), several sizes for HiDPI.
  Linux    theme icon tts-vorlesen-tray[-<state>]-symbolic: Plasma recolors it
           through its current-color-scheme stylesheet, GNOME (AppIndicator)
           through the -symbolic suffix. Rasters are the fallback.
"""

import sys
from pathlib import Path

APP_NAME = "tts-vorlesen"                 # theme icon name of the app
TRAY_NAME = "tts-vorlesen-tray"           # theme icon name of the tray
APP_USER_MODEL_ID = "TTSVorlesen.Tray"    # Windows taskbar grouping
STATES = ("idle", "speaking", "paused", "error")
TRAY_SIZES = (16, 20, 24, 32, 40, 48, 64)
APP_SIZES = (16, 22, 24, 32, 48, 64, 128, 256)

IS_WINDOWS = sys.platform.startswith("win")
_HERE = Path(__file__).resolve().parent


def icons_dir():
    """Folder with app.ico, hicolor/ and tray/, or None."""
    for cand in (_HERE / "icons", _HERE.parent / "assets" / "icons"):
        if (cand / "tray").is_dir() or (cand / "hicolor").is_dir():
            return cand
    return None


def ico_path():
    d = icons_dir()
    return d / "app.ico" if d and (d / "app.ico").is_file() else None


def logo_path():
    """logo-mark.svg for the settings sidebar, or None."""
    d = icons_dir()
    return d / "logo-mark.svg" if d and (d / "logo-mark.svg").is_file() else None


def _from_files(paths):
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QIcon
    icon = QIcon()
    for size, path in paths:
        if path.is_file():
            icon.addFile(str(path), QSize(size, size))
    return icon


def _legacy():
    """Theme speaker icon, used until the generated icons exist."""
    from PySide6.QtGui import QIcon
    icon = QIcon.fromTheme("audio-speakers")
    return icon if not icon.isNull() else QIcon.fromTheme("audio-volume-high")


def app_icon():
    """Multi-size app icon for windows and the taskbar."""
    from PySide6.QtGui import QIcon
    d = icons_dir()
    icon = QIcon()
    if d is not None:
        icon = _from_files([(n, d / "hicolor" / f"{n}x{n}" / "apps" / f"{APP_NAME}.png")
                            for n in APP_SIZES])
    if icon.isNull():
        icon = _legacy()
    if not IS_WINDOWS:
        icon = QIcon.fromTheme(APP_NAME, icon)
    return icon


def taskbar_is_light():
    """Windows: True for a light taskbar (SystemUsesLightTheme=1). Dark is the
    Windows 11 default, so a missing value counts as dark."""
    if not IS_WINDOWS:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return bool(winreg.QueryValueEx(k, "SystemUsesLightTheme")[0])
    except OSError:
        return False


def _desktop_is_dark():
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return False


def tray_variant():
    """'light' or 'dark': the variant drawn for a light or a dark panel."""
    dark = not taskbar_is_light() if IS_WINDOWS else _desktop_is_dark()
    return "dark" if dark else "light"


def tray_raster(state="idle", variant=None):
    """Multi-size raster icon tray-<variant>[-<state>]; idle if the state is missing."""
    variant = variant or tray_variant()
    d = icons_dir()
    if d is None:
        return _legacy()
    for st in (state, "idle"):
        stem = f"tray-{variant}" + ("" if st == "idle" else f"-{st}")
        icon = _from_files([(n, d / "tray" / f"{stem}-{n}.png") for n in TRAY_SIZES])
        if not icon.isNull():
            return icon
    return app_icon()


def tray_theme_name(state="idle"):
    return TRAY_NAME + ("" if state == "idle" else f"-{state}") + "-symbolic"


def tray_icon(state="idle", variant=None):
    """Tray icon for a state (idle, speaking, paused, error)."""
    fallback = tray_raster(state, variant)
    if IS_WINDOWS:
        return fallback
    from PySide6.QtGui import QIcon
    return QIcon.fromTheme(tray_theme_name(state), fallback)


def set_app_user_model_id():
    """Windows: own taskbar group with the app icon instead of python's. Must
    run before the first window exists."""
    if not IS_WINDOWS:
        return
    import ctypes
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            ctypes.c_wchar_p(APP_USER_MODEL_ID))
    except (AttributeError, OSError):
        pass
