#!/usr/bin/env python3
"""Renders the two wizard images of the Windows installer from the brand mark.

    uv run --no-project --with PySide6 python tools/build_installer_art.py

Writes installer/wizard.bmp (side panel, 328x628) and installer/wizard-small.bmp
(110x110, top right corner of the inner pages). Inno Setup scales them to the
screen. The colours come from assets/brand/BRAND.md.
"""
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt                    # noqa: E402
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter   # noqa: E402
from PySide6.QtSvg import QSvgRenderer                   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MARK = ROOT / "assets/brand/logo-mark.svg"
OUT = ROOT / "installer"
PANEL = QColor("#F5F0FA")
INK = QColor("#1D1B20")
VIOLET = QColor("#6750A4")


def render(w: int, h: int, draw) -> QImage:
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(PANEL)
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
    draw(p)
    p.end()
    return img


def main() -> int:
    app = QGuiApplication(sys.argv)                       # noqa: F841 (Qt needs one)
    svg = QSvgRenderer(str(MARK))

    def side(p: QPainter):
        size = 220
        svg.render(p, QRectF((328 - size) / 2, 150, size, size))
        f = QFont("Segoe UI Semibold", 1)
        f.setPixelSize(52)
        p.setFont(f)
        p.setPen(INK)
        p.drawText(QRectF(0, 390, 328, 70), Qt.AlignHCenter | Qt.AlignVCenter, "Recito")
        f.setPixelSize(20)
        f.setFamily("Segoe UI")
        p.setFont(f)
        p.setPen(VIOLET)
        p.drawText(QRectF(0, 460, 328, 36), Qt.AlignHCenter | Qt.AlignVCenter, "Read aloud in any app")

    def small(p: QPainter):
        svg.render(p, QRectF(15, 15, 80, 80))

    OUT.mkdir(exist_ok=True)
    render(328, 628, side).save(str(OUT / "wizard.bmp"), "BMP")
    render(110, 110, small).save(str(OUT / "wizard-small.bmp"), "BMP")
    print("wrote", OUT / "wizard.bmp", OUT / "wizard-small.bmp")
    return 0


if __name__ == "__main__":
    sys.exit(main())
