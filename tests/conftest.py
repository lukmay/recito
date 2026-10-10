"""Shared fixtures. Every test runs with its own temporary data and config
folders, so the user's real configuration is never read or written."""
import pytest


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Point every path the app reads (voices.conf, neural data, caches) into
    tmp_path and force the CPU case for the launcher unless a test says
    otherwise."""
    dirs = {
        "PIPER_DIR": tmp_path / "piper",
        "TTS_NEURAL_DIR": tmp_path / "tts-neural",
        "APPDATA": tmp_path / "appdata",
        "LOCALAPPDATA": tmp_path / "localappdata",
        "XDG_CONFIG_HOME": tmp_path / "config",
        "XDG_CACHE_HOME": tmp_path / "cache",
        "XDG_RUNTIME_DIR": tmp_path / "run",
    }
    for name, path in dirs.items():
        path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(name, str(path))
    for name in ("TTS_NEURAL_VRAM_MAX_MB", "TTS_NEURAL_CPU", "ELEVENLABS_API_KEY",
                 "PIPER_PORT", "PIPER_SOCKET"):
        monkeypatch.delenv(name, raising=False)
    return dirs


@pytest.fixture
def write_conf(isolated_dirs):
    """Write voices.conf into the temporary PIPER_DIR."""
    def write(text):
        p = isolated_dirs["PIPER_DIR"] / "voices.conf"
        p.write_text(text, encoding="utf-8")
        return p
    return write
