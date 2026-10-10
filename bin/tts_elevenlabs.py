"""
tts_elevenlabs: ElevenLabs client for daemon and tray (stdlib only).

Synthesis uses the streaming endpoint with output_format=pcm_24000: raw
16-bit PCM, mono, 24 kHz. It goes straight into the same audio sink as
Piper/Edge without ffmpeg, and playback starts with the first packet.

Cost: ElevenLabs bills per character. Every fully played sentence goes into
the audio cache (key = text + voice + model + speed), so hearing a passage
again costs nothing and has no network latency.

API key: file <PIPER_DIR>/elevenlabs.key, kept out of voices.conf so it is
never shared along with the config. ELEVENLABS_API_KEY in the environment
takes precedence.
"""

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import tts_platform
from tts_i18n import tr

API = os.environ.get("ELEVENLABS_API_BASE", "https://api.elevenlabs.io")  # env override for tests
KEY_URL = "https://elevenlabs.io/app/settings/api-keys"
PRICING_URL = "https://elevenlabs.io/pricing/api"
SAMPLE_RATE = 24000
TIMEOUT = 15

# Models offered in the tray (as of October 2026). credits = credits per
# character from the pricing page; the real factor comes from /v1/models at
# runtime and overrides it. usd_per_m = pay-as-you-go list price per 1M
# characters. context = accepts previous_text/next_text (consistent intonation
# across sentence boundaries); lang_code = accepts language_code (400 error
# otherwise); speed = supports voice_settings.speed (v4/v3 have no speed control).
MODELS = [
    {
        "id": "eleven_flash_v2_5",
        "name": "Flash v2.5",
        "blurb": "Halber Preis, erster Ton nach ~75 ms. Klingt natürlich, etwas "
                 "gleichförmiger als v4. Bestes Preis-Leistungs-Verhältnis.",
        "credits": 0.5, "usd_per_m": 40, "price": "€", "speed_label": "sehr schnell",
        "context": True, "lang_code": True, "speed": True,
    },
    {
        "id": "eleven_v4",
        "name": "Eleven v4",
        "blurb": "Neuestes Modell (seit 09/2026), klingt am lebendigsten, wie in "
                 "ElevenReader. Doppelter Preis, kein Tempo-Regler.",
        "credits": 1.0, "usd_per_m": 80, "price": "€€", "speed_label": "mittel",
        "context": True, "lang_code": False, "speed": False,
    },
    {
        "id": "eleven_multilingual_v2",
        "name": "Multilingual v2",
        "blurb": "Ruhige, sehr stabile Erzählstimme für lange Texte. Doppelter Preis, "
                 "Tempo einstellbar.",
        "credits": 1.0, "usd_per_m": 80, "price": "€€", "speed_label": "mittel",
        "context": True, "lang_code": False, "speed": True,
    },
    {
        "id": "eleven_v3",
        "name": "Eleven v3",
        "blurb": "Vorgänger von v4, sehr ausdrucksstark, aber bei kurzen Sätzen "
                 "unruhig. Kein Grund mehr, ihn statt v4 zu nehmen.",
        "credits": 1.0, "usd_per_m": 80, "price": "€€", "speed_label": "langsam",
        "context": False, "lang_code": True, "speed": False,
    },
]
DEFAULT_MODEL = "eleven_flash_v2_5"

# Voices preferred on first setup (narrator voices among the default voices;
# old defaults such as George/Rachel are retired at the end of 2026). IDs
# always come from the account (/v2/voices), never hardcoded.
PREFERRED_VOICES = ["Elara", "Eldrin", "Finley", "Sawyer", "Darian", "Florence",
                    "Alicia", "George", "Matilda", "Daniel", "Brian"]

# Old default voices that ElevenLabs retires on 2026-12-31 (only accounts
# created before March 2026 have them). Marked in the picker and ranked last
# by the automatic choice.
RETIRING_VOICES = {"roger", "sarah", "laura", "charlie", "george", "callum", "river",
                   "liam", "alice", "matilda", "will", "jessica", "eric", "chris",
                   "brian", "daniel", "lily", "bill"}

# Permissions a restricted key ("Restrict Key") needs, named as on the
# ElevenLabs key page.
PERMISSIONS = [
    ("tts", "Text to Speech", "Access", True, "zum Vorlesen"),
    ("voices", "Voices", "Read", True, "für die Stimmenauswahl"),
    ("models", "Models", "Access", False, "zeigt die echten Preise je Modell"),
    ("user", "User", "Access", False, "zeigt den Kontostand"),
]

# Spoken characters per minute (~150 words of ~6 characters).
CHARS_PER_MINUTE = 900
CACHE_MAX_BYTES = 300 * 1024 * 1024        # ~1.8 h of audio at 24 kHz/16 bit


class ElevenLabsError(RuntimeError):
    """Error with a user-readable message (status = HTTP code or None)."""

    def __init__(self, message, status=None, code=""):
        super().__init__(message)
        self.status = status
        self.code = code


def model_info(model_id):
    for m in MODELS:
        if m["id"] == model_id:
            return m
    return {"id": model_id, "name": model_id, "credits": 1.0, "usd_per_m": 80,
            "context": False, "lang_code": False, "speed": False,
            "price": "?", "speed_label": "?", "blurb": ""}


# --------------------------------------------------------------------------- #
#  API key
# --------------------------------------------------------------------------- #
def key_path() -> Path:
    return tts_platform.piper_dir() / "elevenlabs.key"


def load_key() -> str:
    env = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if env:
        return env
    try:
        return key_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save_key(key: str):
    p = key_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(key.strip() + "\n", encoding="utf-8")
    if not tts_platform.IS_WINDOWS:
        os.chmod(p, 0o600)


def looks_like_key(text: str) -> bool:
    """Clipboard heuristic: ElevenLabs keys start with 'sk_' (older keys are
    32 hex characters)."""
    t = (text or "").strip()
    if t.startswith("sk_") and 30 <= len(t) <= 80 and " " not in t:
        return True
    return len(t) == 32 and all(ch in "0123456789abcdef" for ch in t.lower())


# --------------------------------------------------------------------------- #
#  HTTP
# --------------------------------------------------------------------------- #
def _http_error(status, body: bytes) -> ElevenLabsError:
    """HTTP error -> ElevenLabsError with a user-facing message and the API code."""
    code, detail = "", ""
    try:
        d = json.loads(body.decode("utf-8", "replace")).get("detail")
        if isinstance(d, dict):
            code = d.get("status") or d.get("code") or ""
            detail = d.get("message", "") or code
        elif d:
            detail = str(d)
    except (ValueError, AttributeError):
        pass
    if code in ("quota_exceeded", "insufficient_credits"):
        msg = tr("el.err.quota")
    elif code == "invalid_api_key":
        msg = tr("el.err.invalidKey")
    elif code == "missing_permissions":
        msg = tr("el.err.missingPerm", detail=detail)
    elif status == 401:
        msg = tr("el.err.unauthorized") + (f" ({detail})" if detail else "")
    elif status == 429:
        msg = tr("el.err.rateLimit")
    else:
        msg = tr("el.err.http", status=status, detail=detail or tr("el.err.generic"))
    return ElevenLabsError(msg, status, code)


def _request(method, path, key, body=None, params=None, timeout=TIMEOUT):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("xi-api-key", key)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise _http_error(exc.code, exc.read()) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ElevenLabsError(tr("el.err.noConnection", error=exc)) from None


def _get_json(path, key, params=None):
    with _request("GET", path, key, params=params) as r:
        return json.loads(r.read().decode("utf-8"))


def subscription(key: str) -> dict:
    """Account usage: {'tier', 'used', 'limit', 'reset'}, or ElevenLabsError.
    Needs the 'User: Read' permission; a key without it is still valid
    (see check_account)."""
    s = _get_json("/v1/user/subscription", key)
    return {"tier": s.get("tier", "?"),
            "used": int(s.get("character_count", 0)),
            "limit": int(s.get("character_limit", 0)),
            "reset": s.get("next_character_count_reset_unix")}


def _allowed(fn):
    """Run fn() -> (True, result), or (False, None) if a permission is missing.
    Invalid keys and network errors propagate."""
    try:
        return True, fn()
    except ElevenLabsError as exc:
        if exc.code == "missing_permissions":
            return False, None
        raise


def _probe_tts(key, voice_id):
    """Whether the key may synthesize speech. A request with empty text checks
    this without using any characters: without the permission ElevenLabs
    answers missing_permissions, otherwise success or an input error."""
    try:
        with _request("POST", f"/v1/text-to-speech/{voice_id or 'probe'}", key,
                      {"text": "", "model_id": DEFAULT_MODEL},
                      params={"output_format": f"pcm_{SAMPLE_RATE}"}):
            return True
    except ElevenLabsError as exc:
        if exc.code == "missing_permissions":
            return False
        if exc.code == "invalid_api_key" or exc.status is None:
            raise
        return True                              # 400/404/422: permission present


def check_account(key: str) -> dict:
    """Check the key and load everything the tray needs.
    -> {'perms': {tts, voices, models, user}, 'sub', 'voices', 'rates'}.
    Raises ElevenLabsError for an invalid key or no network."""
    ok_voices, voices = _allowed(lambda: list_voices(key))
    ok_models, rates = _allowed(lambda: list_models(key))
    ok_user, sub = _allowed(lambda: subscription(key))
    ok_tts = _probe_tts(key, voices[0]["id"] if voices else "")
    return {"perms": {"tts": ok_tts, "voices": ok_voices, "models": ok_models,
                      "user": ok_user},
            "sub": sub, "voices": voices or [], "rates": rates or {}}


def list_voices(key: str):
    """Voices of the account -> [{'id', 'name', 'desc', 'retiring'}]. Premade
    and user-added voices; library voices appear only after 'Add to My Voices'
    on the website. Names like 'Alice - Clear, Engaging Educator' are split
    into name and description."""
    out, token = [], None
    for _ in range(5):                                    # at most 500 voices
        params = {"page_size": 100}
        if token:
            params["next_page_token"] = token
        d = _get_json("/v2/voices", key, params)
        for v in d.get("voices", []):
            labels = v.get("labels") or {}
            name, _, tagline = v.get("name", "?").partition(" - ")
            facts = ", ".join(x for x in (labels.get("gender"), labels.get("accent"))
                              if x)
            desc = " · ".join(x for x in (tagline.strip() or labels.get("description")
                                          or labels.get("descriptive"), facts) if x)
            out.append({"id": v["voice_id"], "name": name.strip(), "desc": desc,
                        "use": labels.get("use_case") or labels.get("use case") or "",
                        "premade": v.get("category") == "premade",
                        "retiring": name.strip().lower() in RETIRING_VOICES
                        and v.get("category") == "premade"})
        token = d.get("next_page_token")
        if not d.get("has_more") or not token:
            break
    return out


def pick_default_voice(voices):
    """Pick a good reading voice from list_voices() -> voice_id or ''.
    Order: known narrator voices that are not retiring; premade narrative or
    educational voices that are not retiring; retiring known narrators (usable
    until the end of 2026); narrator voices from the library; any non-retiring
    voice."""
    def named(pool):
        for pref in PREFERRED_VOICES:
            for v in pool:
                if v["name"].lower() == pref.lower():
                    return v
    lasting = [v for v in voices if not v["retiring"]]
    narrators = [v for v in lasting
                 if v.get("use") in ("narrative_story", "informative_educational")]
    own = [v for v in narrators if v.get("premade")]
    pick = (named(lasting) or (own[0] if own else None) or named(voices)
            or (narrators[0] if narrators else None) or (lasting or voices or [None])[0])
    return pick["id"] if pick else ""


def list_models(key: str) -> dict:
    """TTS-capable models of the account -> {model_id: credits_per_char}.
    Gives the tray the real cost factor instead of the table above."""
    out = {}
    with _request("GET", "/v1/models", key) as r:
        for m in json.loads(r.read().decode("utf-8")):
            if not m.get("can_do_text_to_speech"):
                continue
            rate = (m.get("model_rates") or {}).get("character_cost_multiplier")
            out[m["model_id"]] = float(rate) if rate else model_info(m["model_id"])["credits"]
    return out


# --------------------------------------------------------------------------- #
#  Audio cache
# --------------------------------------------------------------------------- #
def cache_dir() -> Path:
    if tts_platform.IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
        return base / "tts-elevenlabs-cache"
    return Path.home() / ".cache/tts-elevenlabs"


def _cache_file(text, voice, model, speed) -> Path:
    h = hashlib.sha256(f"{model}\0{voice}\0{speed:.2f}\0{text}".encode("utf-8"))
    return cache_dir() / f"{h.hexdigest()[:40]}.pcm"


def _cache_prune():
    try:
        files = sorted(cache_dir().glob("*.pcm"), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        while files and total > CACHE_MAX_BYTES:
            p = files.pop(0)
            total -= p.stat().st_size
            p.unlink()
    except OSError:
        pass


def cache_stats():
    """(number of sentences, bytes) in the cache, shown in the tray."""
    try:
        files = list(cache_dir().glob("*.pcm"))
        return len(files), sum(p.stat().st_size for p in files)
    except OSError:
        return 0, 0


def cache_clear():
    for p in cache_dir().glob("*.pcm"):
        try:
            p.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
#  Synthesis
# --------------------------------------------------------------------------- #
def speed_from_length_scale(length_scale) -> float:
    """Piper length_scale (smaller = faster) -> ElevenLabs speed.
    The API accepts only 0.7 .. 1.2; values outside are clamped."""
    s = 1.0 / (length_scale or 1.0)
    return round(max(0.7, min(1.2, s)), 2)


def stream_pcm(text, voice_id, model_id, key, length_scale=1.0, lang=None,
               previous_text=None, next_text=None, use_cache=True):
    """Generator of PCM bytes (s16le, mono, 24 kHz) for one sentence.

    Raises ElevenLabsError before the first byte if the request fails, so the
    caller can fall back to Piper cleanly. A fully streamed sentence is
    written to the cache."""
    speed = speed_from_length_scale(length_scale)
    cf = _cache_file(text, voice_id, model_id, speed) if use_cache else None
    if cf is not None and cf.exists():
        try:
            os.utime(cf)                         # LRU: mark as recently played
            yield cf.read_bytes()
            return
        except OSError:
            pass

    info = model_info(model_id)
    settings = {"stability": 0.5, "similarity_boost": 0.75}
    if info.get("speed"):
        settings["speed"] = speed
    body = {"text": text, "model_id": model_id, "voice_settings": settings}
    if lang and info.get("lang_code"):
        body["language_code"] = lang
    if info.get("context"):
        if previous_text:
            body["previous_text"] = previous_text[-300:]
        if next_text:
            body["next_text"] = next_text[:300]

    resp = _request("POST", f"/v1/text-to-speech/{voice_id}/stream", key, body,
                    params={"output_format": f"pcm_{SAMPLE_RATE}"})
    parts, carry, complete = [], b"", False
    try:
        while True:
            buf = resp.read(4096)
            if not buf:
                complete = True
                break
            buf = carry + buf
            cut = len(buf) & ~1                  # whole 16-bit samples only
            buf, carry = buf[:cut], buf[cut:]
            if buf:
                if cf is not None:
                    parts.append(buf)
                yield buf
    finally:
        resp.close()
        if complete and cf is not None and parts:
            try:
                cf.parent.mkdir(parents=True, exist_ok=True)
                tmp = cf.with_suffix(".tmp")
                tmp.write_bytes(b"".join(parts))
                os.replace(tmp, cf)
                if time.time() % 20 < 1:         # prune now and then
                    _cache_prune()
            except OSError:
                pass


def credits_per_hour(credits_per_char: float) -> int:
    return int(CHARS_PER_MINUTE * 60 * credits_per_char)
