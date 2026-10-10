r"""
Shared HTTP server for the local neural TTS models.

Each model runs in its own venv and process (their dependencies conflict), but
all of them speak the same protocol, so the daemon (bin/tts_neural.py) treats
them alike.

A model server subclasses Backend and calls run(MyBackend(), "<key>"), where
<key> is its entry in models.json. The port comes from --port (set by the
launcher) or from models.json. If that port is taken or reserved, the server
binds a free one and writes it to <data_dir>/<key>/port.

Protocol (127.0.0.1 only):
  GET  /health   -> {"ok": true, "model": "...", "loaded": bool}
  GET  /voices   -> {"voices": [{"id", "lang", "desc"}], "default": {lang: id},
                     "languages": [codes the model speaks], "tags": [...],
                     "sample_rate": int, "instruct": bool}
  POST /synth    JSON {"text", "voice", "lang", "speed", "instruct"}
                 -> 200 with header X-Sample-Rate, body = raw s16le mono PCM,
                    streamed until the connection closes. Errors before the
                    first byte -> 500 + JSON {"error": "..."}.
  POST /shutdown -> the process exits and frees its VRAM.

The model loads at startup. Synthesis is serialized: one GPU job at a time.

Standard library only: the launcher, the daemon and the tests import this
module without torch.
"""

import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REGISTRY_FILE = HERE / "models.json"

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def leave_store_python():
    """Re-run the current script with a regular Python if this is the
    Microsoft Store Python.

    Store Python redirects writes to %LOCALAPPDATA% into its package sandbox,
    so the tools, the GPU lock and the daemon would see different folders.
    Call this from command-line entry points only."""
    if os.name != "nt" or ("WindowsApps" not in sys.executable
                           and "PythonSoftwareFoundation" not in sys.prefix):
        return
    import shutil
    import subprocess
    cands = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Python" / d / "python.exe"
             for d in ("Python312", "Python313", "Python311")]
    cands += [Path(p) for p in (shutil.which("py") or "",) if p]
    for c in cands:
        if c.exists() and "WindowsApps" not in str(c):
            args = [str(c)] + (["-3"] if c.name == "py.exe" else []) + sys.argv
            sys.exit(subprocess.call(args))


def data_dir() -> Path:
    """Root for venvs, weights, logs and voices: TTS_NEURAL_DIR, else
    %LOCALAPPDATA%\\tts-neural (Windows) or ~/.local/share/tts-neural."""
    if os.environ.get("TTS_NEURAL_DIR"):
        return Path(os.environ["TTS_NEURAL_DIR"])
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA",
                                   str(Path.home() / "AppData/Local"))) / "tts-neural"
    return Path.home() / ".local/share/tts-neural"


def voices_dir() -> Path:
    """Shared reference voices for cloning: <id>.wav plus <id>.txt (transcript)."""
    return data_dir() / "voices"


def load_registry(path=REGISTRY_FILE) -> dict:
    """models.json without the "_doc" style keys."""
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def parse_port(default=0):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=default)
    return ap.parse_known_args()[0].port


def to_pcm16(samples) -> bytes:
    """Float samples in -1..1 (numpy or torch) -> s16le bytes."""
    import numpy as np
    if hasattr(samples, "detach"):
        samples = samples.detach().float().cpu().numpy()
    a = np.asarray(samples, dtype=np.float32).reshape(-1)
    return (np.clip(a, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def log_fn(name):
    """print() with a "[name]" prefix, flushed, for server logs."""
    def log(*parts):
        print(f"[{name}]", *parts, flush=True)
    return log


class Backend:
    """Base class for a model. Subclasses set name/sample_rate and implement
    load() and synth(). synth() is a generator of float arrays or s16le bytes;
    yielding several small pieces gives an early first sound."""
    name = "base"
    sample_rate = 24000
    tags = []            # supported inline tags, e.g. ["[laugh]", "(whispering)"]
    instruct = False     # accepts natural-language style instructions
    languages = ("de", "en")   # two-letter codes /synth accepts as "lang"

    def load(self):
        raise NotImplementedError

    def voices(self):
        """-> list[{"id", "lang", "desc"}]"""
        return []

    def default_voices(self):
        """-> {lang: id}"""
        return {}

    def synth(self, text, voice, lang, speed=1.0, instruct=""):
        raise NotImplementedError


def apply_limits(name):
    """Protect the rest of the machine before the model loads.

    - below-normal CPU priority (Windows) and a capped torch thread count
    - a hard VRAM cap for the torch allocator (TTS_NEURAL_VRAM_CAP_MB, set by
      the launcher). Beyond it torch raises an out-of-memory error instead of
      letting Windows page VRAM into system RAM, which stalls the desktop."""
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)   # BELOW_NORMAL
        except Exception:
            pass
    try:
        import torch
    except ImportError:
        return
    torch.set_num_threads(int(os.environ.get("TTS_NEURAL_THREADS", "4")))
    cap = int(os.environ.get("TTS_NEURAL_VRAM_CAP_MB", "0") or 0)
    if cap and torch.cuda.is_available():
        total = torch.cuda.get_device_properties(0).total_memory / 2**20
        torch.cuda.set_per_process_memory_fraction(min(1.0, cap / total), 0)
        print(f"[{name}] VRAM cap {cap} MB of {total:.0f} MB", flush=True)


def _vram_note():
    try:
        import torch
        if torch.cuda.is_available():
            return (f", VRAM {torch.cuda.memory_reserved() / 2**20:.0f} MB "
                    f"(peak {torch.cuda.max_memory_reserved() / 2**20:.0f} MB)")
    except Exception:
        pass
    return ""


def _free_cuda_cache():
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def make_server(backend: Backend, port: int = 0, key: str = "", exit_fn=os._exit):
    """HTTP server for an already loaded backend, not yet serving.

    Writes the bound port to <data_dir>/<key>/port. exit_fn ends the process
    on /shutdown and on idle exit; tests pass a harmless replacement.
    The returned server has a `state` dict: {"loaded", "last", "lock"}."""
    backend_key = key or backend.name
    lock = threading.Lock()
    state = {"loaded": True, "last": time.time(), "lock": lock}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *a):
            pass

        def _json(self, code, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self._json(200, {"ok": True, "model": backend.name,
                                 "loaded": state["loaded"]})
            elif self.path == "/voices":
                self._json(200, {"voices": backend.voices(),
                                 "default": backend.default_voices(),
                                 "languages": list(backend.languages),
                                 "tags": backend.tags,
                                 "instruct": backend.instruct,
                                 "sample_rate": backend.sample_rate})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            if self.path == "/shutdown":
                self._json(200, {"ok": True})
                threading.Thread(target=lambda: (time.sleep(0.2), exit_fn(0)),
                                 daemon=True).start()
                return
            if self.path != "/synth":
                self._json(404, {"error": "not found"})
                return
            state["last"] = time.time()
            try:
                req = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                self._json(400, {"error": f"bad json: {exc}"})
                return
            text = (req.get("text") or "").strip()
            if not text:
                self._json(400, {"error": "empty text"})
                return
            with lock:
                self._synth(req, text)

        def _synth(self, req, text):
            ts = time.perf_counter()
            started = False
            total = 0
            ttfa = 0.0
            try:
                gen = backend.synth(text, req.get("voice") or "",
                                    req.get("lang") or "en",
                                    float(req.get("speed") or 1.0),
                                    req.get("instruct") or "")
                for piece in gen:
                    pcm = piece if isinstance(piece, (bytes, bytearray)) else to_pcm16(piece)
                    if not pcm:
                        continue
                    if not started:
                        started = True
                        ttfa = (time.perf_counter() - ts) * 1000
                        self.send_response(200)
                        self.send_header("Content-Type", "application/octet-stream")
                        self.send_header("X-Sample-Rate", str(backend.sample_rate))
                        self.end_headers()
                    total += len(pcm)
                    self.wfile.write(pcm)
                    self.wfile.flush()
                if not started:
                    self._json(500, {"error": "no audio produced"})
                    return
                dur = total / 2 / backend.sample_rate
                el = time.perf_counter() - ts
                print(f"[{backend.name}] {len(text)} chars: first audio {ttfa:.0f} ms, "
                      f"{dur:.1f} s audio in {el:.1f} s (RTF {el / max(dur, 1e-6):.2f})",
                      flush=True)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                print(f"[{backend.name}] client disconnected", flush=True)
            except Exception as exc:
                traceback.print_exc()
                _free_cuda_cache()          # e.g. after hitting the VRAM cap
                if not started:
                    self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        # Windows reserves port ranges dynamically (Hyper-V/WSL, WinError
        # 10013). Take any free port; the launcher reads it from the port file.
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        print(f"[{backend.name}] port {port} unavailable ({exc}) -> "
              f"{srv.server_address[1]}", flush=True)
    srv.daemon_threads = True
    srv.state = state
    pf = data_dir() / backend_key / "port"
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(str(srv.server_address[1]))
    return srv


def start_idle_exit(srv, name, idle_min, exit_fn=os._exit, poll_s=20):
    """Exit after idle_min minutes without a /synth request, even if the
    daemon that started the server is gone (crash, engine switch, tray quit).
    0 disables it."""
    if idle_min <= 0:
        return

    def watch():
        while True:
            time.sleep(poll_s)
            idle = time.time() - srv.state["last"]
            if idle > idle_min * 60 and not srv.state["lock"].locked():
                print(f"[{name}] idle for {idle_min:g} min -> exiting to free VRAM",
                      flush=True)
                exit_fn(0)
                return
    threading.Thread(target=watch, daemon=True).start()


def serve(backend: Backend, port: int = 0, key: str = ""):
    """Load the backend and serve forever. key = models.json key for the port
    file (default backend.name)."""
    apply_limits(backend.name)
    t0 = time.perf_counter()
    print(f"[{backend.name}] loading model ...", flush=True)
    backend.load()
    print(f"[{backend.name}] loaded{_vram_note()}", flush=True)
    srv = make_server(backend, port, key)
    print(f"[{backend.name}] ready after {time.perf_counter() - t0:.1f} s "
          f"on 127.0.0.1:{srv.server_address[1]}", flush=True)
    # Set by the launcher.
    start_idle_exit(srv, backend.name,
                    float(os.environ.get("TTS_NEURAL_IDLE_EXIT_MIN", "0") or 0))
    srv.serve_forever()


def run(backend: Backend, key: str):
    """Entry point of neural/<key>/server.py: port from --port, else models.json."""
    try:
        default = int(load_registry().get(key, {}).get("port", 0))
    except (OSError, ValueError):
        default = 0
    serve(backend, parse_port(default), key)
