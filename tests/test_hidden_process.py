"""tts_platform.run_hidden/popen_hidden: child processes of the tray, the
settings window and the daemon get no console window on Windows."""
import subprocess

import pytest

import tts_platform


@pytest.fixture
def captured(monkeypatch):
    """Replace subprocess.Popen/run; returns the kwargs of the last call."""
    seen = {}

    def fake(args, **kw):
        seen.clear()
        seen.update(kw, args=args)
        return "proc"

    monkeypatch.setattr(subprocess, "Popen", fake)
    monkeypatch.setattr(subprocess, "run", fake)
    return seen


@pytest.mark.parametrize("call", ["popen_hidden", "run_hidden"])
def test_windows_sets_no_window_flags(monkeypatch, captured, call):
    monkeypatch.setattr(tts_platform, "IS_WINDOWS", True)
    assert getattr(tts_platform, call)(["ffmpeg", "-version"], stdout=subprocess.PIPE) == "proc"
    assert captured["args"] == ["ffmpeg", "-version"]
    assert captured["creationflags"] & tts_platform.CREATE_NO_WINDOW
    assert captured["stdin"] == subprocess.DEVNULL          # pythonw has no valid stdin
    assert captured["stdout"] == subprocess.PIPE
    if hasattr(subprocess, "STARTUPINFO"):
        si = captured["startupinfo"]
        assert si.dwFlags & subprocess.STARTF_USESHOWWINDOW and si.wShowWindow == 0


def test_windows_keeps_existing_flags_and_input(monkeypatch, captured):
    monkeypatch.setattr(tts_platform, "IS_WINDOWS", True)
    tts_platform.run_hidden(["x"], creationflags=0x4000, input=b"data")
    assert captured["creationflags"] == 0x4000 | tts_platform.CREATE_NO_WINDOW
    assert "stdin" not in captured                          # run(input=...) sets its own
    tts_platform.popen_hidden(["x"], stdin=subprocess.PIPE)
    assert captured["stdin"] == subprocess.PIPE


def test_other_platforms_pass_through(monkeypatch, captured):
    monkeypatch.setattr(tts_platform, "IS_WINDOWS", False)
    tts_platform.popen_hidden(["aplay"], stdout=subprocess.PIPE)
    assert captured == {"args": ["aplay"], "stdout": subprocess.PIPE}


def test_daemon_start_is_hidden(monkeypatch, captured):
    """The tray starts the daemon through the same helper."""
    monkeypatch.setattr(tts_platform, "IS_WINDOWS", True)
    monkeypatch.setattr(tts_platform, "TRAY_MANAGES_DAEMON", True)
    monkeypatch.setattr(tts_platform, "daemon_running", lambda timeout=0.4: False)
    monkeypatch.setattr(tts_platform, "_daemon_proc", None)
    tts_platform.ensure_daemon_running()
    assert captured["args"][1].endswith("piper-read-daemon.py")
    assert captured["creationflags"] & tts_platform.CREATE_NO_WINDOW
