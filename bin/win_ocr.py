# -*- coding: utf-8 -*-
"""
Windows OCR: drag a screen region -> screenshot -> text.

Recognition uses the built-in Windows OCR (`Windows.Media.Ocr`, the engine
behind PowerToys Text Extractor) via `winsdk`. No external tesseract is
needed, and it uses the installed OCR languages (e.g. de-DE and en-US). If
winsdk or Windows OCR is unavailable, tesseract is used when installed.

Flow: a frameless, semi-transparent overlay spans all monitors, the user drags
a rectangle, the region is grabbed from the matching screen (high-DPI aware,
Qt carries the device pixel ratio) and saved as PNG for OCR. Must run in the
Qt GUI thread (selection overlay)."""

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QEventLoop, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

import tts_platform


class _RegionSelector(QWidget):
    """Overlay across the whole virtual desktop; returns the dragged rectangle
    in global (logical) coordinates, or None."""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool)
        # Without this attribute the widget background is opaque grey. With it
        # the desktop shows through and only the dimming is painted on top.
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        # Span all monitors. WindowFullScreen covers only one screen and
        # conflicts with the multi-monitor geometry.
        geo = QRect()
        for screen in QGuiApplication.screens():
            geo = geo.united(screen.geometry())
        self.setGeometry(geo)
        self._origin_global = geo.topLeft()
        self._start = None
        self._end = None
        self.result = None
        self._loop = QEventLoop()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        if self._start and self._end:
            sel = QRect(self._start, self._end).normalized()
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            p.fillRect(sel, Qt.transparent)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            p.setPen(QPen(QColor(80, 170, 255), 2))
            p.drawRect(sel)

    def mousePressEvent(self, ev):
        self._start = ev.position().toPoint()
        self._end = self._start
        self.update()

    def mouseMoveEvent(self, ev):
        self._end = ev.position().toPoint()
        self.update()

    def mouseReleaseEvent(self, ev):
        self._end = ev.position().toPoint()
        sel = QRect(self._start, self._end).normalized()
        if sel.width() >= 4 and sel.height() >= 4:
            tl = sel.topLeft() + self._origin_global
            self.result = QRect(tl, sel.size())
        self.close()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.close()

    def closeEvent(self, ev):
        self._loop.quit()
        super().closeEvent(ev)

    def select(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self._loop.exec()
        return self.result


def _select_region():
    return _RegionSelector().select()


def _grab(rect: QRect) -> Path:
    """Grab the region and save it as PNG -> path. Cropped in pixmap pixel
    space, so high-DPI screens work."""
    screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
    g = screen.geometry()
    full = screen.grabWindow(0)
    dpr = full.devicePixelRatio() or 1.0
    x = round((rect.x() - g.x()) * dpr)
    y = round((rect.y() - g.y()) * dpr)
    w = round(rect.width() * dpr)
    h = round(rect.height() * dpr)
    crop = full.copy(x, y, w, h)
    out = Path(tempfile.gettempdir()) / "tts-ocr.png"
    crop.save(str(out), "PNG")
    return out


async def _windows_ocr_async(path: str) -> str:
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.storage import StorageFile, FileAccessMode
    file = await StorageFile.get_file_from_path_async(path)
    stream = await file.open_async(FileAccessMode.READ)
    decoder = await BitmapDecoder.create_async(stream)
    bmp = await decoder.get_software_bitmap_async()
    eng = OcrEngine.try_create_from_user_profile_languages()
    if eng is None:
        langs = list(OcrEngine.available_recognizer_languages)
        eng = OcrEngine.try_create_from_language(langs[0]) if langs else None
    if eng is None:
        return ""
    result = await eng.recognize_async(bmp)
    return result.text or ""


def _windows_ocr(path: str) -> str:
    """Built-in Windows OCR (Windows.Media.Ocr)."""
    return asyncio.run(_windows_ocr_async(path))


def _tesseract(path: str, lang: str) -> str:
    """Fallback when Windows OCR is not available."""
    exe = shutil.which("tesseract")
    if not exe:
        cand = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / \
            "Tesseract-OCR" / "tesseract.exe"
        exe = str(cand) if cand.exists() else None
    if not exe:
        return ""
    res = tts_platform.run_hidden([exe, path, "stdout", "-l", lang],
                                  capture_output=True, text=True, check=False)
    return res.stdout.strip()


def tesseract_langs(wanted):
    """'deu+fra' -> only the packs tesseract has installed ('eng' if none)."""
    exe = shutil.which("tesseract") or ""
    try:
        res = tts_platform.run_hidden([exe, "--list-langs"], capture_output=True,
                                      text=True, check=False, timeout=10)
        have = {ln.strip() for ln in res.stdout.splitlines()}
    except Exception:
        return wanted
    keep = [c for c in wanted.split("+") if c in have]
    return "+".join(keep) or ("eng" if "eng" in have else wanted)


def capture_text(lang="deu+eng") -> str:
    """Select a region, run OCR, return the text ('' if cancelled).
    Prefers the built-in Windows OCR; tesseract is the fallback."""
    rect = _select_region()
    if rect is None:
        return ""
    png = _grab(rect)
    try:
        try:
            text = _windows_ocr(str(png))
            if text.strip():
                return text.strip()
        except Exception:
            pass
        return _tesseract(str(png), tesseract_langs(lang)).strip()
    finally:
        try:
            png.unlink()
        except OSError:
            pass
