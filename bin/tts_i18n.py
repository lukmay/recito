"""
tts_i18n: translations for the tray, the settings window and user-facing
messages of the backend modules.

One JSON catalog per language in bin/i18n/<code>.json, flat keys such as
"nav.voice". English (en.json) is the source and the fallback for every
missing key. Placeholders use str.format names: "Hello {name}".

Python:  from tts_i18n import tr;  tr("save.done", name="Anna")
QML:     Tr.t("save.done", {name: "Anna"})   (bin/ui/Tr.qml, reads i18n.strings)

The language comes from ui_language in voices.conf ("auto" or a code). "auto"
follows the system locale. TTS_UI_LANG overrides both, for tests.
"""

import json
import locale
import os
from pathlib import Path

CATALOG_DIR = Path(__file__).resolve().parent / "i18n"
SOURCE = "en"

_catalogs = {}
_current = None
_listeners = []


def _load(code):
    if code not in _catalogs:
        try:
            data = json.loads((CATALOG_DIR / f"{code}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        _catalogs[code] = data
    return _catalogs[code]


def available():
    """[(code, native name)] of every catalog, English first."""
    out = []
    for p in sorted(CATALOG_DIR.glob("*.json")):
        meta = _load(p.stem).get("_meta", {})
        out.append((p.stem, meta.get("name", p.stem)))
    out.sort(key=lambda x: (x[0] != SOURCE, x[1].lower()))
    return out


def system_language():
    """Two-letter code of the system UI language, if there is a catalog for it."""
    names = []
    try:
        from PySide6.QtCore import QLocale
        names += QLocale.system().uiLanguages()
    except Exception:
        pass
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        if os.environ.get(var):
            names.append(os.environ[var])
    try:
        names.append(locale.getlocale()[0] or "")
    except ValueError:
        pass
    codes = {c for c, _ in available()}
    for n in names:
        code = n.replace("-", "_").split("_")[0].split(":")[0].lower()
        if code in codes:
            return code
    return SOURCE


def setting():
    """What the user picked: "auto" or a language code."""
    try:
        import tts_platform
        return tts_platform.conf_read().get("ui_language", "auto") or "auto"
    except Exception:
        return "auto"


def _resolve(choice):
    codes = {c for c, _ in available()}
    if choice in codes:
        return choice
    return system_language()


def language():
    global _current
    if _current is None:
        _current = os.environ.get("TTS_UI_LANG") or _resolve(setting())
    return _current


def set_language(choice, persist=True):
    """Switch live. choice is "auto" or a code; persist writes voices.conf."""
    global _current
    _current = _resolve(choice)
    if persist:
        import tts_platform
        tts_platform.conf_write({"ui_language": choice})
    for fn in list(_listeners):
        fn()


def on_change(fn):
    _listeners.append(fn)


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def strings(code=None):
    """Merged catalog for a language: English below, the language on top."""
    code = code or language()
    merged = {k: v for k, v in _load(SOURCE).items() if not k.startswith("_")}
    if code != SOURCE:
        merged.update({k: v for k, v in _load(code).items() if not k.startswith("_")})
    return merged


def tr(key, default=None, **kw):
    text = _load(language()).get(key)
    if text is None:
        text = _load(SOURCE).get(key)
    if text is None:
        text = default if default is not None else key
    return text.format_map(_Safe(kw)) if kw else text


def locale_name():
    """Locale for number formatting, from the catalog's _meta."""
    return _load(language()).get("_meta", {}).get("locale", "en_US")


def fmt_num(value, digits=0):
    s = f"{value:,.{digits}f}"
    if _load(language()).get("_meta", {}).get("decimal", ".") == ",":
        s = s.replace(",", "\0").replace(".", ",").replace("\0", ".")
    return s


# --------------------------------------------------------------------------- #
#  QML bridge
# --------------------------------------------------------------------------- #
_bridge = None


def bridge():
    """The "i18n" context property for QML (created on first use)."""
    global _bridge
    if _bridge is None:
        from PySide6.QtCore import Property, QObject, Signal, Slot

        class I18n(QObject):
            changed = Signal()

            def __init__(self):
                super().__init__()
                on_change(self.changed.emit)

            @Property("QVariantMap", notify=changed)
            def strings(self):
                return strings()

            @Property(str, notify=changed)
            def language(self):
                return language()

            @Property(str, notify=changed)
            def locale(self):
                return locale_name()

            @Property(str, notify=changed)
            def choice(self):
                return setting()

            @Property(str, notify=changed)
            def systemName(self):
                return dict(available()).get(system_language(), SOURCE)

            @Property("QVariantList", constant=True)
            def languages(self):
                return [{"code": c, "name": n} for c, n in available()]

            @Slot(str)
            def setLanguage(self, choice):
                set_language(choice)

        _bridge = I18n()
    return _bridge
