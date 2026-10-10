"""
tts_neural: connects the local neural TTS servers (neural/<key>/) to the
daemon and the settings window.

The servers run as separate processes in their own venvs (protocol in
neural/server_base.py, registry in neural/models.json). This module starts a
server on demand, streams PCM per sentence and stops idle servers so their
VRAM is freed again.

voices.conf:
  engine=<key>             a key from models.json, e.g. qwen3 or kokoro2
  <key>_<lang>=<voice>     voice per reading language (empty = model default)
  <key>_instruct=<text>    optional style instruction or parameters
  neural_idle_min=30       stop a server after this many idle minutes (0 = never)
"""

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import tts_langs

START_BACKOFF_S = 60          # no new start attempt this long after a failed one
REAPER_POLL_S = 30


def _neural_dir():
    here = Path(__file__).resolve().parent
    for cand in (here / "neural", here.parent / "neural"):
        if (cand / "models.json").exists():
            return cand
    return None


NEURAL_DIR = _neural_dir()
launcher = None
if NEURAL_DIR is not None:
    sys.path.insert(0, str(NEURAL_DIR))
    try:
        import launcher  # noqa: F811
    except Exception as exc:          # a broken neural/ folder must never take the daemon down
        print(f"[tts-neural] launcher not loadable: {exc}", flush=True)
        launcher = None

REGISTRY = launcher.REGISTRY if launcher else {}
ENGINES = set(REGISTRY)
CAPABILITIES = ("clone", "cpu_ok", "instruct", "tags", "markup")

_last_use = {}
_start_lock = threading.Lock()
_broken_until = {}


def installed(key):
    return bool(launcher) and launcher.installed(key)


def installed_engines():
    return [k for k in REGISTRY if installed(k)]


def usable(key):
    """Installed and within the VRAM budget (neural_vram_max_mb). The UI does
    not offer anything above the budget."""
    return installed(key) and launcher.within_budget(key)[0]


def usable_engines():
    return [k for k in REGISTRY if usable(k)]


def title(key):
    return REGISTRY.get(key, {}).get("title", key)


def capabilities(key):
    """{"clone", "cpu_ok", "instruct", "tags", "markup"} -> bool, from models.json."""
    spec = REGISTRY.get(key, {})
    return {c: bool(spec.get(c)) for c in CAPABILITIES}


def native_speed(key):
    """Does the model render the requested speed itself (models.json
    native_speed)? Otherwise the daemon time-stretches its output."""
    return bool(REGISTRY.get(key, {}).get("native_speed"))


def clones(key):
    """Does this model clone reference recordings (voices/<id>.wav as "ref:<id>")?"""
    return capabilities(key)["clone"]


def voices_cache(key):
    """Last /voices response, saved by the launcher when the server started."""
    if not launcher:
        return {}
    try:
        return json.loads((launcher.data_dir() / key / "voices.json")
                          .read_text(encoding="utf-8"))
    except Exception:
        return {}


def langs(key):
    """Languages a model speaks: what its server reported in /voices
    ("languages"), else the table in tts_langs."""
    return tts_langs.engine_langs(key, reported=voices_cache(key).get("languages"))


def merge_voice_lists(meta, refs, langs=tts_langs.DEFAULT_READ):
    """{lang: [...]} for each of `langs` from a /voices response and the
    reference voices (tts_voices.list_voices()).

    Per language: the model's own voices for that language (or "multi"), then
    references in that language, then the other references (cloning works
    across languages). A language without own voices offers all own voices
    (they speak every language of the model). The model default comes first,
    duplicates are dropped."""
    out = {}
    own = [v for v in meta.get("voices", []) if not v["id"].startswith("ref:")]
    for lang in langs:
        ids = [v["id"] for v in own if v.get("lang") in (lang, "multi", "", None)]
        if not any(v.get("lang") == lang for v in own):
            ids += [v["id"] for v in own]
        ids += [f"ref:{v['id']}" for v in refs if v["lang"] == lang]
        ids += [f"ref:{v['id']}" for v in refs if v["lang"] != lang]
        d = (meta.get("default") or {}).get(lang)
        if d:
            ids = [d] + [i for i in ids if i != d]
        out[lang] = list(dict.fromkeys(ids))
    return out


def voice_lists(key, for_langs=None):
    """Voice lists for the settings window, for every language the model
    speaks (or `for_langs`). Cloning models also get every file in the voices
    folder, read fresh, so new voices show up without a server restart."""
    refs = []
    if clones(key):
        import tts_voices
        refs = tts_voices.list_voices()
    return merge_voice_lists(voices_cache(key), refs, for_langs or langs(key))


def ensure(key, timeout=300):
    """Start the server unless it runs. After a failed start, wait
    START_BACKOFF_S before trying again, so not every sentence retries."""
    if _broken_until.get(key, 0) > time.time():
        raise RuntimeError(f"{key}: server start failed recently")
    port = launcher.port_of(key)
    if launcher.healthy(port):
        return port
    with _start_lock:
        if launcher.healthy(launcher.port_of(key)):
            return launcher.port_of(key)
        t0 = time.time()
        print(f"[tts-neural] starting {key} server ...", flush=True)
        try:
            launcher.start(key, wait=True, timeout=timeout)
        except Exception as exc:
            _broken_until[key] = time.time() + START_BACKOFF_S
            print(f"[tts-neural] {key}: start failed ({exc})", flush=True)
            raise
        print(f"[tts-neural] {key} server ready after {time.time() - t0:.1f} s",
              flush=True)
        return launcher.port_of(key)


def prewarm(key):
    """Start the server in the background (daemon startup); errors are only logged."""
    def run():
        try:
            ensure(key)
            _last_use[key] = time.time()
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


def stream(key, text, voice="", lang="de", speed=1.0, instruct=""):
    """Generator of (pcm_bytes, sample_rate). Errors before the first byte are
    raised, so the daemon can fall back. Closing the generator (stop) drops
    the connection and the server aborts the request."""
    port = ensure(key)
    _last_use[key] = time.time()
    body = json.dumps({"text": text, "voice": voice, "lang": lang,
                       "speed": speed, "instruct": instruct}).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/synth", data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        r = urllib.request.urlopen(req, timeout=120)
    except urllib.error.HTTPError as exc:
        try:
            msg = json.loads(exc.read()).get("error", "")
        except Exception:
            msg = str(exc)
        raise RuntimeError(f"{key}: {msg}") from None
    try:
        sr = int(r.headers.get("X-Sample-Rate") or 24000)
        carry = b""
        while True:
            buf = r.read(8192)
            if not buf:
                break
            buf = carry + buf
            cut = len(buf) & ~1               # whole int16 samples only
            carry = buf[cut:]
            if cut:
                yield buf[:cut], sr
            _last_use[key] = time.time()
    finally:
        r.close()
        _last_use[key] = time.time()


def start_idle_reaper(keys, idle_min):
    """Stop servers that were not used for their idle limit (frees VRAM)."""
    if not launcher or idle_min <= 0:
        return

    def loop():
        while True:
            time.sleep(REAPER_POLL_S)
            now = time.time()
            for k in keys:
                if not launcher.healthy(launcher.port_of(k), 0.5):
                    _last_use.pop(k, None)
                    continue
                # Servers started by an earlier daemon get adopted here; their
                # idle time starts now.
                last = _last_use.setdefault(k, now)
                limit = launcher.idle_min(k) or idle_min
                if now - last > limit * 60:
                    print(f"[tts-neural] {k}: unused for {limit:g} min -> server stopped",
                          flush=True)
                    launcher.stop(k)
                    _last_use.pop(k, None)
    threading.Thread(target=loop, daemon=True).start()


def stop_others(keep=None):
    """Stop every model server except `keep` (engine switch: never two models
    in VRAM, including servers left over from an earlier daemon)."""
    if not launcher:
        return
    for k in REGISTRY:
        if k != keep and launcher.healthy(launcher.port_of(k), 0.3):
            print(f"[tts-neural] {k}: no longer the active engine -> server stopped",
                  flush=True)
            launcher.stop(k)


def stop_all():
    for k in installed_engines():
        if launcher.healthy(launcher.port_of(k), 0.3):
            launcher.stop(k)
