r"""
Start and stop the local neural TTS servers (used by the daemon and the tools).

Each server runs with the Python of its own venv
(<data_dir>/<key>/venv) and logs to <data_dir>/<key>/server.log.
Standard library only.

GPU guard: the rest of the machine has priority. A model only starts if
  1. its need (vram_mb in models.json, whole process) fits the budget
     (neural_vram_max_mb in voices.conf, default 5500),
  2. at least neural_vram_reserve_mb stay free after loading (default 3000),
  3. no other model server runs (it is stopped first), and
  4. nvidia-smi answers; if it does not, only models below 1.5 GB start.
Without an NVIDIA GPU only models marked cpu_ok start. The server's torch
allocator gets vram_mb as a hard cap, so it fails with out-of-memory instead
of paging VRAM into system RAM.

    python neural/launcher.py status | start <key> | stop <key>
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from server_base import data_dir, leave_store_python, load_registry  # noqa: E402

REGISTRY = load_registry()

DEFAULT_BUDGET_MB = 5500
DEFAULT_RESERVE_MB = 3000
UNKNOWN_FREE_MAX_MB = 1500      # nvidia-smi silent -> only small models
DEFAULT_IDLE_MIN = 30.0
OFF_ENGINE_IDLE_MAX_MIN = 10.0  # idle limit for large models that are not the active engine
LARGE_MODEL_MB = 2000


def venv_python(key) -> Path:
    v = data_dir() / key / "venv"
    return v / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def server_script(key) -> Path:
    return HERE / REGISTRY[key]["dir"] / "server.py"


def setup_hint(key) -> str:
    """Setup script to run for a missing venv, e.g. neural/qwen3/setup.ps1."""
    d = HERE / REGISTRY[key]["dir"]
    name = "setup.ps1" if os.name == "nt" else "setup.sh"
    if not (d / name).exists():
        name = next((n for n in ("setup.ps1", "setup.sh") if (d / n).exists()), name)
    return f"neural/{REGISTRY[key]['dir']}/{name}"


def installed(key) -> bool:
    return key in REGISTRY and venv_python(key).exists() and server_script(key).exists()


def healthy(port, timeout=1.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health",
                                    timeout=timeout) as r:
            return json.loads(r.read()).get("loaded", False)
    except Exception:
        return False


def port_of(key) -> int:
    """Actual port (the server's port file), else the one from models.json."""
    try:
        return int((data_dir() / key / "port").read_text().strip())
    except Exception:
        return REGISTRY[key]["port"]


def log_path(key) -> Path:
    return data_dir() / key / "server.log"


def cache_voices(key):
    """Save /voices of the running server to <key>/voices.json, so the settings
    can list voices while the server is down."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port_of(key)}/voices",
                                    timeout=10) as r:
            meta = json.loads(r.read())
        (data_dir() / key / "voices.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def conf_path() -> Path:
    """The daemon's voices.conf (same lookup as bin/tts_platform.piper_dir)."""
    if os.environ.get("PIPER_DIR"):
        return Path(os.environ["PIPER_DIR"]) / "voices.conf"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming"))) \
            / "piper" / "voices.conf"
    return Path.home() / ".local/share/piper/voices.conf"


def _conf():
    out = {}
    try:
        for line in conf_path().read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def _int(val, default):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def budget_mb():
    return _int(os.environ.get("TTS_NEURAL_VRAM_MAX_MB")
                or _conf().get("neural_vram_max_mb"), DEFAULT_BUDGET_MB)


def reserve_mb():
    return _int(_conf().get("neural_vram_reserve_mb"), DEFAULT_RESERVE_MB)


def need_mb(key):
    return _int(REGISTRY[key].get("vram_mb"), 0)


def idle_min(key):
    """Minutes without a request after which the server exits: neural_idle_min
    (default 30) for the engine active in voices.conf; other models above
    2 GB (e.g. after a voice preview) at most 10. 0 = never."""
    conf = _conf()
    try:
        m = float(conf.get("neural_idle_min", DEFAULT_IDLE_MIN))
    except ValueError:
        m = DEFAULT_IDLE_MIN
    if m <= 0:
        return 0.0
    if conf.get("engine") == key:
        return m
    return min(m, OFF_ENGINE_IDLE_MAX_MIN) if need_mb(key) > LARGE_MODEL_MB else m


def within_budget(key):
    """(ok, reason): budget and hardware, without the current GPU state."""
    if not has_nvidia() and not REGISTRY[key].get("cpu_ok"):
        return False, f"{key} braucht eine NVIDIA-Grafikkarte"
    need, budget = need_mb(key), budget_mb()
    if need > budget:
        return False, (f"{key} braucht ~{need} MB VRAM, Budget ist {budget} MB "
                       "(neural_vram_max_mb in voices.conf)")
    return True, ""


def running():
    return [k for k in REGISTRY if healthy(port_of(k), 0.3)]


def has_nvidia():
    """NVIDIA GPU present? TTS_NEURAL_CPU=1 forces the CPU case."""
    if os.environ.get("TTS_NEURAL_CPU") == "1":
        return False
    return shutil.which("nvidia-smi") is not None


def admit(key):
    """Raise RuntimeError if the GPU guard refuses to start `key` now."""
    ok, why = within_budget(key)
    if not ok:
        raise RuntimeError(why)
    if not has_nvidia():
        # CPU only: VRAM does not matter, within_budget() checked cpu_ok.
        return
    need = need_mb(key)
    free = vram_free_mb()
    if free is None:
        if need > UNKNOWN_FREE_MAX_MB:
            raise RuntimeError(f"{key}: GPU-Zustand unbekannt (nvidia-smi antwortet "
                               "nicht), großes Modell wird nicht gestartet")
        return
    if free - need < reserve_mb():
        raise RuntimeError(f"{key}: nur {free} MB VRAM frei, nach dem Laden blieben "
                           f"{free - need} MB, Reserve ist {reserve_mb()} MB")


def server_env(key):
    """Environment for a server process: caps from the guard plus models.json env."""
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8",
               TTS_NEURAL_VRAM_CAP_MB=str(need_mb(key) or ""),
               TTS_NEURAL_IDLE_EXIT_MIN=str(idle_min(key)))
    env.update({k: str(v) for k, v in (REGISTRY[key].get("env") or {}).items()})
    return env


def start(key, wait=True, timeout=240):
    """Start the server unless it is already healthy and wait for /health
    to report "loaded". Raises RuntimeError if the GPU guard refuses, and
    kills a server that does not get ready in time."""
    spec = REGISTRY[key]
    if healthy(port_of(key)):
        return True
    for other in running():                   # one model on the GPU at a time
        if other != key:
            stop(other)
    admit(key)
    pf = data_dir() / key / "port"
    if pf.exists():
        pf.unlink()
    py = venv_python(key)
    if not py.exists():
        raise FileNotFoundError(f"{key}: venv missing ({py}), run {setup_hint(key)}")
    script = server_script(key)
    log = log_path(key)
    log.parent.mkdir(parents=True, exist_ok=True)
    flags = 0
    if os.name == "nt":
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                 | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
    with open(log, "ab") as lf:
        proc = subprocess.Popen([str(py), str(script), "--port", str(spec["port"])],
                                cwd=str(script.parent), stdout=lf,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                creationflags=flags, env=server_env(key))
    if not wait:
        return False
    t0 = time.time()
    while time.time() - t0 < timeout:
        if healthy(port_of(key)):
            cache_voices(key)
            return True
        if proc.poll() is not None:
            raise RuntimeError(f"{key}: server exited while loading "
                               f"(code {proc.returncode}, log: {log})")
        time.sleep(0.5)
    proc.kill()
    raise TimeoutError(f"{key}: server not ready after {timeout} s, killed (log: {log})")


def stop(key):
    port = port_of(key)
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{port}/shutdown", data=b"{}", method="POST"), timeout=3)
    except Exception:
        pass
    for _ in range(40):
        if not healthy(port, 0.3):
            return
        time.sleep(0.25)


def _smi(field):
    """One nvidia-smi field in MB, or None. stdin/stderr are set explicitly:
    under pythonw (daemon, tray) there are no valid standard handles, and an
    inherited invalid handle makes the child fail."""
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--query-gpu={field}", "--format=csv,noheader,nounits"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0).stdout
        return int(out.strip().splitlines()[0])
    except Exception:
        return None


def vram_free_mb():
    return _smi("memory.free")


def vram_used_mb():
    return _smi("memory.used")


def main(argv):
    cmd, key = argv[0] if argv else "status", argv[1] if len(argv) > 1 else ""
    if cmd == "start":
        start(key)
        print("ok")
    elif cmd == "stop":
        stop(key)
        print("ok")
    else:
        print(f"VRAM budget {budget_mb()} MB, reserve {reserve_mb()} MB, "
              f"free {vram_free_mb()} MB")
        for k in REGISTRY:
            print(f"{k:<11} installed={installed(k)!s:<5} running={healthy(port_of(k))!s:<5} "
                  f"needs ~{need_mb(k)} MB  allowed={within_budget(k)[0]}")


if __name__ == "__main__":
    leave_store_python()
    main(sys.argv[1:])
