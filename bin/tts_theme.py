"""
tts_theme: colors for the parts of the tray app that are Qt Widgets (context
menu, tooltips, message boxes). The settings window is Qt Quick with
Material 3 (ui/Theme.qml) and uses the same color roles.

Light and dark follow the system and switch live.
"""

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

# Material 3 baseline violet, same values as ui/Theme.qml.
LIGHT = {
    "surface": "#FEF7FF", "surface_container": "#F3EDF7",
    "surface_container_highest": "#E6E0E9", "on_surface": "#1D1B20",
    "primary": "#6750A4", "on_primary": "#FFFFFF",
    "outline_variant": "#CAC4D0",
    "inverse_surface": "#322F35", "inverse_on_surface": "#F5EFF7",
}
DARK = {
    "surface": "#141218", "surface_container": "#211F26",
    "surface_container_highest": "#36343B", "on_surface": "#E6E0E9",
    "primary": "#D0BCFF", "on_primary": "#381E72",
    "outline_variant": "#49454F",
    "inverse_surface": "#E6E0E9", "inverse_on_surface": "#322F35",
}


def _linux_desktop_dark():
    """Ask the desktop directly whether it is dark. The pip Qt has no KDE
    platform theme and often reports the scheme as 'Unknown' there.
    Returns True/False, or None when nothing can be found out."""
    import configparser
    import subprocess
    from pathlib import Path
    kde = Path.home() / ".config/kdeglobals"
    if kde.exists():
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            cp.read(kde, encoding="utf-8")
            bg = cp.get("Colors:Window", "BackgroundNormal", fallback="")
            if bg:
                r, g, b = (int(x) for x in bg.split(",")[:3])
                return (r * 299 + g * 587 + b * 114) / 1000 < 128
            name = cp.get("General", "ColorScheme", fallback="")
            if name:
                return "dark" in name.lower()
        except (configparser.Error, ValueError, OSError):
            pass
    try:
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface",
                              "color-scheme"], capture_output=True, text=True,
                             timeout=2).stdout
        if out.strip():
            return "dark" in out
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def is_dark() -> bool:
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme != Qt.ColorScheme.Unknown:
            return scheme == Qt.ColorScheme.Dark
    except AttributeError:                       # Qt < 6.5
        pass
    if sys.platform.startswith("linux"):
        found = _linux_desktop_dark()
        if found is not None:
            return found
    return QGuiApplication.palette().window().color().lightness() < 128


def _stylesheet(c: dict) -> str:
    return f"""
QMenu {{
    background: {c['surface_container']}; color: {c['on_surface']};
    border: 1px solid {c['outline_variant']}; border-radius: 8px; padding: 8px 0;
}}
QMenu::item {{ padding: 8px 32px 8px 12px; border-radius: 4px; margin: 0 4px; }}
QMenu::item:selected {{ background: {c['surface_container_highest']}; }}
QMenu::separator {{ height: 1px; background: {c['outline_variant']}; margin: 8px 0; }}
QMenu::indicator {{ width: 16px; height: 16px; margin-left: 8px; }}
QToolTip {{
    background: {c['inverse_surface']}; color: {c['inverse_on_surface']};
    border: none; border-radius: 4px; padding: 4px 8px;
}}
"""


def _palette(c: dict) -> QPalette:
    p = QPalette()
    for role, key in (
        (QPalette.Window, "surface"), (QPalette.Base, "surface"),
        (QPalette.Button, "surface_container"),
        (QPalette.WindowText, "on_surface"), (QPalette.Text, "on_surface"),
        (QPalette.ButtonText, "on_surface"),
        (QPalette.Highlight, "primary"), (QPalette.HighlightedText, "on_primary"),
    ):
        p.setColor(role, QColor(c[key]))
    return p


def apply(app: QApplication):
    """Apply the colors and re-apply them when the system switches light/dark."""
    app.setStyle(QStyleFactory.create("Fusion"))

    def _refresh(*_):
        c = DARK if is_dark() else LIGHT
        app.setPalette(_palette(c))
        app.setStyleSheet(_stylesheet(c))

    _refresh()
    try:
        QGuiApplication.styleHints().colorSchemeChanged.connect(_refresh)
    except AttributeError:
        pass
