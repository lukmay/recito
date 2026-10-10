"""Reading languages: parsing, engine coverage, the language fallback route,
detection restricted to the reading languages, and the Piper download."""
import io
import types

import pytest

import tts_config
import tts_langs
import tts_neural


# --- read_langs ---------------------------------------------------------------
@pytest.mark.parametrize("value, langs", [
    (None, ["de", "en"]),                       # missing key: the old pair
    ("", ["de", "en"]),
    ("de,en", ["de", "en"]),
    ("fr", ["fr"]),
    ("de, en ,fr", ["de", "en", "fr"]),
    ("FR;es it", ["fr", "es", "it"]),           # other separators and case
    ("de-DE,en_US,de", ["de", "en"]),           # locales, duplicates
    ("xx,de", ["de"]),                          # unknown codes are dropped
    ("xx", ["de", "en"]),                       # nothing left: default
])
def test_parse_read_langs(value, langs):
    assert tts_langs.parse_read_langs(value) == langs


def test_format_read_langs():
    assert tts_langs.format_read_langs(["fr", "de", "fr", "xx"]) == "fr,de"


def test_every_language_has_its_data():
    for c in tts_langs.SUPPORTED:
        assert tts_langs.SAMPLE[c] and tts_langs.SAMPLE_PAUSE[c].count(".") == 3
        assert tts_langs.PIPER_DEFAULT[c].startswith(c + "_")
        assert tts_langs.edge_default(c).startswith(c + "-")
        assert c in tts_langs.LCID_PRIMARY and c in tts_langs.TESSERACT and c in tts_langs.NATIVE


def test_system_read_langs():
    assert tts_langs.system_read_langs("fr") == ["fr", "en"]
    assert tts_langs.system_read_langs("en") == ["en"]
    assert tts_langs.system_read_langs("de_AT") == ["de", "en"]


# --- config compatibility -----------------------------------------------------
def test_config_without_read_langs_reads_de_en():
    cfg = tts_config.daemon_config(tts_config.parse("de=de_DE-thorsten-low\nen=en_US-amy-low\n"))
    assert cfg.read_langs == ["de", "en"]
    assert cfg.piper_voices == {"de": "de_DE-thorsten-low", "en": "en_US-amy-low"}


def test_config_with_languages_and_engine_voices():
    text = ("read_langs=de,en,fr\nde=de_DE-thorsten-low\nfr=fr_FR-siwis-medium\n"
            "chatterbox_de=ref:anna\nchatterbox_fr=default\nedge_fr=fr-FR-HenriNeural\n"
            "elevenlabs_fr=abc\n")
    cfg = tts_config.daemon_config(tts_config.parse(text), {"chatterbox"})
    assert cfg.read_langs == ["de", "en", "fr"]
    assert cfg.piper_voices == {"de": "de_DE-thorsten-low", "fr": "fr_FR-siwis-medium"}
    assert cfg.voices("edge") == {"fr": "fr-FR-HenriNeural"}
    assert cfg.prefixed("chatterbox") == {"de": "ref:anna", "fr": "default"}
    assert cfg.prefixed("elevenlabs") == {"fr": "abc"}


# --- engines ------------------------------------------------------------------
def test_engine_langs():
    assert tts_langs.engine_langs("edge") == tts_langs.SUPPORTED
    assert tts_langs.engine_langs("kokoro2") == ("de", "en")
    assert "fr" in tts_langs.engine_langs("chatterbox") and "cs" not in tts_langs.engine_langs("chatterbox")
    assert tts_langs.engine_langs("qwen3") == ("de", "en", "fr", "es", "it", "pt", "ru")
    assert tts_langs.engine_langs("unknown") == ("de", "en")
    # a server's own list wins; sapi depends on the installed voices
    assert tts_langs.engine_langs("kokoro2", reported=["en", "fr", "ja"]) == ("en", "fr")
    assert tts_langs.engine_langs("sapi", voice_langs_={"fr"}) == ("de", "en", "fr")


def test_neural_langs_prefer_server_report(monkeypatch):
    monkeypatch.setattr(tts_neural, "voices_cache", lambda key: {"languages": ["de", "fr"]})
    assert tts_neural.langs("kokoro2") == ("de", "fr")
    monkeypatch.setattr(tts_neural, "voices_cache", lambda key: {})
    assert tts_neural.langs("kokoro2") == ("de", "en")


def test_voice_langs_from_names():
    names = ["Microsoft Server Speech Text to Speech Voice (de-DE, Hedda)",
             "fr-FR-DeniseNeural", "ZiraPro", "xx-YY-Foo"]
    assert tts_langs.voice_langs(names) == {"de", "fr"}


def test_merge_voice_lists_for_new_languages():
    meta = {"voices": [{"id": "builtin", "lang": "en"}, {"id": "default", "lang": "de"}],
            "default": {"de": "default", "en": "builtin"}}
    refs = [{"id": "anna", "lang": "de"}, {"id": "luc", "lang": "fr"}]
    lists = tts_neural.merge_voice_lists(meta, refs, ("de", "fr"))
    assert lists["de"][:2] == ["default", "ref:anna"]
    # no own French voice: all own voices, French references before the rest
    assert lists["fr"] == ["builtin", "default", "ref:luc", "ref:anna"]


@pytest.mark.parametrize("engine_ok, piper, edge, route", [
    (True, {"fr"}, True, "engine"),
    (False, {"fr"}, True, "piper"),
    (False, set(), True, "edge"),
    (False, set(), False, "default"),
])
def test_speak_route(engine_ok, piper, edge, route):
    assert tts_langs.speak_route("fr", engine_ok=engine_ok, piper_langs=piper, edge_ok=edge) == route


# --- detection ----------------------------------------------------------------
def fake_lingua(record):
    """Minimal stand-in for the lingua package: remembers which languages the
    detector was built for and 'detects' by a marker word."""
    class Language:
        def __init__(self, code):
            self.code = code

        @staticmethod
        def from_iso_code_639_1(iso):
            return Language(iso)

        def __eq__(self, other):
            return isinstance(other, Language) and other.code == self.code

        def __hash__(self):
            return hash(self.code)

    class Detector:
        def __init__(self, langs):
            self.langs = langs

        def detect_language_of(self, text):
            for lang in self.langs:
                if f"[{lang.code.lower()}]" in text:
                    return lang
            return self.langs[0]

    class Builder:
        @staticmethod
        def from_languages(*langs):
            record.extend(lang.code for lang in langs)
            b = Builder()
            b.langs = list(langs)
            return b

        def with_preloaded_language_models(self):
            return self

        def build(self):
            return Detector(self.langs)

    iso = types.SimpleNamespace(**{c.upper(): c.upper() for c in tts_langs.SUPPORTED})
    return types.SimpleNamespace(Language=Language, IsoCode639_1=iso,
                                 LanguageDetectorBuilder=Builder)


def test_detector_is_restricted_to_the_reading_languages():
    seen = []
    det, to_key = tts_langs.build_detector(["fr", "de", "xx", "fr"], lingua=fake_lingua(seen))
    assert seen == ["FR", "DE"]
    assert sorted(to_key.values()) == ["de", "fr"]
    assert to_key[det.detect_language_of("Bonjour [fr]")] == "fr"


def test_detector_needs_two_languages():
    assert tts_langs.build_detector(["de"], lingua=fake_lingua([])) == (None, {})


def test_real_lingua_detects_only_chosen_languages():
    lingua = pytest.importorskip("lingua")
    det, to_key = tts_langs.build_detector(["de", "fr"], lingua=lingua)
    assert to_key[det.detect_language_of("Bonjour, comment vas-tu aujourd'hui ?")] == "fr"
    # English is not a reading language, so it maps to one of the two
    assert to_key[det.detect_language_of("This is clearly an English sentence.")] in ("de", "fr")


# --- Piper voices ---------------------------------------------------------------
def test_piper_urls_follow_the_voice_layout():
    onnx, js = tts_langs.piper_urls("fr_FR-siwis-medium")
    assert "/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx" in onnx
    assert js.split("?")[0].endswith("fr_FR-siwis-medium.onnx.json")
    with pytest.raises(ValueError):
        tts_langs.piper_urls("siwis")


class _Resp(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_download_piper_voice(tmp_path):
    tmp_path = tmp_path / "voices"
    urls, progress = [], []

    def opener(url, timeout=0):
        urls.append(url)
        return _Resp(b"{}" if ".json" in url else b"x" * 200_000)

    path = tts_langs.download_piper_voice("fr_FR-siwis-medium", tmp_path, opener=opener,
                                          progress=lambda d, t: progress.append((d, t)))
    assert path == tmp_path / "fr_FR-siwis-medium.onnx" and path.stat().st_size == 200_000
    assert (tmp_path / "fr_FR-siwis-medium.onnx.json").exists()
    assert progress[-1] == (200_000, 200_000)
    assert not list(tmp_path.glob("*.part"))
    assert tts_langs.piper_installed(tmp_path, "fr") == ["fr_FR-siwis-medium"]
    assert tts_langs.piper_pick(tmp_path, "fr") == "fr_FR-siwis-medium"


def test_cancelled_download_leaves_nothing(tmp_path):
    tmp_path = tmp_path / "voices"
    opener = lambda url, timeout=0: _Resp(b"x" * 300_000)
    with pytest.raises(InterruptedError):
        tts_langs.download_piper_voice("fr_FR-siwis-medium", tmp_path, opener=opener,
                                       cancel=lambda: True)
    assert list(tmp_path.iterdir()) == []
