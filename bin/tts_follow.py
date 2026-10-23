"""
tts_follow: the follow window. While Recito reads, a small window floats
above all others, shows the text being read and marks the word you hear, so
the eyes can follow the voice.

The window never takes the focus, so the selection and the cursor stay in
the app the text came from. It opens when a job starts, scrolls along with
the voice and fades out right after the end, at once after a stop.
Scrolling by hand stops the automatic scrolling for a moment. The close
button hides it until the next text; the settings window and the tray menu
turn it off (follow_window=off in voices.conf).
The color button picks the mark color (follow_color in voices.conf).

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
                               QGraphicsPixmapItem, QGraphicsScene, QHBoxLayout, QLabel,
                               QSizeGrip, QTextBrowser, QTextEdit, QVBoxLayout, QWidget)

HIDE_AFTER_MS = 800             # after the end of a text
FADE_MS = 150                   # Spectrum's short exit motion
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
        self.colors = tts_theme.s2_colors()
        self.mark_color = tts_theme.mark_color(self._mark)
        sp, size = tts_theme.SPACE, tts_theme.SIZE

        self.btn_color = _ActionButton("color", self)
        self.btn_color.setCheckable(True)
        self.btn_color.toggled.connect(self._toggle_picker)
        self.btn_pause = _ActionButton("pause", self)
        self.btn_pause.clicked.connect(lambda: self._send({"cmd": "pause"}))
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

        # Swatch picker, shown under the header by the color button
        picker = QHBoxLayout()
        picker.setContentsMargins(0, 0, 0, 0)
        picker.setSpacing(sp["xs"])
        self.swatches = []
        for name in tts_theme.MARKS:
            s = _Swatch(name, self)
            s.setChecked(name == self._mark)
            s.clicked.connect(lambda _=False, n=name: self._pick(n))
            self.swatches.append(s)
            picker.addWidget(s)
        picker.addStretch(1)
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
        self._fade.setDuration(FADE_MS)
        self._fade.setEasingCurve(QEasingCurve.InCubic)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self._faded)
        self._save_timer = QTimer(self, singleShot=True, interval=600,
                                  timeout=self._save_geometry)
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
""")
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
        self.btn_color.setToolTip(tr("follow.color"))
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
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        for blur, pix in self._shadow_pix or ():
            p.drawPixmap(QPointF(panel.x() - blur * 2, panel.y() - blur * 2), pix)
        p.setBrush(QColor(c["elevated"]))
        border = QColor(c["border"])
        p.setPen(QPen(border, 1) if border.alpha() else Qt.NoPen)
        p.drawRoundedRect(panel.adjusted(0.5, 0.5, -0.5, -0.5), r, r)

    # --- mark color ------------------------------------------------------- #
    def _toggle_picker(self, on):
        self.picker.setVisible(on)

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
            # The switch can change in the settings window at any time
            self._job, self._dismissed = job, not enabled()
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
            if self.isVisible() and not self._hide_timer.isActive():
                self._hide_timer.start()
            return
        self._hide_timer.stop()
        if not self.isVisible() and not self._dismissed and self._job is not None:
            self._show()

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
    def _show(self):
        if not self._placed:
            self._place()
            self._placed = True
        self._fade.stop()
        self.setWindowOpacity(1.0)
        self.show()
        self.raise_()

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
        self._fade.start()

    def _faded(self):
        self.btn_color.setChecked(False)
        self.hide()
        self.setWindowOpacity(1.0)

    def _stop(self):
        self._send({"cmd": "stop"})
        self._hide_timer.stop()
        self._fade.start()

    def _dismiss(self):
        self._dismissed = True
        self._hide_timer.stop()
        self._fade.stop()
        self._faded()

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
