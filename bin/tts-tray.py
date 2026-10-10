#!/usr/bin/env python3
"""
tts-tray: tray app for the piper-read daemon (KDE Plasma and Windows).

One icon in the system tray. A left click opens the settings window
(tts_settings, Qt Quick with Material 3), a right click a short menu:
engine, fallback, filters, GPU, sample, stop, language, autostart.
On the first start it opens the setup assistant.

On Windows the tray also registers the global hotkeys and manages the
daemon (Linux: keyd plus KDE shortcuts, daemon as a systemd user service).

Runs with the venv Python (PySide6 and edge_tts installed).
"""

import getpass
import json
import socket
import sys
import threading
import time
from pathlib import Path

import tts_config
import tts_icons
import tts_platform   # OS layer: paths, IPC, hotkeys, autostart, OCR, service
import tts_theme
import tts_elevenlabs as eleven
import tts_i18n
import tts_langs
import tts_settings
import tts_reader      # imports Qt WebEngine: must load before the QApplication
from tts_i18n import tr
from tts_settings import ENGINE_KEYS, SAMPLE, engine_menu_label, send

from PySide6.QtCore import QCoreApplication, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

read_conf = tts_platform.conf_read
write_conf = tts_platform.conf_write


def restart_daemon():
    tts_platform.restart_daemon()


# Channel to the running tray: KDE shortcuts start a script that reports here
# via "tts-tray.py --reader" (on Windows the tray listens itself).
CHANNEL = f"tts-tray-{getpass.getuser()}"


def forward_to_running_tray(command: str) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(CHANNEL)
    if not sock.waitForConnected(1500):
        return False
    sock.write(command.encode("utf-8"))
    sock.waitForBytesWritten(1500)
    sock.disconnectFromServer()
    return True


# --------------------------------------------------------------------------- #
#  Hotkey bridge. On Windows global hotkeys fire in a foreign thread; a Qt
#  signal brings them back to the GUI thread, where reading and OCR may run.
# --------------------------------------------------------------------------- #
class HotkeyBridge(QObject):
    fired = Signal(str)             # action name (read, read_fast, ocr, ...)


# --------------------------------------------------------------------------- #
#  Daemon state for the tray icon (idle, speaking, paused, error)
# --------------------------------------------------------------------------- #
def query_status(timeout=1.0):
    """Daemon state, "" if it answered nothing (older daemon, busy), None if
    it is not reachable."""
    try:
        s = tts_platform.ipc_connect(timeout=timeout)
    except OSError:
        return None
    try:
        s.sendall(b'{"cmd": "status"}')
        s.shutdown(socket.SHUT_WR)
        data = b""
        while len(data) < 4096:
            chunk = s.recv(1024)
            if not chunk:
                break
            data += chunk
        return json.loads(data or b"{}").get("state", "")
    except (OSError, ValueError, AttributeError):
        return ""
    finally:
        s.close()


class StatusWatcher(QObject):
    """Polls the daemon state and the Windows taskbar theme in a worker
    thread (a refused connect can take a second on Windows) and reports
    changes to the GUI thread."""
    changed = Signal(str, bool)     # state, taskbar is light
    INTERVAL = 1.5                  # seconds between polls
    ERROR_AFTER = 3                 # failed polls in a row before "error"

    def __init__(self):
        super().__init__()
        self._wake = threading.Event()
        self._quit = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._quit.set()
        self._wake.set()

    def poke(self):
        """Poll now (after a read or pause command)."""
        self._wake.set()

    def _loop(self):
        last, fails, state = None, 0, "idle"
        while not self._quit.is_set():
            st = query_status()
            if st is None:
                fails += 1
                if fails >= self.ERROR_AFTER:
                    state = "error"
            else:
                fails = 0
                if st in tts_icons.STATES:
                    state = st
                elif state == "error":
                    state = "idle"
            now = (state, tts_icons.taskbar_is_light())
            if now != last:
                last = now
                self.changed.emit(*now)
            self._wake.wait(self.INTERVAL)
            self._wake.clear()


# --------------------------------------------------------------------------- #
#  Tray
# --------------------------------------------------------------------------- #
class Tray:
    def __init__(self, app):
        self.app = app
        self.settings = None
        self.reader = None              # ElevenReader window, created on first use
        self.state = "idle"
        self._light = tts_icons.taskbar_is_light()
        self._icon_key = None
        self.tray = QSystemTrayIcon()
        self._update_icon()
        self.tray.setContextMenu(self._menu())
        self.tray.activated.connect(self._activated)
        self.tray.show()
        tts_i18n.on_change(self._retranslate)

        # Icon follows the daemon state and, on Windows, the taskbar theme.
        self.watcher = StatusWatcher()
        self.watcher.changed.connect(self._on_status)
        self.watcher.start()
        app.aboutToQuit.connect(self.watcher.stop)
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_scheme)

        # Where the tray manages the daemon (Windows), start it here and
        # register the global hotkeys in this process.
        tts_platform.ensure_daemon_running()
        self.bridge = HotkeyBridge()
        self.bridge.fired.connect(self._do_action)
        if tts_platform.INPROC_HOTKEYS:
            handlers = {action: (lambda a=action: self.bridge.fired.emit(a))
                        for action, _label in tts_platform.HOTKEY_ACTIONS}
            tts_platform.apply_hotkeys(handlers)
        if tts_platform.TRAY_MANAGES_DAEMON:
            app.aboutToQuit.connect(tts_platform.stop_daemon)
            # Watchdog: if the daemon dies (crash, out of memory), restart it
            # within seconds, like systemd Restart=always on Linux.
            self._watchdog = QTimer()
            self._watchdog.setInterval(5000)
            self._watchdog.timeout.connect(tts_platform.ensure_daemon_running)
            self._watchdog.start()

        # The ElevenReader sign-in arrives through the clipboard from a
        # bookmarklet. On Wayland Qt reports changes only with focus, so while
        # connecting the tray also polls every 2 s (at most 5 min).
        app.clipboard().dataChanged.connect(self._check_clipboard)
        self._connect_until = 0.0
        self._connect_poll = QTimer(interval=2000, timeout=self._poll_clipboard)

        # Prewarm: load ElevenReader invisibly so the hotkey does not wait for
        # the web app's first load (about 3 s). Only when already connected.
        if tts_reader.load_session():
            QTimer.singleShot(4000, self._reader_window)

        # First start: greet with the setup assistant once the tray is up.
        if tts_settings.needs_onboarding():
            QTimer.singleShot(1200, self.open_settings)

        QLocalServer.removeServer(CHANNEL)      # orphaned channel after a crash
        self.channel = QLocalServer()
        self.channel.newConnection.connect(self._on_channel)
        self.channel.listen(CHANNEL)

    # --- Tray icon and state ------------------------------------------------ #
    def _variant(self):
        if tts_platform.IS_WINDOWS:
            return "light" if self._light else "dark"
        return tts_icons.tray_variant()

    def _update_icon(self):
        key = (self.state, self._variant())
        if key != self._icon_key:
            self._icon_key = key
            self.tray.setIcon(tts_icons.tray_icon(*key))
        tip = tr("tray.tooltip")
        if self.state != "idle":
            tip += "\n" + tr(f"tray.state.{self.state}")
        self.tray.setToolTip(tip)

    def _on_status(self, state, light):
        self.state, self._light = state, light
        self._update_icon()

    def _on_scheme(self, *_):
        self._light = tts_icons.taskbar_is_light()
        self._update_icon()

    def _send(self, obj):
        ok = send(obj)
        self.watcher.poke()          # show speaking/paused without waiting a poll
        return ok

    def _on_channel(self):
        sock = self.channel.nextPendingConnection()

        def read():
            command = bytes(sock.readAll()).decode("utf-8", "replace").strip()
            sock.deleteLater()
            if command in ("reader", "reader-window"):
                self._do_action(command)
            elif command.startswith("text:"):          # text handed over directly
                self._to_reader(command[len("text:"):])
        sock.readyRead.connect(read)

    # --- Hotkey actions (run in the GUI thread) ---------------------------- #
    def _do_action(self, action):
        if action == "pause":
            self._send({"cmd": "pause"})
        elif action == "stop":
            self._send({"cmd": "stop"})
        elif action in ("read", "read_fast"):
            self._speak(fast=action.endswith("fast"))
        elif action in ("ocr", "ocr_fast"):
            self._ocr(fast=action.endswith("fast"))
        elif action == "reader":
            self._to_reader()
        elif action == "reader-window":
            self._reader_window().open_window()

    # --- ElevenReader ----------------------------------------------------- #
    def _reader_window(self):
        if self.reader is None:
            self.reader = tts_reader.ReaderWindow(on_status=self._reader_status,
                                                  on_error=self._reader_error)
        return self.reader

    def _to_reader(self, text=None):
        if text is None:
            text = tts_platform.get_selection()   # Windows: selection via the Ctrl+C trick
        if not text.strip():
            self.tray.showMessage("ElevenReader", tr("tray.msg.readerNothing"),
                                  QSystemTrayIcon.Information)
            return
        self._send({"cmd": "stop"})                      # no local reading on top
        self._reader_window().send(text)

    def _start_connect(self):
        tts_reader.open_connect_page()
        self._connect_until = time.time() + 300
        self._connect_poll.start()

    def _poll_clipboard(self):
        if time.time() > self._connect_until:
            self._connect_poll.stop()
            return
        self._check_clipboard()

    def _clipboard_text(self):
        text = self.app.clipboard().text()
        if not text and tts_platform.IS_LINUX:
            import subprocess
            try:
                text = subprocess.run(["wl-paste", "--no-newline"], capture_output=True,
                                      text=True, timeout=2).stdout
            except (OSError, subprocess.SubprocessError):
                text = ""
        return text

    def _clear_clipboard(self):
        self.app.clipboard().clear()
        if tts_platform.IS_LINUX:
            import subprocess
            try:
                subprocess.run(["wl-copy", "--clear"], timeout=2)
            except (OSError, subprocess.SubprocessError):
                pass

    def _reader_status(self, msg):
        # Only report what needs an action; the window shows the rest.
        if msg == "connect":
            self._start_connect()
            self.tray.showMessage(tr("tray.msg.readerConnectTitle"),
                                  tr("tray.msg.readerConnectText"), QSystemTrayIcon.Information)

    def _check_clipboard(self):
        """Take over the sign-in copied by the bookmarklet and remove it from
        the clipboard right away (it holds the sign-in token)."""
        text = self._clipboard_text()
        diag = tts_reader.parse_diag(text)
        if diag is not None:                  # bookmarklet found no sign-in
            import json
            path = tts_reader.storage_dir() / "diagnose.json"
            path.write_text(json.dumps(diag, indent=2, ensure_ascii=False), encoding="utf-8")
            self._clear_clipboard()
            self.tray.showMessage("ElevenReader", tr("tray.msg.readerDiag", path=path),
                                  QSystemTrayIcon.Warning)
            return
        entry = tts_reader.parse_session(text)
        if entry is None:
            return
        tts_reader.save_session(entry)
        self._clear_clipboard()
        self._connect_poll.stop()
        email = entry.get("value", {}).get("email") or tr("tray.msg.readerAccount")
        self.tray.showMessage(tr("tray.msg.readerConnectedTitle"),
                              tr("tray.msg.readerConnected", email=email),
                              QSystemTrayIcon.Information)
        self._reader_window().session_updated()

    def _reader_error(self, msg, text):
        self.tray.showMessage("ElevenReader", tr("tray.msg.readerFallback", error=msg),
                              QSystemTrayIcon.Warning)
        if text.strip():
            self._send({"text": text, "voice": "auto", "length_scale": self._length_scale(False)})

    def _length_scale(self, fast):
        conf = read_conf()
        key, default = ("speed_fast", 0.8) if fast else ("speed_normal", 1.0)
        try:
            return float(conf.get(key, default))
        except ValueError:
            return default

    def _speak(self, fast=False):
        text = tts_platform.get_selection()       # Windows: selection via the Ctrl+C trick
        if not text.strip():
            self.tray.showMessage(tr("app.name"), tr("tray.msg.nothingSelected"),
                                  QSystemTrayIcon.Information)
            return
        if not self._send({"text": text, "voice": "auto",
                     "length_scale": self._length_scale(fast)}):
            self._daemon_hint()

    def _ocr(self, fast=False):
        if not tts_platform.ocr_supported():
            self.tray.showMessage(tr("tray.msg.ocrTitle"), tr("tray.msg.ocrMissing"),
                                  QSystemTrayIcon.Warning)
            return
        try:
            text = tts_platform.capture_ocr_text()
        except Exception as exc:
            self.tray.showMessage(tr("tray.msg.ocrTitle"), tr("tray.msg.ocrError", error=exc),
                                  QSystemTrayIcon.Warning)
            return
        if text.strip() and not self._send(
                {"text": text, "voice": "auto",
                 "length_scale": self._length_scale(fast)}):
            self._daemon_hint()

    def _daemon_hint(self):
        if tts_platform.TRAY_MANAGES_DAEMON:
            tts_platform.ensure_daemon_running()
        self.tray.showMessage(tr("app.name"), tr("tray.msg.daemonDown"),
                              QSystemTrayIcon.Warning)

    def _menu(self):
        m = QMenu()
        # A removed engine (engine=kokoro) shows as its successor or Piper.
        conf, _ = tts_config.migrate(read_conf(), lambda k: k in tts_settings.NEURAL)

        grp = QActionGroup(m)
        grp.setExclusive(True)
        for key in ENGINE_KEYS:
            a = QAction(engine_menu_label(key), m, checkable=True)
            a.setChecked(conf.get("engine", "piper") == key)
            a.triggered.connect(lambda _=False, k=key: self._set_engine(k))
            grp.addAction(a)
            m.addAction(a)
        m.addSeparator()

        a_det = QAction(tr("tray.autoLang"), m, checkable=True)
        a_det.setChecked(conf.get("lang_detect", "text") == "sentence")
        a_det.triggered.connect(lambda checked: self._set_detect(checked))
        m.addAction(a_det)

        # On: Piper steps in when the engine fails (audible voice change).
        # Off: the voice never changes; failing sentences are retried in a
        # simplified form or skipped.
        a_fb = QAction(tr("tray.fallback"), m, checkable=True)
        a_fb.setChecked(conf.get("fallback", "piper") != "off")
        a_fb.triggered.connect(lambda on: self._set_fallback(on))
        m.addAction(a_fb)

        a_tex = QAction(tr("tray.latex"), m, checkable=True)
        a_tex.setChecked("latex" in [f.strip() for f in
                                     conf.get("filters", "").split(",")])
        a_tex.triggered.connect(lambda checked: self._set_filter("latex", checked))
        m.addAction(a_tex)

        # Compute device for Piper: GPU (CUDA) or CPU. Switching reloads the
        # daemon.
        a_gpu = QAction(tr("tray.gpu"), m, checkable=True)
        a_gpu.setChecked(conf.get("device", "cpu") == "gpu")
        a_gpu.triggered.connect(lambda on: self._set_device(on))
        m.addAction(a_gpu)
        m.addSeparator()

        a_test = QAction(tr("tray.sample"), m)
        # The sample in the app language if it is a reading language, else in
        # the first reading language.
        read_langs = tts_langs.parse_read_langs(conf.get("read_langs"))
        ui_lang = tts_langs.norm(tts_i18n.language())
        sample_lang = ui_lang if ui_lang in read_langs else read_langs[0]
        a_test.triggered.connect(lambda: self._send(
            {"text": SAMPLE[sample_lang], "voice": "auto"}))
        m.addAction(a_test)
        a_stop = QAction(tr("tray.stop"), m)
        a_stop.triggered.connect(lambda: self._send({"cmd": "stop"}))
        m.addAction(a_stop)
        m.addSeparator()

        # ElevenReader uses an unofficial interface: every entry says Beta
        def beta(key):
            a = QAction(tr("tray.beta", label=tr(key)), m)
            a.setToolTip(tr("beta.reader"))
            return a
        m.setToolTipsVisible(True)
        a_rd = beta("tray.readerSend")
        a_rd.triggered.connect(lambda: self._do_action("reader"))
        m.addAction(a_rd)
        a_rw = beta("tray.readerOpen")
        a_rw.triggered.connect(lambda: self._do_action("reader-window"))
        m.addAction(a_rw)
        a_rc = beta("tray.readerConnect")
        a_rc.triggered.connect(self._start_connect)
        m.addAction(a_rc)
        m.addSeparator()

        lang_menu = m.addMenu(tr("tray.language"))
        lgrp = QActionGroup(lang_menu)
        choice = tts_i18n.setting()
        names = dict(tts_i18n.available())
        for code, name in [("auto", tr("lang.auto", lang=names.get(tts_i18n.system_language(), "")))] \
                + tts_i18n.available():
            a = QAction(name, lang_menu, checkable=True)
            a.setChecked(choice == code)
            a.triggered.connect(lambda _=False, c=code: tts_i18n.set_language(c))
            lgrp.addAction(a)
            lang_menu.addAction(a)

        if tts_platform.autostart_supported():
            a_auto = QAction(tr("tray.autostart"), m, checkable=True)
            a_auto.setChecked(tts_platform.autostart_enabled())
            a_auto.triggered.connect(self._set_autostart)
            m.addAction(a_auto)
        m.addSeparator()

        a_setup = QAction(tr("tray.setup"), m)
        a_setup.triggered.connect(self.open_onboarding)
        m.addAction(a_setup)
        a_set = QAction(tr("tray.settings"), m)
        a_set.triggered.connect(self.open_settings)
        m.addAction(a_set)
        a_quit = QAction(tr("tray.quit"), m)
        a_quit.triggered.connect(self.app.quit)
        m.addAction(a_quit)
        return m

    def _retranslate(self):
        self._update_icon()
        self.tray.setContextMenu(self._menu())

    def _activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_settings()

    def open_settings(self, engine=None):
        # Rebuilt on every open, so the window always shows the current
        # voices.conf (also after changes through this menu).
        if self.settings is None or not self.settings.isVisible():
            if self.settings is not None:
                self.settings.close()
            self.settings = tts_settings.SettingsWindow()
        if engine:
            self.settings.select_engine(engine)
        self.settings.show()

    def open_onboarding(self):
        self.open_settings()
        self.settings.open_onboarding()

    def _set_engine(self, key):
        if key == "elevenlabs" and not eleven.load_key():
            # Without a key the daemon would silently stay on Piper, so go
            # straight to the setup instead.
            self.open_settings(engine="elevenlabs")
            self.tray.setContextMenu(self._menu())
            return
        write_conf({"engine": key})
        restart_daemon()
        self.tray.setContextMenu(self._menu())   # refresh the check marks

    def _set_detect(self, per_sentence):
        write_conf({"lang_detect": "sentence" if per_sentence else "text"})
        restart_daemon()
        self.tray.setContextMenu(self._menu())

    def _set_fallback(self, on):
        write_conf({"fallback": "piper" if on else "off"})
        restart_daemon()
        self.tray.setContextMenu(self._menu())

    def _set_autostart(self, on):
        tts_platform.set_autostart(on)
        self.tray.setContextMenu(self._menu())

    def _set_device(self, gpu):
        write_conf({"device": "gpu" if gpu else "cpu"})
        restart_daemon()
        self.tray.setContextMenu(self._menu())

    def _set_filter(self, name, on):
        names = [f.strip() for f in read_conf().get("filters", "").split(",") if f.strip()]
        if on and name not in names:
            names.append(name)
        elif not on and name in names:
            names.remove(name)
        write_conf({"filters": ",".join(names)})
        restart_daemon()
        self.tray.setContextMenu(self._menu())


def main():
    # Single instance: two trays (autostart plus a manual start) would mean two
    # icons and every menu action twice. QLockFile frees stale locks itself.
    import tempfile
    from PySide6.QtCore import QLockFile
    # --reader: take the selection; --reader-text "...": exactly this text
    reader_cmd = "--reader" in sys.argv
    text_arg = (sys.argv[sys.argv.index("--reader-text") + 1]
                if "--reader-text" in sys.argv[:-1] else None)
    lock = QLockFile(str(Path(tempfile.gettempdir()) / "tts-tray.lock"))
    if not lock.tryLock(0):
        if text_arg is not None and forward_to_running_tray("text:" + text_arg):
            return 0
        if reader_cmd and forward_to_running_tray("reader"):
            return 0
        print(tr("tray.msg.alreadyRunning"))
        return 0
    tts_settings.init_style()            # Material for Qt Quick, before the app
    # Qt WebEngine (ElevenReader window) and Qt Quick (settings) share one
    # process, so they must be allowed to share OpenGL contexts.
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    tts_icons.set_app_user_model_id()    # taskbar groups under our icon, not python's
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(tts_icons.app_icon())
    tts_theme.apply(app)                 # tray menu in the same colors
    # Right after sign-in the taskbar is often not ready yet; without waiting
    # the tray would give up for the whole session.
    deadline = time.time() + 120
    while not QSystemTrayIcon.isSystemTrayAvailable() and time.time() < deadline:
        time.sleep(1)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, tr("app.name"), tr("tray.msg.noTray"))
        return 1
    QGuiApplication.setDesktopFileName("tts-tray")
    tray = Tray(app)
    if text_arg is not None:             # started with a command: run it right away
        QTimer.singleShot(500, lambda: tray._to_reader(text_arg))
    elif reader_cmd:
        QTimer.singleShot(500, lambda: tray._do_action("reader"))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
