#!/usr/bin/env python3
"""
Check the UI catalogs in bin/i18n against en.json, the source catalog.

For every other catalog it reports missing keys, extra keys, placeholders
that differ from English ({name} sets), em dashes, and values still equal to
the English text (leftover English) unless the key is listed in SAME_OK.
It also reports keys the code uses literally (Tr.t("...") in QML,
tr("...") in Python) that en.json lacks.

    python scripts/check_i18n.py          # exit code 1 when something is wrong
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOGS = ROOT / "bin" / "i18n"
SOURCE = "en"
META_FIELDS = ("name", "locale", "decimal")

# Values that may equal the English text: names, key caps, units and words
# that are the same in that language.
SAME_ALWAYS = re.compile(r"^(key\.|app\.name$|tray\.tooltip$|engine\.\w+\.title$|engine\.mssp\.menu$"
                         r"|rules\.group\.(obsidian|markdown)$|follow\.color\.(magenta|orange|cyan)$)")
SAME_OK = {
    "de": {"lang.auto", "card.online", "card.offline", "el.optional", "el.err.http", "pause.label",
           "player.pause", "voice.name", "voice.audioFiles", "trait.warm", "onb.hw.system",
           "beta.tag", "tray.beta"},
    "fr": {"pause.label", "player.pause", "voice.source", "voice.audioFiles"},
    "es": {"el.err.http", "el.err.generic", "voice.audioFiles", "onb.speed.normal", "beta.tag",
           "tray.beta"},
    "it": {"card.online", "card.offline", "el.err.http", "hotkey.stop", "player.stop",
           "voice.srcFile", "voice.audioFiles", "tray.stop", "beta.tag", "tray.beta"},
}
PLACEHOLDER = re.compile(r"\{(\w+)\}")
# a whole literal key; "prefix." + name concatenations are skipped
CODE_KEY = re.compile(r"""(?:\bTr\.t|\btr)\(\s*["']([a-zA-Z][\w.-]*[\w-])["']""")


def load(code):
    return json.loads((CATALOGS / f"{code}.json").read_text(encoding="utf-8"))


def codes():
    return sorted(p.stem for p in CATALOGS.glob("*.json"))


def only_symbols(text):
    """True for values without words, e.g. '{ms} ms' or '{x}×'."""
    return not re.search(r"[^\W\d_]{3,}", PLACEHOLDER.sub("", text))


def check_catalog(code, src=None):
    """Problems of one catalog as a dict of lists."""
    src = src if src is not None else load(SOURCE)
    cat = load(code)
    keys = [k for k in src if k != "_meta"]
    have = [k for k in cat if k != "_meta"]
    out = {
        "missing": [k for k in keys if k not in cat],
        "extra": [k for k in have if k not in src],
        "placeholders": [],
        "dashes": [k for k in have if "—" in str(cat[k])],
        "english": [],
        "meta": [f for f in META_FIELDS if not (cat.get("_meta") or {}).get(f)],
    }
    for k in keys:
        if k not in cat:
            continue
        if set(PLACEHOLDER.findall(src[k])) != set(PLACEHOLDER.findall(cat[k])):
            out["placeholders"].append(k)
        if (code != SOURCE and cat[k] == src[k] and not SAME_ALWAYS.match(k)
                and k not in SAME_OK.get(code, ()) and not only_symbols(src[k])):
            out["english"].append(k)
    return out


def code_keys():
    """Translation keys the code uses as string literals."""
    found = set()
    for pattern in ("bin/*.py", "bin/*.qml", "bin/ui/*.qml"):
        for p in ROOT.glob(pattern):
            found.update(CODE_KEY.findall(p.read_text(encoding="utf-8")))
    return found


def check_all():
    src = load(SOURCE)
    report = {code: check_catalog(code, src) for code in codes()}
    missing = sorted(k for k in code_keys() if k not in src)
    return report, missing


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    report, missing = check_all()
    bad = False
    for code, problems in report.items():
        lines = [f"  {kind}: {', '.join(keys)}" for kind, keys in problems.items() if keys]
        name = load(code).get("_meta", {}).get("name", code)
        print(f"{code} ({name}): {'ok' if not lines else 'problems'}")
        for ln in lines:
            print(ln)
        bad = bad or bool(lines)
    if missing:
        bad = True
        print("used in the code but missing in en.json: " + ", ".join(missing))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
