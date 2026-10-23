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

# --- Follow window: Adobe Spectrum 2 look ------------------------------------
# Values from @adobe/spectrum-tokens 13.16 (color-palette, color-aliases,
# layout). The follow window floats above other apps like a Spectrum popover:
# elevated background, no border in light, the elevated drop shadow, quiet
# round action buttons and the Spectrum gray scale for text. Colors with
# alpha are written #AARRGGBB, the order QColor reads.
S2_LIGHT = {
    "elevated": "#FFFFFF",              # background-elevated-color
    "border": "#00000000",              # popover-border-color (none)
    "content": "#292929",               # neutral-content-color (gray-800)
    "content_read": "#717171",          # gray-600, 4.9:1 on elevated
    "title": "#505050",                 # neutral-subdued-content-color (gray-700)
    "on_mark": "#131313",               # gray-900
    "hover": "#E9E9E9",                 # gray-100
    "pressed": "#E1E1E1",               # gray-200
    "ring": "#131313",                  # gray-900, selected swatch
    "swatch_border": "#1F000000",       # 12 % black around a swatch
    "shadow": "#1F000000",              # drop-shadow-elevated-key-color
    "shadow_ambient": "#29000000",      # drop-shadow-elevated-color
}
S2_DARK = {
    "elevated": "#222222",
    "border": "#393939",                # gray-300: dark windows need an edge on dark apps
    "content": "#DBDBDB",
    "content_read": "#8A8A8A",          # gray-600, 5.2:1 on elevated
    "title": "#AFAFAF",
    "on_mark": "#F2F2F2",
    "hover": "#323232",                 # gray-200 (dark scale)
    "pressed": "#393939",               # gray-300
    "ring": "#F2F2F2",
    "swatch_border": "#29FFFFFF",
    "shadow": "#5C000000",              # drop-shadow-color (dark), 36 %
    "shadow_ambient": "#5C000000",
}
# Mark colors for the word being heard: the Spectrum palette at step 400
# (light) and 600 (dark), so the mark stands out from the window (1.6:1 and
# 2.4:1) and the text on it keeps 11.5:1 and 5.9:1 contrast.
MARKS = {
    "blue": ("#ACCFFD", "#2549E5"),
    "purple": ("#DDC1F6", "#8222D7"),
    "magenta": ("#FFB9D0", "#B5134C"),
    "orange": ("#FFC15E", "#A23B00"),
    "yellow": ("#F5C700", "#825200"),
    "green": ("#81E43A", "#32690B"),
    "cyan": ("#8AD5FF", "#03638C"),
}
DEFAULT_MARK = "blue"                   # Spectrum's accent color

# Spacing (4 px grid), corner radii (corner-radius-*) and sizes.
SPACE = {"xs": 4, "s": 8, "m": 12, "l": 16, "xl": 24, "xxl": 48}
RADIUS = {"mark": 4, "swatch": 8, "window": 16}   # small, medium, extra-large
SIZE = {"control": 32, "icon": 20, "swatch": 24, "shadow": 24}
TYPE = {"title": 14, "reading": 18}
READING_LINE_HEIGHT = 150           # percent of the font height
# Background opacity of the follow window in percent; text and mark stay opaque
OPACITY = {"min": 20, "max": 100, "step": 5, "default": 100}
HALO_WIDTH = 1                      # px of panel color around the glyphs of a see-through panel
# Motion: Spectrum's quick durations, shorter on the way out
FADE_IN_MS = 120
FADE_OUT_MS = 140
ICON_STROKE = 1.5


def font_family() -> str:
    return "Segoe UI" if sys.platform == "win32" else "Noto Sans"


def s2_colors() -> dict:
    return S2_DARK if is_dark() else S2_LIGHT


def mark_color(name: str) -> str:
    light, dark = MARKS.get(name, MARKS[DEFAULT_MARK])
    return dark if is_dark() else light


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
