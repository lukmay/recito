"""Reading languages in the daemon: detection restricted to read_langs and
the per-language fallback (engine, Piper voice of the language, Edge, default
Piper voice). Piper, lingua, Edge and the neural servers are fakes."""
import json
import sys
import types

import pytest

from test_daemon import daemon  # noqa: F401  (fixture)
from test_tts_langs import fake_lingua


class _Chunk:
    sample_rate = 22050
    sample_channels = 1

    def __init__(self, text):
        self.audio_int16_bytes = text.encode()


class _Voice:
    def __init__(self, name):
        self.name = name

    def synthesize(self, text, syn_config=None):
        yield _Chunk(f"piper-{self.name}:{text}")


@pytest.fixture
def langd(daemon, monkeypatch, isolated_dirs):  # noqa: F811
    """daemon() with Piper voices named after their file and a fake lingua."""
    seen = []
    monkeypatch.setitem(sys.modules, "lingua", fake_lingua(seen))
    piper = sys.modules["piper"]
    monkeypatch.setattr(piper, "PiperVoice", types.SimpleNamespace(
        load=lambda path, **k: _Voice(path.replace("\\", "/").rsplit("/", 1)[-1][:2])))

    def load(conf, voices=("fr_FR-siwis-medium",)):
        for v in voices:
            (isolated_dirs["PIPER_DIR"] / f"{v}.onnx").write_bytes(b"")
        d = daemon(conf)
        d.detected_for = seen
        return d
    return load


def test_without_read_langs_detection_is_de_en(langd):
    d = langd("engine=piper\nen=fr_FR-siwis-medium\n", voices=("fr_FR-siwis-medium",))
    assert d.READ_LANGS == ["de", "en"] and d.DEFAULT_KEY == "de"
    assert d.detected_for == ["DE", "EN"]


def test_detection_only_among_reading_languages(langd):
    d = langd("engine=piper\nread_langs=de,fr\n")
    assert d.detected_for == ["DE", "FR"]
    # the French Piper voice was found without a fr= line in voices.conf
    assert d.VOICE_MAP["fr"] == "fr_FR-siwis-medium"
    assert d.run('{"text": "Bonjour [fr]. Encore une phrase.", "voice": "auto"}') == \
        ["piper-fr:Bonjour [fr].", "piper-fr:Encore une phrase."]   # once per text
    assert d.rules_lang("auto", "Bonjour [fr]") == "fr"


def test_per_sentence_detection(langd):
    d = langd("engine=piper\nread_langs=de,fr\nlang_detect=sentence\n")
    assert d.run('{"text": "Bonjour [fr]. Hallo [de].", "voice": "auto"}') == \
        ["piper-fr:Bonjour [fr].", "piper-de:Hallo [de]."]


def test_engine_reads_its_languages_piper_the_others(langd):
    d = langd("engine=kokoro2\nread_langs=de,fr\nlang_detect=sentence\n")
    assert d.speakable("fr") and not d.ACTIVE.ready("fr") and d.ACTIVE.ready("de")
    assert d.run('{"text": "Hallo [de]. Bonjour [fr].", "voice": "auto"}') == \
        ["neural:Hallo [de].", "piper-fr:Bonjour [fr]."]
    assert all(c[4] == "de" for c in d.calls if c[0] == "stream")


def test_edge_steps_in_without_a_piper_voice(langd, monkeypatch):
    monkeypatch.setitem(sys.modules, "edge_tts", types.ModuleType("edge_tts"))
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    d = langd("engine=kokoro2\nread_langs=de,es\nlang_detect=sentence\n", voices=())
    assert d.EDGE_LANG_OK and d.speakable("es")
    used = []

    def edge(sentence, voice, ls):
        used.append(voice)
        yield _Chunk("edge:" + sentence)
    monkeypatch.setattr(d, "edge_chunks", edge)
    assert d.run('{"text": "Hola [es].", "voice": "auto"}') == ["edge:Hola [es]."]
    assert used == ["es-ES-ElviraNeural"]


def test_failing_edge_falls_back_to_piper_for_the_rest(langd, monkeypatch):
    monkeypatch.setitem(sys.modules, "edge_tts", types.ModuleType("edge_tts"))
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    d = langd("engine=kokoro2\nread_langs=de,es\n", voices=())
    calls = []

    def edge(sentence, voice, ls):
        calls.append(sentence)
        raise OSError("offline")
        yield
    monkeypatch.setattr(d, "edge_chunks", edge)
    assert d.run('{"text": "Hola [es]. Otra [es].", "voice": "es"}') == \
        ["piper-de:Hola [es].", "piper-de:Otra [es]."]
    assert calls == ["Hola [es]."]                   # tried once per job


def test_piper_engine_never_goes_online(langd, monkeypatch):
    monkeypatch.setitem(sys.modules, "edge_tts", types.ModuleType("edge_tts"))
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    d = langd("engine=piper\nread_langs=de,es\n", voices=())
    assert not d.EDGE_LANG_OK and not d.speakable("es")
    assert d.DETECTOR is None                         # only one readable language
    assert d.run(json.dumps({"text": "Hola.", "voice": "auto"})) == ["piper-de:Hola."]


def test_elevenlabs_sends_the_language_code(langd, monkeypatch):
    import tts_elevenlabs as eleven
    monkeypatch.setattr(eleven, "load_key", lambda: "k")
    sent = []

    def stream_pcm(text, voice, model, key, ls, lang=None, **kw):
        sent.append((voice, lang))
        yield b"el"
    monkeypatch.setattr(eleven, "stream_pcm", stream_pcm)
    d = langd("engine=elevenlabs\nread_langs=fr,de\nelevenlabs_de=v-de\n")
    assert d.DEFAULT_KEY == "fr"
    d.run('{"text": "Bonjour [fr].", "voice": "fr"}')
    assert sent == [("v-de", "fr")]                  # multilingual voice of another language
