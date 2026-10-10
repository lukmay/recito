#!/usr/bin/env python3
"""
Render the app and tray icons from the SVG sources in assets/brand/ into
assets/icons/ (generated, committed).

    python tools/build_icons.py            # build
    python tools/build_icons.py --check    # rebuild into a temp dir, fail on drift

Output:
    app.ico                                   Windows, 16-256 px layers
    hicolor/<N>x<N>/apps/recito.png         Linux icon theme
    hicolor/scalable/apps/recito.svg
    hicolor/scalable/status/recito-tray[-<state>]-symbolic.svg
                                              recolorable by Plasma and GNOME
    tray/tray-<variant>[-<state>]-<N>.png     rasters for the Windows tray
    logo-mark.svg                             settings sidebar logo

Needs only PySide6 (QtSvg). The ICO is written here, no Pillow. The same Qt
version gives byte-identical output.
"""

import argparse
import filecmp
import os
import re
import shutil
import struct
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BRAND = REPO / "assets" / "brand"
OUT = REPO / "assets" / "icons"

APP_NAME = "recito"
TRAY_NAME = "recito-tray"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 256)
HICOLOR_SIZES = (16, 22, 24, 32, 48, 64, 128, 256)
TRAY_SIZES = (16, 20, 24, 32, 40, 48, 64)
TRAY_VARIANTS = ("light", "dark", "symbolic")
TRAY_STATES = ("", "speaking", "paused", "error")     # "" = idle

SVG_NS = "http://www.w3.org/2000/svg"


# --------------------------------------------------------------------------- #
#  Rendering
# --------------------------------------------------------------------------- #
_app = None


def _qt():
    """Offscreen QGuiApplication (QtSvg needs one for text and fonts)."""
    global _app
    from PySide6.QtGui import QGuiApplication
    if QGuiApplication.instance() is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        _app = QGuiApplication([sys.argv[0]])
    return _app


def render(svg: Path, size: int):
    """SVG -> QImage (ARGB32, size x size, aspect ratio kept, centered)."""
    _qt()
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    renderer = QSvgRenderer(str(svg))
    if not renderer.isValid():
        raise ValueError(f"invalid SVG: {svg}")
    renderer.setAspectRatioMode(Qt.KeepAspectRatio)
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    renderer.render(p, QRectF(0, 0, size, size))
    p.end()
    return img.convertToFormat(QImage.Format_ARGB32)


def png_bytes(img) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(data)


def _bgra_rows(img):
    """Rows of raw BGRA bytes, top to bottom (ARGB32 is BGRA in memory)."""
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    raw = bytes(img.constBits())[:bpl * h]
    return [raw[y * bpl:y * bpl + w * 4] for y in range(h)]


def dib_bytes(img) -> bytes:
    """32-bit BMP layer for an ICO: header, bottom-up BGRA, AND mask."""
    w, h = img.width(), img.height()
    rows = _bgra_rows(img)
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    xor = b"".join(reversed(rows))
    mask_stride = ((w + 31) // 32) * 4
    mask = bytearray()
    for row in reversed(rows):
        line = bytearray(mask_stride)
        for x in range(w):
            if row[x * 4 + 3] == 0:                 # fully transparent
                line[x // 8] |= 0x80 >> (x % 8)
        mask += line
    return header + xor + bytes(mask)


def ico_bytes(layers) -> bytes:
    """layers: [(size, image bytes)]. PNG for 256 px, DIB below."""
    out = struct.pack("<HHH", 0, 1, len(layers))
    offset = 6 + 16 * len(layers)
    entries, blobs = b"", b""
    for size, blob in layers:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        blobs += blob
        offset += len(blob)
    return out + entries + blobs


# --------------------------------------------------------------------------- #
#  Source selection
# --------------------------------------------------------------------------- #
def pick_source(brand: Path, stem: str, size: int) -> Path:
    """Smallest size-tuned variant <stem>-<N>.svg with size <= N <= size + 4
    (20 and 22 use the 24 drawing, as BRAND.md asks), else the master."""
    for n in range(size, size + 5):
        cand = brand / f"{stem}-{n}.svg"
        if cand.is_file():
            return cand
    return brand / f"{stem}.svg"


# --------------------------------------------------------------------------- #
#  Symbolic SVG for Plasma and GNOME
# --------------------------------------------------------------------------- #
# Plasma recolors an SVG only if it carries <style id="current-color-scheme">
# and its shapes paint with currentColor plus a ColorScheme-* class. GNOME
# replaces the fill of -symbolic icons itself, so the same file serves both.
_STATE_CLASSES = {"error": "ColorScheme-NegativeText", "warning": "ColorScheme-NeutralText",
                  "success": "ColorScheme-PositiveText"}
_SHAPES = {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "use"}
_STYLE = (".ColorScheme-Text{color:#232629;}.ColorScheme-NegativeText{color:#da4453;}"
          ".ColorScheme-NeutralText{color:#f67400;}.ColorScheme-PositiveText{color:#27ae60;}")


def _paint(value):
    v = (value or "").strip()
    return v and v != "none" and not v.startswith("url(")


def symbolic_svg(src: Path) -> bytes:
    """The source as is if it already follows the Plasma convention, else a
    converted copy (colours to currentColor plus a ColorScheme class)."""
    raw = src.read_bytes()
    if b'id="current-color-scheme"' in raw:
        return raw
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    root = ET.parse(src).getroot()
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag in ("style", "defs", "title", "desc", "metadata"):
            continue
        classes = (el.get("class") or "").split()
        scheme = next((_STATE_CLASSES[c] for c in classes if c in _STATE_CLASSES),
                      "ColorScheme-Text")
        touched = False
        for attr in ("fill", "stroke"):
            if _paint(el.get(attr)):
                el.set(attr, "currentColor")
                touched = True
        style = el.get("style")
        if style:
            new = re.sub(r"(fill|stroke)\s*:\s*(?!none|url\()[^;]+", r"\1:currentColor", style)
            touched |= new != style
            el.set("style", new)
        if tag in _SHAPES and el.get("fill") is None and "fill:" not in (style or ""):
            el.set("fill", "currentColor")            # default black -> scheme color
            touched = True
        if touched and scheme not in classes:
            el.set("class", " ".join(classes + [scheme]))
    # The style element goes first, as KIconLoader expects.
    st = ET.Element(f"{{{SVG_NS}}}style", {"id": "current-color-scheme", "type": "text/css"})
    st.text = _STYLE
    for old in [e for e in root if e.get("id") == "current-color-scheme"]:
        root.remove(old)
    root.insert(0, st)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


# --------------------------------------------------------------------------- #
#  Build
# --------------------------------------------------------------------------- #
def _write(path: Path, data: bytes, written: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    written.append(path)


def build(brand: Path = BRAND, out: Path = OUT) -> list:
    """Render everything; returns the written paths. Missing tray variants or
    states are skipped with a note; the app master icon is required."""
    master = brand / "app-icon.svg"
    if not master.is_file():
        raise FileNotFoundError(f"missing {master}")
    written = []

    ico = [(n, png_bytes(render(pick_source(brand, "app-icon", n), n)) if n >= 256
            else dib_bytes(render(pick_source(brand, "app-icon", n), n)))
           for n in ICO_SIZES]
    _write(out / "app.ico", ico_bytes(ico), written)

    for n in HICOLOR_SIZES:
        img = render(pick_source(brand, "app-icon", n), n)
        _write(out / "hicolor" / f"{n}x{n}" / "apps" / f"{APP_NAME}.png", png_bytes(img), written)
    _write(out / "hicolor" / "scalable" / "apps" / f"{APP_NAME}.svg", master.read_bytes(), written)
    if (brand / "logo-mark.svg").is_file():           # settings sidebar
        _write(out / "logo-mark.svg", (brand / "logo-mark.svg").read_bytes(), written)

    for variant in TRAY_VARIANTS:
        for state in TRAY_STATES:
            stem = f"tray-{variant}" + (f"-{state}" if state else "")
            if not (brand / f"{stem}.svg").is_file():
                print(f"note: {stem}.svg not found, skipped", file=sys.stderr)
                continue
            for n in TRAY_SIZES:
                img = render(pick_source(brand, stem, n), n)
                _write(out / "tray" / f"{stem}-{n}.png", png_bytes(img), written)
            if variant == "symbolic":
                name = TRAY_NAME + (f"-{state}" if state else "") + "-symbolic.svg"
                _write(out / "hicolor" / "scalable" / "status" / name,
                       symbolic_svg(brand / f"{stem}.svg"), written)
    return written


def check(brand: Path = BRAND, out: Path = OUT) -> list:
    """Rebuild into a temp dir; returns the files that differ or are missing."""
    with tempfile.TemporaryDirectory() as tmp:
        fresh = build(brand, Path(tmp))
        drift = []
        for p in fresh:
            rel = p.relative_to(tmp)
            if not (out / rel).is_file() or not filecmp.cmp(p, out / rel, shallow=False):
                drift.append(str(rel))
        return drift


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--brand", type=Path, default=BRAND, help="SVG source folder")
    ap.add_argument("--out", type=Path, default=OUT, help="output folder")
    ap.add_argument("--check", action="store_true", help="fail if the output is stale")
    args = ap.parse_args(argv)
    if args.check:
        drift = check(args.brand, args.out)
        for rel in drift:
            print(f"stale: {rel}")
        return 1 if drift else 0
    if args.out.exists():
        # Remove old output so renamed or dropped states do not linger.
        for sub in ("hicolor", "tray"):
            shutil.rmtree(args.out / sub, ignore_errors=True)
    written = build(args.brand, args.out)
    print(f"{len(written)} files in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
