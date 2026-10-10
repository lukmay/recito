"""The Windows setup puts ffmpeg.exe next to the program; tts_platform adds it to PATH as a fallback."""
import os

import tts_platform


def _run(monkeypatch, tmp_path, *, windows, system_ffmpeg, bundled=True):
    app = tmp_path / "app"
    app.mkdir(parents=True)
    if bundled:
        (app / "ffmpeg.exe").write_bytes(b"")
    monkeypatch.setattr(tts_platform, "__file__", str(app / "tts_platform.py"))
    monkeypatch.setattr(tts_platform, "IS_WINDOWS", windows)
    found = "C:/sys/ffmpeg.exe" if system_ffmpeg else None
    monkeypatch.setattr(tts_platform.shutil, "which", lambda name: found)
    monkeypatch.setenv("PATH", "C:/base")
    tts_platform._use_bundled_ffmpeg()
    return os.environ["PATH"], str(app)


def test_bundled_ffmpeg_is_the_fallback(monkeypatch, tmp_path):
    path, app = _run(monkeypatch, tmp_path, windows=True, system_ffmpeg=False)
    assert path == "C:/base" + os.pathsep + app


def test_system_ffmpeg_wins(monkeypatch, tmp_path):
    path, _ = _run(monkeypatch, tmp_path, windows=True, system_ffmpeg=True)
    assert path == "C:/base"


def test_nothing_changes_without_the_file_or_off_windows(monkeypatch, tmp_path):
    assert _run(monkeypatch, tmp_path, windows=True, system_ffmpeg=False, bundled=False)[0] == "C:/base"
    assert _run(monkeypatch, tmp_path / "b", windows=False, system_ffmpeg=False)[0] == "C:/base"
