"""GPU guard of neural/launcher.py: budget, reserve, CPU mode, one server at
a time. nvidia-smi and the server processes are faked."""
import subprocess
from types import SimpleNamespace

import pytest

import launcher

FAKE_REGISTRY = {
    "small": {"title": "Small", "dir": "small", "port": 41001, "vram_mb": 1000, "cpu_ok": True},
    "mid": {"title": "Mid", "dir": "mid", "port": 41002, "vram_mb": 5000,
            "env": {"MID_FLAG": "1"}},
    "huge": {"title": "Huge", "dir": "huge", "port": 41003, "vram_mb": 9000},
}


@pytest.fixture
def gpu(monkeypatch):
    """Fake NVIDIA GPU. Set gpu.free to an int (MB) or None for a silent nvidia-smi."""
    state = SimpleNamespace(free=12000, used=4000, present=True, calls=[])

    def which(name):
        return "/usr/bin/nvidia-smi" if (name == "nvidia-smi" and state.present) else None

    def run(args, **kw):
        state.calls.append(args)
        if args[0] != "nvidia-smi":
            raise AssertionError(f"unexpected command {args}")
        value = state.free if "memory.free" in args[1] else state.used
        if value is None:
            raise subprocess.TimeoutExpired(args, 10)
        return SimpleNamespace(stdout=f"{value}\n", returncode=0)

    monkeypatch.setattr(launcher, "REGISTRY", dict(FAKE_REGISTRY))
    monkeypatch.setattr(launcher.shutil, "which", which)
    monkeypatch.setattr(launcher.subprocess, "run", run)
    return state


def test_vram_queries(gpu):
    assert launcher.vram_free_mb() == 12000
    assert launcher.vram_used_mb() == 4000
    gpu.free = None
    assert launcher.vram_free_mb() is None


def test_budget_defaults_and_overrides(gpu, write_conf, monkeypatch):
    assert launcher.budget_mb() == launcher.DEFAULT_BUDGET_MB
    assert launcher.reserve_mb() == launcher.DEFAULT_RESERVE_MB
    write_conf("neural_vram_max_mb=8000\nneural_vram_reserve_mb=1000\n")
    assert launcher.budget_mb() == 8000
    assert launcher.reserve_mb() == 1000
    monkeypatch.setenv("TTS_NEURAL_VRAM_MAX_MB", "9500")
    assert launcher.budget_mb() == 9500
    write_conf("neural_vram_max_mb=lots\n")
    monkeypatch.delenv("TTS_NEURAL_VRAM_MAX_MB")
    assert launcher.budget_mb() == launcher.DEFAULT_BUDGET_MB


def test_within_budget(gpu, write_conf):
    assert launcher.within_budget("small") == (True, "")
    assert launcher.within_budget("mid")[0] is True
    ok, why = launcher.within_budget("huge")
    assert not ok and "9000" in why
    write_conf("neural_vram_max_mb=10000\n")
    assert launcher.within_budget("huge")[0] is True


def test_cpu_mode(gpu, monkeypatch):
    gpu.present = False
    assert launcher.has_nvidia() is False
    assert launcher.within_budget("small")[0] is True        # cpu_ok
    assert launcher.within_budget("mid")[0] is False
    launcher.admit("small")                                   # no VRAM check on CPU
    with pytest.raises(RuntimeError):
        launcher.admit("mid")
    assert gpu.calls == []

    gpu.present = True
    monkeypatch.setenv("TTS_NEURAL_CPU", "1")
    assert launcher.has_nvidia() is False


def test_admit_reserve(gpu):
    gpu.free = 9000
    launcher.admit("mid")                       # 9000 - 5000 = 4000 >= 3000
    gpu.free = 7000
    with pytest.raises(RuntimeError, match="Reserve"):
        launcher.admit("mid")                   # 7000 - 5000 = 2000 < 3000
    with pytest.raises(RuntimeError):
        launcher.admit("huge")                  # over budget before any query


def test_admit_unknown_gpu_state(gpu):
    gpu.free = None
    launcher.admit("small")                     # below UNKNOWN_FREE_MAX_MB
    with pytest.raises(RuntimeError, match="nvidia-smi"):
        launcher.admit("mid")


def test_idle_minutes(gpu, write_conf):
    assert launcher.idle_min("mid") == launcher.OFF_ENGINE_IDLE_MAX_MIN
    assert launcher.idle_min("small") == launcher.DEFAULT_IDLE_MIN
    write_conf("engine=mid\nneural_idle_min=45\n")
    assert launcher.idle_min("mid") == 45
    assert launcher.idle_min("huge") == launcher.OFF_ENGINE_IDLE_MAX_MIN
    write_conf("neural_idle_min=0\n")
    assert launcher.idle_min("mid") == 0
    write_conf("neural_idle_min=soon\n")
    assert launcher.idle_min("small") == launcher.DEFAULT_IDLE_MIN


def test_server_env(gpu, write_conf):
    write_conf("engine=mid\nneural_idle_min=20\n")
    env = launcher.server_env("mid")
    assert env["TTS_NEURAL_VRAM_CAP_MB"] == "5000"
    assert env["TTS_NEURAL_IDLE_EXIT_MIN"] == "20.0"
    assert env["MID_FLAG"] == "1"
    assert env["PYTHONUNBUFFERED"] == "1"


def test_port_file(gpu, isolated_dirs):
    assert launcher.port_of("mid") == 41002
    pf = isolated_dirs["TTS_NEURAL_DIR"] / "mid" / "port"
    pf.parent.mkdir(parents=True)
    pf.write_text("50123")
    assert launcher.port_of("mid") == 50123


@pytest.fixture
def servers(gpu, monkeypatch):
    """Fake running servers: healthy() is true for ports in `up`."""
    state = SimpleNamespace(up=set(), stopped=[], spawned=[])
    monkeypatch.setattr(launcher, "healthy", lambda port, timeout=1.0: port in state.up)

    def stop(key):
        state.stopped.append(key)
        state.up.discard(launcher.port_of(key))

    monkeypatch.setattr(launcher, "stop", stop)
    monkeypatch.setattr(launcher.subprocess, "Popen",
                        lambda *a, **k: state.spawned.append(a) or pytest.fail("spawned"))
    return state


def test_start_returns_if_healthy(servers):
    servers.up.add(41002)
    assert launcher.start("mid") is True
    assert servers.stopped == []


def test_start_stops_other_server_first(servers):
    """Only one model on the GPU: a running server is stopped before the
    guard even looks at the new one, also when the guard then refuses."""
    servers.up.add(41001)
    with pytest.raises(RuntimeError):
        launcher.start("huge")
    assert servers.stopped == ["small"]
    assert servers.spawned == []


def test_start_without_venv(servers, gpu):
    with pytest.raises(FileNotFoundError, match="setup"):
        launcher.start("mid")
    assert servers.spawned == []
