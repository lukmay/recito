"""Builds the README images in docs/images/.

    python tools/make_screenshots.py            # shoot light and dark, then compose
    python tools/make_screenshots.py shoot      # only the raw window grabs
    python tools/make_screenshots.py compose    # only the framing

Needs PySide6 (the same one the app uses). Run it with the app's venv.

Shoot stage: opens the real settings window offscreen (QT_QPA_PLATFORM=offscreen)
at device pixel ratio 2, with a throwaway config folder (PIPER_DIR, APPDATA and
XDG_CONFIG_HOME point into a temp dir). Nothing calls save or test, so no
installed setup is read or written. One child process per theme, because the
theme is read once at start.

Compose stage: puts each grab on a 1600x1000 canvas with a brand gradient,
rounded corners and a soft shadow, and writes WebP files.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RAW = REPO / "docs" / "images" / "_raw"
OUT = REPO / "docs" / "images"
SCALE = 2                   # canvas pixels per logical pixel
CAP = 3                     # window grabs are taken at 3x and scaled down, so enlarging stays sharp
THEMES = ("light", "dark")


# --------------------------------------------------------------------------- #
# shoot
# --------------------------------------------------------------------------- #
def shoot(theme: str) -> None:
    out = RAW / theme
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="recito_shots_"))
    conf = tmp / "piper"
    conf.mkdir()
    shutil.copy(REPO / "config/voices.conf", conf / "voices.conf")
    # Piper voices are read-only copies, so the engine cards show real coverage
    src = Path(os.environ.get("APPDATA", "")) / "piper"
    for name in ("de_DE-thorsten-low", "en_US-amy-low"):
        for ext in (".onnx", ".onnx.json"):
            if (src / (name + ext)).exists():
                shutil.copy(src / (name + ext), conf / (name + ext))
    os.environ.update(
        PIPER_DIR=str(conf), APPDATA=str(tmp), XDG_CONFIG_HOME=str(tmp),
        QT_QPA_PLATFORM="offscreen", TTS_UI_LANG="en", TTS_UI_THEME=theme,
        QT_QUICK_CONTROLS_MATERIAL_VARIANT="Normal",
        QT_SCALE_FACTOR=str(CAP),
    )
    if sys.platform == "win32":
        os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
    sys.path.insert(0, str(REPO / "bin"))

    from PySide6.QtCore import QObject, QMetaObject, Q_ARG, qInstallMessageHandler
    from PySide6.QtWidgets import QApplication

    qInstallMessageHandler(lambda *a: None)
    import tts_settings
    tts_settings.init_style()
    app = QApplication(sys.argv)
    sw = tts_settings.SettingsWindow()
    win = sw.window
    win.resize(1000, 800)
    win.show()

    def pump(sec):
        t = time.time()
        while time.time() - t < sec:
            app.processEvents()
            time.sleep(0.01)

    def jsv(v):
        return v.toVariant() if hasattr(v, "toVariant") else v

    def all_items():
        todo, res = [win.contentItem()], []
        while todo:
            it = todo.pop()
            res.append(it)
            todo += it.childItems()
        return res

    def find_type(prefix):
        for it in all_items():
            if it.metaObject().className().startswith(prefix):
                return it
        return None

    def flick():
        best = None
        for it in all_items():
            if it.inherits("QQuickFlickable") and it.isVisible():
                ch = it.property("contentHeight")
                if ch and ch > it.height() + 10 and (best is None or it.width() > best.width()):
                    best = it
        return best

    def scroll_to(y):
        f = flick()
        if f is not None:
            f.setProperty("contentY", max(0, y))

    def scroll_item_into_view(obj_name, pad=16):
        f = flick()
        it = win.findChild(QObject, obj_name)
        if f is None or it is None:
            return
        pos = it.mapToItem(f.property("contentItem"), 0, 0)
        scroll_to(pos.y() - pad)

    def shot(name, sec=0.6, size=(1000, 700)):
        if (win.width(), win.height()) != size:
            win.resize(*size)
            pump(0.5)
        pump(sec)
        win.grabWindow().save(str(out / f"{name}.png"))
        print("shot", theme, name, flush=True)

    def force_english():
        for it in all_items():
            if it.metaObject().className().startswith("LanguageBox"):
                codes = [m.get("code") for m in jsv(it.property("model"))]
                if "en" in codes:
                    it.setProperty("currentIndex", codes.index("en"))

    pump(1.0)
    onb = win.findChild(QObject, "onboarding")
    for _ in range(80):
        if jsv(onb.property("info")) is not None:
            break
        pump(0.2)

    # --- onboarding welcome, mid demo: keys held, words lighting up ---------- #
    demo = find_type("WelcomeDemo")
    if demo is not None:
        parts = jsv(demo.property("parts"))
        demo.setProperty("held", {p: True for p in parts})
        QMetaObject.invokeMethod(demo, "play")
        pump(0.85)
    force_english()
    shot("welcome", 0.0, size=(1000, 800))

    def go_to(name):
        for _ in range(10):
            if jsv(onb.property("stepName")) == name:
                break
            QMetaObject.invokeMethod(onb, "go", Q_ARG("QVariant", 1), Q_ARG("QVariant", True))
            pump(0.5)

    go_to("langs")
    for code in ("de", "fr"):
        QMetaObject.invokeMethod(onb, "toggleLang", Q_ARG("QVariant", code))
    pump(0.3)
    if "de" not in jsv(onb.property("readLangs")):
        QMetaObject.invokeMethod(onb, "toggleLang", Q_ARG("QVariant", "de"))
    shot("onb_langs")
    go_to("engine")
    shot("onb_engine")
    go_to("clone")
    pump(0.5)
    shot("onb_clone")

    win.setProperty("onboardingOpen", False)
    pump(0.5)
    nav = win.findChild(QObject, "nav")
    force_english()

    # --- main window: voice page with engine cards --------------------------- #
    nav.setProperty("currentIndex", 0)
    shot("voice", size=(1040, 780))
    scroll_item_into_view("readLangsSection")
    shot("langs_section")
    scroll_to(0)

    # --- new voice dialog ----------------------------------------------------- #
    nv = win.findChild(QObject, "newVoiceDlg")
    QMetaObject.invokeMethod(nv, "open")
    shot("newvoice")
    QMetaObject.invokeMethod(nv, "close")
    pump(0.4)

    # --- rules page ------------------------------------------------------------ #
    nav.setProperty("currentIndex", 2)
    pump(0.5)
    QMetaObject.invokeMethod(win, "toggleOpen", Q_ARG("QVariant", "user"))
    pump(0.4)
    win.resize(1000, 940)
    pump(0.5)
    scroll_item_into_view("rulesSection", -100)
    shot("rules", size=(1000, 940))

    print("done", flush=True)
    sys.stdout.flush()
    os._exit(0)


# --------------------------------------------------------------------------- #
# compose
# --------------------------------------------------------------------------- #
CANVAS = (1600, 1000)
BOX = (1360, 860)           # largest window area on the canvas
RADIUS = 16
# name, raw grab, crop in logical px of the window (x, y, w, h) or None, box
MOTIFS = [
    ("welcome", "welcome", None, BOX),
    ("onboarding-engine", "onb_engine", None, BOX),
    ("voice", "voice", None, BOX),
    ("new-voice", "newvoice", (200, 140, 600, 420), BOX),
    ("rules", "rules", None, BOX),
    ("languages", "langs_section", (292, 12, 672, 224), (1180, 420)),
]
TRAY_STATES = [("", "Idle"), ("-speaking", "Speaking"), ("-paused", "Paused"), ("-error", "Error")]


def compose() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import (QColor, QFont, QGuiApplication, QImage, QLinearGradient,
                               QPainter, QPainterPath, QRadialGradient)

    app = QGuiApplication.instance() or QGuiApplication(sys.argv)

    def canvas(theme):
        img = QImage(CANVAS[0] * SCALE, CANVAS[1] * SCALE, QImage.Format_RGB32)
        p = QPainter(img)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        p.scale(SCALE, SCALE)
        w, h = CANVAS
        g = QLinearGradient(0, 0, w, h)
        if theme == "light":
            g.setColorAt(0.0, QColor("#5E49A3"))
            g.setColorAt(0.55, QColor("#9179CF"))
            g.setColorAt(1.0, QColor("#EBBAD6"))
        else:
            g.setColorAt(0.0, QColor("#14101C"))
            g.setColorAt(0.6, QColor("#21192F"))
            g.setColorAt(1.0, QColor("#2D2040"))
        p.fillRect(QRectF(0, 0, w, h), g)
        glow = QRadialGradient(w * 0.2, h * 0.1, w * 0.7)
        glow.setColorAt(0, QColor(255, 255, 255, 46 if theme == "light" else 20))
        glow.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(QRectF(0, 0, w, h), glow)
        return img, p

    def shadow(p, rect, theme):
        # stacked translucent rounded rects approximate a large soft blur
        p.setPen(Qt.NoPen)
        steps = 28
        for i in range(steps):
            grow = 46 * (1 - i / steps)
            a = (6 if theme == "light" else 9) * (i + 1) / steps * 1.4
            r = rect.adjusted(-grow, -grow, grow, grow).translated(0, 26)
            p.setBrush(QColor(20, 8, 50, int(a)))
            p.drawRoundedRect(r, RADIUS + grow, RADIUS + grow)

    def window(p, theme, src, crop, box):
        shot = QImage(str(RAW / theme / f"{src}.png"))
        sw, sh = shot.width() / CAP, shot.height() / CAP
        if crop:
            x, y, cw, ch = crop
            shot = shot.copy(int(x * CAP), int(y * CAP), int(cw * CAP), int(ch * CAP))
            sw, sh = cw, ch
        k = min(box[0] / sw, box[1] / sh, 1.5)
        dw, dh = sw * k, sh * k
        rect = QRectF((CANVAS[0] - dw) / 2, (CANVAS[1] - dh) / 2, dw, dh)
        shadow(p, rect, theme)
        path = QPainterPath()
        path.addRoundedRect(rect, RADIUS, RADIUS)
        p.save()
        p.setClipPath(path)
        p.drawImage(rect, shot)
        p.restore()
        # one hairline keeps the dark window readable on the dark canvas
        p.setBrush(Qt.NoBrush)
        p.setPen(QColor(255, 255, 255, 38 if theme == "dark" else 70))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)

    def tray(p, theme):
        bar_w, bar_h = 1120, 230
        rect = QRectF((CANVAS[0] - bar_w) / 2, (CANVAS[1] - bar_h) / 2, bar_w, bar_h)
        shadow(p, rect, theme)
        p.setPen(QColor(255, 255, 255, 40) if theme == "dark" else QColor(255, 255, 255, 120))
        p.setBrush(QColor("#202124") if theme == "dark" else QColor("#F3F1F6"))
        p.drawRoundedRect(rect, RADIUS, RADIUS)
        font = QFont("Segoe UI" if sys.platform == "win32" else "Noto Sans")
        font.setPixelSize(22)
        p.setFont(font)
        cell = bar_w / len(TRAY_STATES)
        for i, (suffix, label) in enumerate(TRAY_STATES):
            cx = rect.left() + cell * (i + 0.5)
            icon = QImage(str(REPO / "assets/icons/tray" / f"tray-{theme}{suffix}-64.png"))
            p.drawImage(QRectF(cx - 40, rect.top() + 44, 80, 80), icon)
            p.setPen(QColor("#CAC4D0") if theme == "dark" else QColor("#49454F"))
            p.drawText(QRectF(cx - 100, rect.top() + 146, 200, 34), Qt.AlignCenter, label)
            p.setPen(Qt.NoPen)

    for theme in THEMES:
        for name, src, crop, box in MOTIFS:
            img, p = canvas(theme)
            window(p, theme, src, crop, box)
            p.end()
            save(img, OUT / f"{name}-{theme}.webp")
        img, p = canvas(theme)
        tray(p, theme)
        p.end()
        save(img, OUT / f"tray-{theme}.webp")
    del app


def save(img, path):
    img.save(str(path), "WEBP", 90)
    print(f"{path.name}: {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode == "child":
        shoot(sys.argv[2])
    else:
        if mode in ("all", "shoot"):
            for t in THEMES:
                subprocess.run([sys.executable, __file__, "child", t], check=True)
        if mode in ("all", "compose"):
            compose()
