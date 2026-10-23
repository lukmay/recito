"""
tts_follow: the follow window. While Recito reads, a small window floats
above all others, shows the text being read and marks the word you hear, so
the eyes can follow the voice.

The window never takes the focus, so the selection and the cursor stay in
the app the text came from. It opens when a job starts, scrolls along with
the voice and fades out right after the end, at once after a stop. While
reading is paused it fades out and comes back on resume. Scrolling by hand
stops the automatic scrolling for a moment. The close button hides it until
the next text; the settings window and the tray menu turn it off
(follow_window=off in voices.conf). The look button opens the mark color
(follow_color) and the background opacity (follow_opacity, in percent; text
and mark stay opaque).

The look follows Adobe Spectrum 2 (tokens in tts_theme): a popover with the
elevated shadow, quiet round action buttons with line icons, the Spectrum
gray scale for text and a color swatch picker.

A worker thread asks the daemon {"cmd": "follow"} several times a second
while it speaks and a few times a second while it is idle. The daemon sends
the text once per job and then only the span of the word being heard
(tts_timing estimates it from the playback position).
"""

import json
import socket
import threading
import time

import tts_platform
import tts_theme
from tts_i18n import tr

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QEvent, QObject, QPointF,
                            QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import (QColor, QCursor, QFont, QGuiApplication, QImage, QPainter,
                           QPainterPath, QPen, QPixmap, QTextBlockFormat, QTextCharFormat,
                           QTextCursor)
from PySide6.QtWidgets import (QAbstractButton, QFrame, QGraphicsBlurEffect,
                               QGraphicsDropShadowEffect,
                               QGraphicsPixmapItem, QGraphicsScene, QHBoxLayout, QLabel,
                               QSizeGrip, QSlider, QTextBrowser, QTextEdit, QVBoxLayout,
                               QWidget)

HIDE_AFTER_MS = 800             # after the end of a text
USER_SCROLL_HOLD = 3.0          # seconds without automatic scrolling after the wheel
DEFAULT_SIZE = (720, 220)

COLOR_NAMES = {                 # literal keys, so check_i18n sees them
    "blue": "follow.color.blue", "purple": "follow.color.purple",
    "magenta": "follow.color.magenta", "orange": "follow.color.orange",
    "yellow": "follow.color.yellow", "green": "follow.color.green",
    "cyan": "follow.color.cyan",
}


def enabled() -> bool:
    return tts_platform.conf_read().get("follow_window", "on").lower() not in (
        "off", "0", "no", "false", "aus")


def mark_name() -> str:
    name = tts_platform.conf_read().get("follow_color", tts_theme.DEFAULT_MARK).lower()
    return name if name in tts_theme.MARKS else tts_theme.DEFAULT_MARK


def opacity() -> int:
    """Background opacity in percent, within tts_theme.OPACITY."""
    o = tts_theme.OPACITY
    try:
        value = int(tts_platform.conf_read().get("follow_opacity", o["default"]))
    except ValueError:
        value = o["default"]
    return max(o["min"], min(o["max"], value))


def query_follow(job, timeout=1.0):
    """The daemon's follow reply, None if it is not reachable."""
    try:
        s = tts_platform.ipc_connect(timeout=timeout)
    except OSError:
        return None
    try:
        s.sendall(json.dumps({"cmd": "follow", "job": job}).encode("utf-8"))
        s.shutdown(socket.SHUT_WR)
        data = bytearray()
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
        reply = json.loads(bytes(data) or b"{}")
        return reply if isinstance(reply, dict) else None
    except (OSError, ValueError):
        return None
    finally:
        s.close()


class FollowWatcher(QObject):
    """Polls the daemon in a worker thread and hands the replies to the GUI
    thread."""
    update = Signal(dict)
    FAST = 0.08                 # seconds between polls while a text is read
    SLOW = 0.35                 # while idle: how soon a new text shows up
    DOWN = 2.0                  # while the daemon is not reachable

    def __init__(self):
        super().__init__()
        self._job = None
        self._wake = threading.Event()
        self._quit = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is None or not self._thread.is_alive():
            self._quit.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self):
        self._quit.set()
        self._wake.set()

    def poke(self):
        self._wake.set()

    def _loop(self):
        while not self._quit.is_set():
            reply = query_follow(self._job)
            if reply is None:
                self._job = None        # a restarted daemon sends the text again
                wait = self.DOWN
            else:
                if "text" in reply or reply.get("job") is None:
                    self._job = reply.get("job")
                self.update.emit(reply)
                wait = self.FAST if reply.get("state") in ("speaking", "paused") else self.SLOW
            self._wake.wait(wait)
            self._wake.clear()


# --------------------------------------------------------------------------- #
#  Controls: quiet action buttons and color swatches
# --------------------------------------------------------------------------- #
def _icon_path(kind: str) -> QPainterPath:
    """Line icons on a 20 px grid in the style of the Spectrum 2 workflow icons."""
    p = QPainterPath()
    if kind == "pause":
        p.addRoundedRect(QRectF(5, 4, 3.5, 12), 1.25, 1.25)
        p.addRoundedRect(QRectF(11.5, 4, 3.5, 12), 1.25, 1.25)
    elif kind == "play":
        p.moveTo(6.5, 4.5)
        p.lineTo(15.5, 10)
        p.lineTo(6.5, 15.5)
        p.closeSubpath()
    elif kind == "stop":
        p.addRoundedRect(QRectF(5, 5, 10, 10), 2, 2)
    elif kind == "close":
        p.moveTo(5.5, 5.5)
        p.lineTo(14.5, 14.5)
        p.moveTo(14.5, 5.5)
        p.lineTo(5.5, 14.5)
    return p


class _ActionButton(QAbstractButton):
    """Quiet, round icon button (Spectrum ActionButton, isQuiet, size M)."""

    def __init__(self, kind, window):
        super().__init__()
        self.kind = kind
        self._win = window
        side = tts_theme.SIZE["control"]
        self.setFixedSize(side, side)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)

    def sizeHint(self):
        return QSize(tts_theme.SIZE["control"], tts_theme.SIZE["control"])

    def paintEvent(self, _ev):
        c = self._win.colors
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.isDown() or self.isChecked():
            p.setBrush(QColor(c["pressed"]))
        elif self.underMouse():
            p.setBrush(QColor(c["hover"]))
        else:
            p.setBrush(Qt.NoBrush)
        p.setPen(Qt.NoPen)
        p.drawEllipse(self.rect())
        icon = tts_theme.SIZE["icon"]
        off = (self.width() - icon) / 2
        p.translate(off, off)
        if self.kind == "color":
            ring = QRectF(3, 3, 14, 14)
            p.setBrush(QColor(self._win.mark_color))
            p.setPen(QPen(QColor(c["swatch_border"]), 1))
            p.drawEllipse(ring)
            return
        pen = QPen(QColor(c["content"]), tts_theme.ICON_STROKE)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(_icon_path(self.kind))


class _Swatch(QAbstractButton):
    """One color of the swatch picker; the selected one carries a ring."""

    def __init__(self, name, window):
        super().__init__()
        self.name = name
        self._win = window
        self.setCheckable(True)
        side = tts_theme.SIZE["control"]
        self.setFixedSize(side, side)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tr(COLOR_NAMES[name]))

    def sizeHint(self):
        return QSize(tts_theme.SIZE["control"], tts_theme.SIZE["control"])

    def paintEvent(self, _ev):
        c = self._win.colors
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        sw = tts_theme.SIZE["swatch"]
        off = (self.width() - sw) / 2
        r = tts_theme.RADIUS["swatch"]
        if self.isChecked():
            p.setPen(QPen(QColor(c["ring"]), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 2), r + 2, r + 2)
        p.setPen(QPen(QColor(c["swatch_border"]), 1))
        p.setBrush(QColor(tts_theme.mark_color(self.name)))
        p.drawRoundedRect(QRectF(off + 0.5, off + 0.5, sw - 1, sw - 1), r, r)


class _ReadingView(QTextBrowser):
    """The text, with the word being heard on a rounded mark behind the
    glyphs (an ExtraSelection can only paint square corners)."""

    def __init__(self):
        super().__init__()
        self.mark = None                # (start, end) in the document
        self.mark_color = QColor()

    def _mark_rects(self):
        start, end = self.mark
        doc = self.document()
        block = doc.findBlock(start)
        if not block.isValid() or end <= start:
            return []
        layout = block.layout()
        origin = layout.position()
        bpos = block.position()
        dx = -self.horizontalScrollBar().value()
        dy = -self.verticalScrollBar().value()
        pad = tts_theme.SPACE["xs"] / 2
        rects = []
        for i in range(layout.lineCount()):
            line = layout.lineAt(i)
            ls, le = bpos + line.textStart(), bpos + line.textStart() + line.textLength()
            a, b = max(start, ls), min(end, le)
            if a >= b:
                continue
            x1, x2 = (_x(line.cursorToX(pos - bpos)) for pos in (a, b))
            top = origin.y() + line.y() + dy
            h = line.ascent() + line.descent()
            rects.append(QRectF(origin.x() + x1 + dx - pad, top - pad / 2,
                                x2 - x1 + 2 * pad, h + pad))
        return rects

    def paintEvent(self, ev):
        if self.mark is not None and self.mark_color.isValid():
            p = QPainter(self.viewport())
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(self.mark_color)
            r = tts_theme.RADIUS["mark"]
            for rect in self._mark_rects():
                p.drawRoundedRect(rect, r, r)
            p.end()
        super().paintEvent(ev)


def _x(value):
    """QTextLine.cursorToX returns a float or (x, position), by binding."""
    return value[0] if isinstance(value, tuple) else value


def _shadow(size: QSize, radius: float, blur: float, color: QColor, dy: float) -> QPixmap:
    """Soft shadow of a rounded rectangle, blurred once per size and color."""
    m = int(blur * 2)
    img = QImage(size.width() + 2 * m, size.height() + 2 * m, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(QRectF(m, m + dy, size.width(), size.height()), radius, radius)
    p.end()
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(QPixmap.fromImage(img))
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(blur)
    effect.setBlurHints(QGraphicsBlurEffect.QualityHint)
    item.setGraphicsEffect(effect)
    scene.addItem(item)
    out = QImage(img.size(), QImage.Format_ARGB32_Premultiplied)
    out.fill(Qt.transparent)
    p = QPainter(out)
    scene.render(p, QRectF(out.rect()), QRectF(0, 0, img.width(), img.height()))
    p.end()
    return QPixmap.fromImage(out)


# --------------------------------------------------------------------------- #
#  The window
# --------------------------------------------------------------------------- #
class FollowWindow(QWidget):
    """Frameless window above all others that never takes the focus. The
    panel sits inside a transparent margin that holds its shadow."""

    def __init__(self, send):
        flags = (Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                 | Qt.WindowDoesNotAcceptFocus)
        super().__init__(None, flags)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle(tr("follow.title"))
        self._send = send
        self._job = None
        self._span = None
        self._state = "idle"
        self._dismissed = False         # hidden with the close button until the next text
        self._user_scroll_until = 0.0
        self._placed = False
        self._shadow_key = None
        self._shadow_pix = None
        self._mark = mark_name()
        self._opacity = opacity()
        self.colors = tts_theme.s2_colors()
        self.mark_color = tts_theme.mark_color(self._mark)
        sp, size = tts_theme.SPACE, tts_theme.SIZE

        self.btn_color = _ActionButton("color", self)
        self.btn_color.setCheckable(True)
        self.btn_color.toggled.connect(self._toggle_picker)
        self.btn_pause = _ActionButton("pause", self)
        self.btn_pause.clicked.connect(self._pause)
        self.btn_stop = _ActionButton("stop", self)
        self.btn_stop.clicked.connect(self._stop)
        self.btn_close = _ActionButton("close", self)
        self.btn_close.clicked.connect(self._dismiss)

        self.title = QLabel(tr("app.name"))
        f = QFont(tts_theme.font_family())
        f.setPixelSize(tts_theme.TYPE["title"])
        f.setWeight(QFont.DemiBold)
        self.title.setFont(f)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(sp["xs"])
        head.addWidget(self.title)
        head.addStretch(1)
        for b in (self.btn_color, self.btn_pause, self.btn_stop, self.btn_close):
            head.addWidget(b)
        self.head = QWidget()
        self.head.setLayout(head)
        self.head.setCursor(Qt.SizeAllCursor)
        self.head.installEventFilter(self)

        # Look panel under the header (look button): swatch picker for the
        # mark color and a slider for the background opacity
        swatch_row = QHBoxLayout()
        swatch_row.setContentsMargins(0, 0, 0, 0)
        swatch_row.setSpacing(sp["xs"])
        self.swatches = []
        for name in tts_theme.MARKS:
            s = _Swatch(name, self)
            s.setChecked(name == self._mark)
            s.clicked.connect(lambda _=False, n=name: self._pick(n))
            self.swatches.append(s)
            swatch_row.addWidget(s)
        swatch_row.addStretch(1)
        o = tts_theme.OPACITY
        self.opacity_label = QLabel(tr("follow.opacity"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(o["min"], o["max"])
        self.slider.setSingleStep(o["step"])
        self.slider.setPageStep(o["step"] * 2)
        self.slider.setValue(self._opacity)
        self.slider.setFocusPolicy(Qt.NoFocus)
        self.slider.setCursor(Qt.PointingHandCursor)
        self.slider.setFixedHeight(size["control"])
        self.slider.setAccessibleName(tr("follow.opacity"))
        self.slider.valueChanged.connect(self._set_opacity)
        self.opacity_value = QLabel()
        self.opacity_value.setMinimumWidth(size["control"] + sp["s"])
        self.opacity_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        slider_row = QHBoxLayout()
        slider_row.setContentsMargins(0, 0, 0, 0)
        slider_row.setSpacing(sp["m"])
        slider_row.addWidget(self.opacity_label)
        slider_row.addWidget(self.slider, 1)
        slider_row.addWidget(self.opacity_value)
        picker = QVBoxLayout()
        picker.setContentsMargins(0, 0, 0, 0)
        picker.setSpacing(sp["xs"])
        picker.addLayout(swatch_row)
        picker.addLayout(slider_row)
        self.picker = QWidget()
        self.picker.setLayout(picker)
        self.picker.hide()

        self.view = _ReadingView()
        self.view.setFrameShape(QFrame.NoFrame)
        self.view.setFocusPolicy(Qt.NoFocus)
        self.view.setOpenLinks(False)
        self.view.setTextInteractionFlags(Qt.NoTextInteraction)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        f = QFont(tts_theme.font_family())
        f.setPixelSize(tts_theme.TYPE["reading"])
        self.view.setFont(f)
        self.view.viewport().installEventFilter(self)
        self._scroll_anim = QPropertyAnimation(self.view.verticalScrollBar(), b"value", self)
        self._scroll_anim.setDuration(250)
        self._scroll_anim.setEasingCurve(QEasingCurve.OutCubic)

        self.grip = QSizeGrip(self)
        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 0, 0)
        grip_row.addStretch(1)
        grip_row.addWidget(self.grip, 0, Qt.AlignRight | Qt.AlignBottom)

        m = size["shadow"]
        root = QVBoxLayout(self)
        root.setContentsMargins(m + sp["l"], m + sp["s"], m + sp["s"], m + sp["xs"])
        root.setSpacing(sp["s"])
        root.addWidget(self.head)
        root.addWidget(self.picker)
        root.addWidget(self.view, 1)
        root.addLayout(grip_row)

        self._hide_timer = QTimer(self, singleShot=True, interval=HIDE_AFTER_MS,
                                  timeout=self._hide_if_idle)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.finished.connect(self._fade_done)
        self._save_timer = QTimer(self, singleShot=True, interval=600,
                                  timeout=self._save_geometry)
        self._opacity_timer = QTimer(self, singleShot=True, interval=400,
                                     timeout=self._save_opacity)
        self._apply_colors()
        try:
            QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_scheme)
        except AttributeError:
            pass

    # --- look ------------------------------------------------------------- #
    def _apply_colors(self):
        c = self.colors
        sp = tts_theme.SPACE
        self.setStyleSheet(f"""
QLabel {{ color: {c['title']}; }}
QTextBrowser {{ background: transparent; color: {c['content']}; border: none; }}
QScrollBar:vertical {{ background: transparent; width: {sp['s']}px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {c['pressed']}; border-radius: {sp['xs']}px;
                               min-height: {sp['xl']}px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QSizeGrip {{ image: none; width: {sp['l']}px; height: {sp['l']}px; }}
QSlider::groove:horizontal {{ height: 2px; background: {c['pressed']}; border-radius: 1px; }}
QSlider::sub-page:horizontal {{ height: 2px; background: {c['content']}; border-radius: 1px; }}
QSlider::handle:horizontal {{ width: {sp['m']}px; height: {sp['m']}px;
                             margin: -{sp['s'] - 1}px 0; border-radius: {sp['s']}px;
                             background: {c['elevated']}; border: 2px solid {c['content']}; }}
QSlider::handle:horizontal:hover {{ border-color: {c['on_mark']}; }}
""")
        self.opacity_value.setText(tr("follow.percent", value=self._opacity))
        self._halo()
        self.mark_color = tts_theme.mark_color(self._mark)
        self.view.mark_color = QColor(self.mark_color)
        self._tooltips()
        self._highlight()
        for w in (self.btn_color, self.btn_pause, self.btn_stop, self.btn_close, *self.swatches):
            w.update()
        self._shadow_key = None
        self._build_shadow()
        self.update()

    def _tooltips(self):
        self.btn_color.setToolTip(tr("follow.look"))
        self.btn_pause.setToolTip(tr("follow.resume") if self._state == "paused"
                                  else tr("follow.pause"))
        self.btn_stop.setToolTip(tr("follow.stop"))
        self.btn_close.setToolTip(tr("follow.hide"))

    def _on_scheme(self, *_):
        self.colors = tts_theme.s2_colors()
        self._apply_colors()

    def _panel_rect(self) -> QRectF:
        m = tts_theme.SIZE["shadow"]
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def _build_shadow(self):
        """drop-shadow-elevated: 0 4 12 ambient, then 0 0 2 key. Built outside
        paintEvent: the blur effect renders nothing inside another paint."""
        c = self.colors
        size = self._panel_rect().size().toSize()
        key = (size, c["shadow"], c["shadow_ambient"])
        if key == self._shadow_key or size.isEmpty():
            return
        self._shadow_key = key
        r = tts_theme.RADIUS["window"]
        self._shadow_pix = [(blur, _shadow(size, r, blur, QColor(c[role]), dy))
                            for blur, dy, role in ((12, 4, "shadow_ambient"), (2, 0, "shadow"))]

    def paintEvent(self, _ev):
        c = self.colors
        panel = self._panel_rect()
        r = tts_theme.RADIUS["window"]
        alpha = self._opacity / 100
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(alpha)                 # the shadow fades with the background
        for blur, pix in self._shadow_pix or ():
            p.drawPixmap(QPointF(panel.x() - blur * 2, panel.y() - blur * 2), pix)
        p.setOpacity(1.0)
        bg = QColor(c["elevated"])
        bg.setAlphaF(alpha)
        p.setBrush(bg)
        border = QColor(c["border"])
        if alpha < 1 and not border.alpha():
            border = QColor(c["swatch_border"])   # a see-through panel keeps its edge
        p.setPen(QPen(border, 1) if border.alpha() else Qt.NoPen)
        p.drawRoundedRect(panel.adjusted(0.5, 0.5, -0.5, -0.5), r, r)

    # --- mark color ------------------------------------------------------- #
    def _toggle_picker(self, on):
        self.picker.setVisible(on)

    def _set_opacity(self, value):
        o = tts_theme.OPACITY
        value = round(value / o["step"]) * o["step"]
        if value == self._opacity:
            return
        self._opacity = value
        self.opacity_value.setText(tr("follow.percent", value=value))
        self._halo()
        self.update()
        self._opacity_timer.start()           # write once the slider rests

    def _halo(self):
        """A see-through panel lets the app behind it show through the text.
        A soft glow in the panel color around the glyphs keeps the text
        readable on any background; an opaque panel needs none."""
        if self._opacity >= tts_theme.OPACITY["max"]:
            self.view.setGraphicsEffect(None)
            return
        effect = self.view.graphicsEffect()
        if not isinstance(effect, QGraphicsDropShadowEffect):
            effect = QGraphicsDropShadowEffect(self.view)
            effect.setOffset(0, 0)
            effect.setBlurRadius(tts_theme.HALO_BLUR)
            self.view.setGraphicsEffect(effect)
        effect.setColor(QColor(self.colors["elevated"]))

    def _save_opacity(self):
        tts_platform.conf_write({"follow_opacity": str(self._opacity)})

    def _pick(self, name):
        self._mark = name
        for s in self.swatches:
            s.setChecked(s.name == name)
        tts_platform.conf_write({"follow_color": name})
        self.btn_color.setChecked(False)
        self._apply_colors()

    # --- daemon replies --------------------------------------------------- #
    def on_update(self, reply: dict):
        state, job = reply.get("state", "idle"), reply.get("job")
        if "text" in reply and job is not None:
            self._set_text(reply["text"])
            # The settings window can change these at any time
            self._job, self._dismissed = job, not enabled()
            self._sync_look()
        if job is None and state != "idle":
            return                      # a preview from the settings window
        if state != self._state:
            self._state = state
            self.btn_pause.kind = "play" if state == "paused" else "pause"
            self.btn_pause.update()
            self._tooltips()
        span = reply.get("span")
        if span and tuple(span) != self._span:
            self._span = tuple(span)
            self._highlight()
        if state == "idle":
            if self._shown() and not self._hide_timer.isActive():
                self._hide_timer.start()
            return
        self._hide_timer.stop()
        if state == "paused":
            self._fade_out()
        elif not self._dismissed and self._job is not None:
            self._fade_in()

    def _set_text(self, text):
        self._span = None
        self.view.setPlainText(text)
        cur = QTextCursor(self.view.document())
        cur.select(QTextCursor.Document)
        fmt = QTextBlockFormat()
        fmt.setLineHeight(tts_theme.READING_LINE_HEIGHT,
                          QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        fmt.setBottomMargin(tts_theme.SPACE["s"])
        cur.mergeBlockFormat(fmt)
        self.view.verticalScrollBar().setValue(0)
        self._highlight()

    def _highlight(self):
        """Read text in a lighter gray, the word being heard on the mark."""
        if self._span is None:
            self.view.mark = None
            self.view.setExtraSelections([])
            return
        c = self.colors
        doc_len = len(self.view.toPlainText())
        start, end = (min(v, doc_len) for v in self._span)

        read = QTextEdit.ExtraSelection()
        read.cursor = QTextCursor(self.view.document())
        read.cursor.setPosition(0)
        read.cursor.setPosition(start, QTextCursor.KeepAnchor)
        read.format = QTextCharFormat()
        read.format.setForeground(QColor(c["content_read"]))

        word = QTextEdit.ExtraSelection()
        word.cursor = QTextCursor(self.view.document())
        word.cursor.setPosition(start)
        word.cursor.setPosition(end, QTextCursor.KeepAnchor)
        word.format = QTextCharFormat()
        word.format.setForeground(QColor(c["on_mark"]))
        self.view.mark = (start, end)
        self.view.setExtraSelections([read, word])
        self.view.viewport().update()
        if end > start:
            self._follow(word.cursor)

    def _follow(self, cursor):
        """Keep the word in the upper part of the view, unless the user just
        scrolled by hand."""
        if time.monotonic() < self._user_scroll_until:
            return
        c = QTextCursor(cursor)
        c.setPosition(cursor.selectionStart())
        rect = self.view.cursorRect(c)
        h = self.view.viewport().height()
        if h * 0.15 <= rect.top() and rect.bottom() <= h * 0.7:
            return
        bar = self.view.verticalScrollBar()
        goal = max(bar.minimum(), min(bar.maximum(), bar.value() + rect.top() - int(h * 0.3)))
        if self._scroll_anim.state() == QAbstractAnimation.Running:
            if self._scroll_anim.endValue() == goal:
                return
            self._scroll_anim.stop()
        self._scroll_anim.setStartValue(bar.value())
        self._scroll_anim.setEndValue(goal)
        self._scroll_anim.start()

    # --- showing and hiding ----------------------------------------------- #
    def _sync_look(self):
        """Mark color and opacity as stored, also after the settings window."""
        name, value = mark_name(), opacity()
        if value != self._opacity:
            self.slider.blockSignals(True)
            self.slider.setValue(value)
            self.slider.blockSignals(False)
            self._opacity = value
        if name != self._mark:
            self._mark = name
            for s in self.swatches:
                s.setChecked(s.name == name)
        self._apply_colors()

    def _shown(self) -> bool:
        """Visible and not on the way out."""
        return self.isVisible() and not (
            self._fade.state() == QAbstractAnimation.Running and self._fade.endValue() == 0.0)

    def _animate(self, to: float, ms: int, curve):
        self._fade.stop()
        self._fade.setDuration(ms)
        self._fade.setEasingCurve(curve)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(to)
        self._fade.start()

    def _fade_in(self):
        if self._shown():
            return
        if not self._placed:
            self._place()
            self._placed = True
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
            self.raise_()
        self._animate(1.0, tts_theme.FADE_IN_MS, QEasingCurve.OutCubic)

    def _fade_out(self):
        if self._shown():
            self._animate(0.0, tts_theme.FADE_OUT_MS, QEasingCurve.InCubic)

    def _fade_done(self):
        if self._fade.endValue() == 0.0:
            self.btn_color.setChecked(False)
            self.hide()
            self.setWindowOpacity(1.0)

    def _place(self):
        """Last position if it is still on a screen, else bottom center of the
        screen with the mouse."""
        geo = tts_platform.conf_read().get("follow_geometry", "")
        try:
            x, y, w, h = (int(v) for v in geo.split(","))
            rect = QRect(x, y, w, h)
            if QGuiApplication.screenAt(rect.center()) is not None:
                self.setGeometry(rect)
                return
        except ValueError:
            pass
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        m = tts_theme.SIZE["shadow"]
        margin = tts_theme.SPACE["xxl"]
        w = min(DEFAULT_SIZE[0], area.width() - 2 * margin) + 2 * m
        h = DEFAULT_SIZE[1] + 2 * m
        self.setGeometry(area.x() + (area.width() - w) // 2,
                         area.bottom() - h - margin + m, w, h)

    def _hide_if_idle(self):
        if self._state != "idle":
            return
        if self.underMouse():           # someone is still looking at it
            self._hide_timer.start()
            return
        self._fade_out()

    def _pause(self):
        self._send({"cmd": "pause"})
        if self._state == "speaking":   # respond at once, the poll confirms
            self._fade_out()

    def _stop(self):
        self._send({"cmd": "stop"})
        self._hide_timer.stop()
        self._fade_out()

    def _dismiss(self):
        self._dismissed = True
        self._hide_timer.stop()
        self._fade_out()

    # --- moving, resizing, scrolling -------------------------------------- #
    def eventFilter(self, obj, ev):
        if obj is self.head and ev.type() == QEvent.MouseButtonPress \
                and ev.button() == Qt.LeftButton:
            handle = self.windowHandle()
            if handle is not None:
                handle.startSystemMove()  # works on Wayland too
            return True
        if obj is self.view.viewport() and ev.type() == QEvent.Wheel:
            self._user_scroll_until = time.monotonic() + USER_SCROLL_HOLD
            self._scroll_anim.stop()
        return super().eventFilter(obj, ev)

    def moveEvent(self, ev):
        super().moveEvent(ev)
        if self.isVisible():
            self._save_timer.start()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._build_shadow()
        if self.isVisible():
            self._save_timer.start()

    def _save_geometry(self):
        g = self.geometry()
        tts_platform.conf_write({"follow_geometry": f"{g.x()},{g.y()},{g.width()},{g.height()}"})


class Follow:
    """Watcher plus window, switched on and off from the tray."""

    def __init__(self, send):
        self.window = FollowWindow(send)
        self.watcher = FollowWatcher()
        self.watcher.update.connect(self.window.on_update)
        # Runs also while the window is off: the switch in the settings window
        # takes effect with the next text, without a restart.
        self.watcher.start()

    def set_enabled(self, on: bool):
        tts_platform.conf_write({"follow_window": "on" if on else "off"})
        if not on:
            self.window._dismiss()

    def poke(self):
        self.watcher.poke()

    def stop(self):
        self.watcher.stop()
