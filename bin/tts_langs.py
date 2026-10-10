"""
tts_langs: the reading languages, what each engine can speak, and the
per-language data the app needs (sample sentences, default voices).

voices.conf:
  read_langs=de,en,fr    languages the user reads, in order of preference;
                         missing or empty means de,en
  <lang>=<onnx base>     Piper voice per language, e.g. fr=fr_FR-siwis-medium
  <engine>_<lang>=<id>   voice of another engine, e.g. edge_fr, chatterbox_fr

Language detection only chooses among the reading languages. A language the
active engine cannot speak is read by the Piper voice of that language, else
by Edge's default voice of that language (online engines and neural engines,
not with engine=piper), else by the default Piper voice. See speak_route().

No Qt, no network on import. The Piper download (download_piper_voice) is the
only function that goes online.
"""

import os
import re
import shutil
import urllib.request
from pathlib import Path

# Reading languages the app offers, in display order.
SUPPORTED = ("de", "en", "fr", "es", "it", "nl", "pt", "pl", "sv", "da", "cs", "ru", "tr")
DEFAULT_READ = ("de", "en")

# Names in the language itself, for logs and the CLI (the UI uses lang.<code>).
NATIVE = {"de": "Deutsch", "en": "English", "fr": "Français", "es": "Español",
          "it": "Italiano", "nl": "Nederlands", "pt": "Português", "pl": "Polski",
          "sv": "Svenska", "da": "Dansk", "cs": "Čeština", "ru": "Русский", "tr": "Türkçe"}

# Languages per engine. None = every reading language (Piper through
# downloadable voices). Dynamic engines (sapi, mssp) depend on the installed
# voices; see voice_langs(). A neural server can report its own list in
# /voices ("languages"), which tts_neural prefers over this table.
ENGINE_LANGS = {
    "piper": None,
    "edge": None,
    "elevenlabs": None,                     # multilingual models, 29+ languages
    "kokoro2": ("de", "en"),                # this server: English and German voices only
    "chatterbox": ("de", "en", "fr", "es", "it", "nl", "pt", "pl", "sv", "da", "ru", "tr"),
    "qwen3": ("de", "en", "fr", "es", "it", "pt", "ru"),
    "fish": ("de", "en", "fr", "es", "it", "nl", "pt", "pl", "ru"),
    "stepaudio": ("de", "en"),
}
DYNAMIC = ("sapi", "mssp")

SAMPLE = {
    "de": "Hallo! So klinge ich, wenn ich dir einen Text vorlese.",
    "en": "Hello! This is how I sound when I read a text to you.",
    "fr": "Bonjour ! Voici ma voix quand je te lis un texte.",
    "es": "¡Hola! Así sueno cuando te leo un texto.",
    "it": "Ciao! Ecco come suono quando ti leggo un testo.",
    "nl": "Hallo! Zo klink ik als ik je een tekst voorlees.",
    "pt": "Olá! É assim que eu soo quando leio um texto para você.",
    "pl": "Cześć! Tak brzmię, kiedy czytam ci tekst.",
    "sv": "Hej! Så här låter jag när jag läser en text för dig.",
    "da": "Hej! Sådan lyder jeg, når jeg læser en tekst op for dig.",
    "cs": "Ahoj! Takhle zním, když ti čtu text.",
    "ru": "Привет! Вот так я звучу, когда читаю тебе текст.",
    "tr": "Merhaba! Sana bir metin okurken sesim böyle.",
}

# Three short sentences for the sentence-pause preview.
SAMPLE_PAUSE = {
    "de": "Erster Satz. Zweiter Satz. Und ein dritter Satz zum Abschluss.",
    "en": "First sentence. Second sentence. And a third one to finish.",
    "fr": "Première phrase. Deuxième phrase. Et une troisième pour finir.",
    "es": "Primera frase. Segunda frase. Y una tercera para terminar.",
    "it": "Prima frase. Seconda frase. E una terza per finire.",
    "nl": "Eerste zin. Tweede zin. En een derde zin om af te sluiten.",
    "pt": "Primeira frase. Segunda frase. E uma terceira para terminar.",
    "pl": "Pierwsze zdanie. Drugie zdanie. I trzecie na zakończenie.",
    "sv": "Första meningen. Andra meningen. Och en tredje för att avsluta.",
    "da": "Første sætning. Anden sætning. Og en tredje til sidst.",
    "cs": "První věta. Druhá věta. A třetí na závěr.",
    "ru": "Первое предложение. Второе предложение. И третье в завершение.",
    "tr": "Birinci cümle. İkinci cümle. Ve bitirmek için üçüncü bir cümle.",
}

# Curated Piper voice per language, downloaded on request (about 63 MB each).
PIPER_DEFAULT = {
    "de": "de_DE-thorsten-medium", "en": "en_US-lessac-medium",
    "fr": "fr_FR-siwis-medium", "es": "es_ES-davefx-medium", "it": "it_IT-paola-medium",
    "nl": "nl_NL-ronnie-medium", "pt": "pt_BR-faber-medium", "pl": "pl_PL-gosia-medium",
    "sv": "sv_SE-lisa-medium", "da": "da_DK-talesyntese-medium", "cs": "cs_CZ-jirka-medium",
    "ru": "ru_RU-irina-medium", "tr": "tr_TR-dfki-medium",
}
PIPER_MB = 63

# Hand-picked Edge voices: (id, display name, gender, trait). The first one
# is the default and the language fallback of the daemon.
EDGE_PICKS = {
    "de": [("de-DE-KatjaNeural", "Katja", "f", "clear"), ("de-DE-ConradNeural", "Conrad", "m", "calm"),
           ("de-DE-AmalaNeural", "Amala", "f", "friendly"),
           ("de-DE-KillianNeural", "Killian", "m", "lively")],
    "en": [("en-US-AriaNeural", "Aria", "f", "warm"), ("en-US-GuyNeural", "Guy", "m", "steady"),
           ("en-GB-SoniaNeural", "Sonia", "f", "british"),
           ("en-GB-RyanNeural", "Ryan", "m", "british")],
    "fr": [("fr-FR-DeniseNeural", "Denise", "f", "clear"), ("fr-FR-HenriNeural", "Henri", "m", "calm"),
           ("fr-FR-VivienneMultilingualNeural", "Vivienne", "f", "warm"),
           ("fr-FR-RemyMultilingualNeural", "Rémy", "m", "lively")],
    "es": [("es-ES-ElviraNeural", "Elvira", "f", "clear"), ("es-ES-AlvaroNeural", "Álvaro", "m", "calm"),
           ("es-ES-XimenaNeural", "Ximena", "f", "friendly")],
    "it": [("it-IT-ElsaNeural", "Elsa", "f", "clear"), ("it-IT-DiegoNeural", "Diego", "m", "calm"),
           ("it-IT-IsabellaNeural", "Isabella", "f", "warm"),
           ("it-IT-GiuseppeMultilingualNeural", "Giuseppe", "m", "lively")],
    "nl": [("nl-NL-FennaNeural", "Fenna", "f", "clear"), ("nl-NL-MaartenNeural", "Maarten", "m", "calm"),
           ("nl-NL-ColetteNeural", "Colette", "f", "friendly")],
    "pt": [("pt-BR-FranciscaNeural", "Francisca", "f", "warm"),
           ("pt-BR-AntonioNeural", "Antonio", "m", "calm"),
           ("pt-PT-RaquelNeural", "Raquel", "f", "clear"), ("pt-PT-DuarteNeural", "Duarte", "m", "steady")],
    "pl": [("pl-PL-ZofiaNeural", "Zofia", "f", "clear"), ("pl-PL-MarekNeural", "Marek", "m", "calm")],
    "sv": [("sv-SE-SofieNeural", "Sofie", "f", "clear"), ("sv-SE-MattiasNeural", "Mattias", "m", "calm")],
    "da": [("da-DK-ChristelNeural", "Christel", "f", "clear"), ("da-DK-JeppeNeural", "Jeppe", "m", "calm")],
    "cs": [("cs-CZ-VlastaNeural", "Vlasta", "f", "clear"), ("cs-CZ-AntoninNeural", "Antonín", "m", "calm")],
    "ru": [("ru-RU-SvetlanaNeural", "Svetlana", "f", "clear"), ("ru-RU-DmitryNeural", "Dmitry", "m", "calm")],
    "tr": [("tr-TR-EmelNeural", "Emel", "f", "clear"), ("tr-TR-AhmetNeural", "Ahmet", "m", "calm")],
}

# Primary language ids of Windows locales (LCID & 0x3FF), for SAPI voices.
LCID_PRIMARY = {"de": 0x07, "en": 0x09, "fr": 0x0C, "es": 0x0A, "it": 0x10, "nl": 0x13,
                "pt": 0x16, "pl": 0x15, "sv": 0x1D, "da": 0x06, "cs": 0x05, "ru": 0x19,
                "tr": 0x1F}

# Tesseract language packs, for OCR of the reading languages.
TESSERACT = {"de": "deu", "en": "eng", "fr": "fra", "es": "spa", "it": "ita", "nl": "nld",
             "pt": "por", "pl": "pol", "sv": "swe", "da": "dan", "cs": "ces", "ru": "rus",
             "tr": "tur"}


# --------------------------------------------------------------------------- #
#  Reading languages
# --------------------------------------------------------------------------- #
def norm(code):
    """'de', 'DE', 'de-DE', 'de_AT' -> 'de'; '' for anything not supported."""
    c = re.split(r"[-_.:@]", str(code or "").strip().lower())[0]
    return c if c in SUPPORTED else ""


def parse_read_langs(value, default=DEFAULT_READ):
    """read_langs value -> list of supported codes, order kept, no duplicates.
    Missing, empty or only unknown codes give `default`."""
    out = []
    for part in re.split(r"[,;\s]+", str(value or "")):
        c = norm(part)
        if c and c not in out:
            out.append(c)
    return out or list(default)


def format_read_langs(langs):
    return ",".join(parse_read_langs(",".join(langs or [])))


def system_read_langs(ui_lang=""):
    """Preselection for the setup assistant: the system (or app) language if
    it is a reading language, plus English."""
    first = norm(ui_lang)
    if not first:
        try:
            import tts_i18n
            first = norm(tts_i18n.system_language())
        except Exception:
            first = ""
    out = [first] if first else []
    if "en" not in out:
        out.append("en")
    return out


# --------------------------------------------------------------------------- #
#  Engines
# --------------------------------------------------------------------------- #
def engine_langs(engine, reported=None, voice_langs_=None):
    """Languages an engine speaks, as a tuple in SUPPORTED order.

    reported: list from a neural server's /voices (preferred if given).
    voice_langs_: languages of installed voices, for sapi and mssp."""
    if reported:
        have = {norm(x) for x in reported}
        return tuple(c for c in SUPPORTED if c in have)
    if engine in DYNAMIC:
        have = set(voice_langs_ or ()) | set(DEFAULT_READ)
        return tuple(c for c in SUPPORTED if c in have)
    langs = ENGINE_LANGS.get(engine, DEFAULT_READ)
    return SUPPORTED if langs is None else tuple(c for c in SUPPORTED if c in langs)


def supports(engine, lang, **kw):
    return norm(lang) in engine_langs(engine, **kw)


def voice_langs(names):
    """Languages of voice names that carry a locale, e.g. 'Microsoft Server
    Speech Text to Speech Voice (de-DE, Hedda)' or 'fr-FR-DeniseNeural'."""
    out = set()
    for n in names or ():
        for m in re.finditer(r"(?<![a-z])([a-z]{2})-[A-Z]{2}\b", str(n)):
            if m.group(1) in SUPPORTED:
                out.add(m.group(1))
    return out


def edge_default(lang):
    picks = EDGE_PICKS.get(norm(lang))
    return picks[0][0] if picks else ""


def edge_defaults():
    """{lang: [voice ids]} used until the online Edge list has loaded."""
    return {lang: [p[0] for p in EDGE_PICKS[lang]] for lang in SUPPORTED}


def sample(lang):
    return SAMPLE.get(norm(lang) or "en", SAMPLE["en"])


def sample_pause(lang):
    return SAMPLE_PAUSE.get(norm(lang) or "en", SAMPLE_PAUSE["en"])


def speak_route(lang, *, engine_ok, piper_langs, edge_ok):
    """Who reads a unit in `lang`: 'engine', 'piper', 'edge' or 'default'
    (the default Piper voice). engine_ok: the active engine speaks lang now;
    piper_langs: languages with a loaded Piper voice; edge_ok: Edge may step
    in as the language fallback."""
    if engine_ok:
        return "engine"
    if lang in piper_langs:
        return "piper"
    if edge_ok and edge_default(lang):
        return "edge"
    return "default"


# --------------------------------------------------------------------------- #
#  Language detection (lingua)
# --------------------------------------------------------------------------- #
def build_detector(langs, lingua=None):
    """lingua detector restricted to `langs`; (detector, {Language: code}).
    (None, {}) for fewer than two languages or without lingua."""
    langs = [c for c in dict.fromkeys(norm(x) for x in langs) if c]
    if len(langs) < 2:
        return None, {}
    if lingua is None:
        import lingua
    to_lang = {c: lingua.Language.from_iso_code_639_1(getattr(lingua.IsoCode639_1, c.upper()))
               for c in langs}
    det = (lingua.LanguageDetectorBuilder.from_languages(*to_lang.values())
           .with_preloaded_language_models().build())
    return det, {v: k for k, v in to_lang.items()}


# --------------------------------------------------------------------------- #
#  Piper voices
# --------------------------------------------------------------------------- #
_PIPER_URL = ("https://huggingface.co/rhasspy/piper-voices/resolve/main/{family}/{code}/"
              "{name}/{quality}/{code}-{name}-{quality}{ext}?download=true")
_PIPER_NAME = re.compile(r"^(?P<family>[^-_]+)_(?P<region>[^-]+)-(?P<name>[^-]+)-(?P<quality>.+)$")


def piper_installed(piper_dir, lang):
    """Installed Piper voices of a language (onnx base names, sorted)."""
    return sorted(p.stem for p in Path(piper_dir).glob(f"{norm(lang)}_*.onnx"))


def piper_pick(piper_dir, lang):
    """The Piper voice to use for lang when voices.conf names none: the
    curated default if installed, else the first installed one, else ''."""
    have = piper_installed(piper_dir, lang)
    d = PIPER_DEFAULT.get(norm(lang))
    return d if d in have else (have[0] if have else "")


def piper_urls(voice):
    """(onnx url, json url) of a Piper voice name like 'fr_FR-siwis-medium'.
    Same layout as piper.download_voices."""
    m = _PIPER_NAME.match(voice or "")
    if not m:
        raise ValueError(f"not a Piper voice name: {voice!r}")
    fmt = dict(family=m.group("family"), code=f"{m.group('family')}_{m.group('region')}",
               name=m.group("name"), quality=m.group("quality"))
    try:                                   # follow the installed piper package if it differs
        from piper.download_voices import URL_FORMAT as url
        url = url.replace("{lang_family}", "{family}").replace("{lang_code}", "{code}") \
                 .replace("{voice_name}", "{name}").replace("{voice_quality}", "{quality}") \
                 .replace("{extension}", "{ext}")
    except Exception:
        url = _PIPER_URL
    return url.format(ext=".onnx", **fmt), url.format(ext=".onnx.json", **fmt)


def download_piper_voice(voice, piper_dir, progress=None, cancel=None, opener=None,
                         chunk=1 << 16):
    """Download a Piper voice (onnx + json) into piper_dir. Files land as
    .part and are renamed when complete, so a broken download never looks
    installed. progress(done_bytes, total_bytes) follows the onnx file (total
    0 when the server sends no size); cancel() -> True stops it. Returns the
    onnx path."""
    opener = opener or urllib.request.urlopen
    d = Path(piper_dir)
    d.mkdir(parents=True, exist_ok=True)
    onnx_url, json_url = piper_urls(voice)
    targets = [(json_url, d / f"{voice}.onnx.json", False), (onnx_url, d / f"{voice}.onnx", True)]
    parts = []
    try:
        for url, dest, report in targets:
            part = dest.with_name(dest.name + ".part")
            parts.append((part, dest))
            with opener(url, timeout=30) as r, open(part, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0) if report else 0
                done = 0
                while True:
                    if cancel is not None and cancel():
                        raise InterruptedError("download cancelled")
                    buf = r.read(chunk)
                    if not buf:
                        break
                    f.write(buf)
                    done += len(buf)
                    if report and progress:
                        progress(done, total)
            if part.stat().st_size == 0:
                raise OSError(f"empty download: {dest.name}")
        for part, dest in parts:
            os.replace(part, dest)
        return d / f"{voice}.onnx"
    finally:
        for part, _ in parts:
            try:
                part.unlink()
            except OSError:
                pass


def piper_disk_ok(piper_dir, need_mb=PIPER_MB * 2):
    try:
        return shutil.disk_usage(Path(piper_dir)).free > need_mb * 2**20
    except OSError:
        return True
