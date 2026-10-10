"""bin/tts_previews.py: cache paths, index, idempotent build and the rules for
what may be cached. Renderers and the model launcher are fakes; nothing
synthesizes, uses the network or starts a server."""
import json
import wave

import pytest

import tts_neural
import tts_previews as tp

PCM = b"\x01\x00" * 2400                         # 0.1 s at 24 kHz


@pytest.fixture(autouse=True)
def preview_dir(tmp_path, monkeypatch):
    d = tmp_path / "previews"
    monkeypatch.setenv("TTS_PREVIEW_DIR", str(d))
    return d


def fake_renderer(calls, fail=()):
    def render(voice, lang, text):
        calls.append((voice, lang, text))
        if voice in fail:
            raise RuntimeError("offline")
        return PCM, 24000
    return render


def test_cache_dir_env_and_platform_default(preview_dir, monkeypatch, isolated_dirs, tmp_path):
    assert tp.cache_dir() == preview_dir
    monkeypatch.delenv("TTS_PREVIEW_DIR")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    expected = (isolated_dirs["LOCALAPPDATA"] if tp.os.name == "nt" else tmp_path / "xdg") / "tts-previews"
    assert tp.cache_dir() == expected


def test_sample_follows_voice_language():
    assert tp.sample("de").startswith("Hallo")
    assert tp.sample("en").startswith("Hello")
    assert tp.sample("fr").startswith("Bonjour")
    assert tp.sample("xx") == tp.sample("en")          # unknown language: English


def test_relpath_is_stable_and_distinct():
    a = tp.relpath("edge", "de-DE-KatjaNeural", "de")
    assert a == tp.relpath("edge", "de-DE-KatjaNeural", "de", 1.0)
    assert a.startswith("edge/de-de-DE-KatjaNeural-") and a.endswith(".wav")
    others = {tp.relpath("edge", "de-DE-KatjaNeural", "en"),
              tp.relpath("edge", "de-DE-ConradNeural", "de"),
              tp.relpath("edge", "de-DE-KatjaNeural", "de", 0.85),
              tp.relpath("piper", "de-DE-KatjaNeural", "de"),
              tp.relpath("elevenlabs", "v1", "de", 1.0, "eleven_v4")}
    assert a not in others and len(others) == 5
    assert tp.relpath("elevenlabs", "v1", "de", 1.0, "eleven_v4") != \
        tp.relpath("elevenlabs", "v1", "de", 1.0, "eleven_flash_v2_5")


def test_changed_sample_text_never_hits_old_file(monkeypatch):
    before = tp.relpath("piper", "de_DE-thorsten-high", "de")
    monkeypatch.setitem(tp.SAMPLE, "de", "Ein anderer Satz.")
    assert tp.relpath("piper", "de_DE-thorsten-high", "de") != before


@pytest.mark.parametrize("args,ok", [
    (("edge", "en-US-AriaNeural", "en"), True),
    (("edge", "en-US-AriaNeural", "en", "Hello! This is how I sound when I read a text to you."), True),
    (("edge", "en-US-AriaNeural", "en", "Some other text."), False),
    (("chatterbox", "ref:anna", "de"), False),          # own voices can be re-recorded
    (("qwen3", "Vivian", "de", None, "calm"), False),   # style instruction changes the audio
    (("qwen3", "Vivian", "de", None, "  "), True),
    (("edge", "en-US-AriaNeural", "en", None, "", 300), False),
    (("edge", "", "en"), False),
    (("edge", "en-US-AriaNeural", "xx"), False),
    (("edge", "fr-FR-DeniseNeural", "fr"), True),
])
def test_cacheable(args, ok):
    assert tp.cacheable(*args) is ok


def test_from_request_keeps_model_only_for_elevenlabs():
    assert tp.from_request({"engine": "edge", "voice": "x", "lang": "de", "scale": "0.85",
                            "model": "m"}) == \
        {"engine": "edge", "voice": "x", "lang": "de", "scale": 0.85, "model": ""}
    assert tp.from_request({"engine": "elevenlabs", "voice": "v", "lang": "en",
                            "model": "eleven_v4"})["model"] == "eleven_v4"
    assert tp.from_request({"engine": "edge", "voice": "x", "lang": "de", "pauseMs": 200}) is None
    assert tp.from_request({"engine": "edge", "voice": "x", "lang": "de", "text": "Probe."}) is None


def test_store_writes_wav_and_index(preview_dir):
    assert tp.lookup("edge", "v", "de") is None
    p = tp.store("edge", "v", "de", PCM + b"\x00", 24000, scale=1.0)   # odd byte is dropped
    assert tp.lookup("edge", "v", "de") == p
    with wave.open(str(p)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()) == \
            (24000, 1, 2, len(PCM) // 2)
    idx = json.loads((preview_dir / "index.json").read_text(encoding="utf-8"))
    item = idx["items"][tp.relpath("edge", "v", "de")]
    assert item["engine"] == "edge" and item["voice"] == "v" and item["lang"] == "de"
    assert item["seconds"] == 0.1
    assert not list(preview_dir.rglob("*.tmp"))
    with pytest.raises(ValueError):
        tp.store("edge", "v", "de", b"", 24000)


def test_lookup_ignores_empty_file(preview_dir):
    p = tp.path_for("edge", "v", "de")
    p.parent.mkdir(parents=True)
    p.write_bytes(b"RIFF")
    assert tp.lookup("edge", "v", "de") is None


def test_broken_index_starts_fresh(preview_dir):
    preview_dir.mkdir()
    (preview_dir / "index.json").write_text("{not json", encoding="utf-8")
    tp.store("piper", "a", "en", PCM, 22050)
    assert list(tp.read_index()["items"]) == [tp.relpath("piper", "a", "en")]


def test_build_renders_once_and_is_idempotent():
    calls = []
    voices = {"edge": {"de": ["k1", "k2"], "en": ["a1", "ref:own"]}}
    stats = tp.build(["edge"], voices=voices, renderers={"edge": fake_renderer(calls)}, log=lambda m: None)
    assert stats == {"rendered": 3, "skipped": 0, "failed": 0}
    assert ("k1", "de", tp.sample("de")) in calls and ("a1", "en", tp.sample("en")) in calls
    assert all(v != "ref:own" for v, *_ in calls)
    assert len(tp.read_index()["items"]) == 3

    calls.clear()
    stats = tp.build(["edge"], voices=voices, renderers={"edge": fake_renderer(calls)}, log=lambda m: None)
    assert stats == {"rendered": 0, "skipped": 3, "failed": 0} and calls == []

    stats = tp.build(["edge"], voices=voices, renderers={"edge": fake_renderer(calls)}, force=True,
                     log=lambda m: None)
    assert stats["rendered"] == 3


def test_build_skips_the_old_kokoro_engine():
    calls = []
    stats = tp.build(["kokoro"], voices={"kokoro": {"de": ["martin"]}},
                     renderers={"kokoro": fake_renderer(calls)}, log=lambda m: None)
    assert calls == [] and stats["rendered"] == 0


def test_build_gives_up_when_offline():
    calls = []
    ids = [f"v{i}" for i in range(10)]
    stats = tp.build(["edge"], voices={"edge": {"de": ids}},
                     renderers={"edge": fake_renderer(calls, fail=set(ids))}, log=lambda m: None)
    assert stats == {"rendered": 0, "skipped": 0, "failed": tp.GIVE_UP_AFTER}
    assert len(calls) == tp.GIVE_UP_AFTER


def test_build_keeps_going_after_single_failures():
    calls = []
    ids = ["ok1", "bad1", "bad2", "bad3", "bad4", "ok2"]
    stats = tp.build(["piper"], voices={"piper": {"en": ids}},
                     renderers={"piper": fake_renderer(calls, fail={"bad1", "bad2", "bad3", "bad4"})},
                     log=lambda m: None)
    assert stats == {"rendered": 2, "skipped": 0, "failed": 4}
    assert tp.lookup("piper", "ok2", "en") is not None


class FakeLauncher:
    def __init__(self, running=()):
        self._running = list(running)
        self.events = []

    def running(self):
        return list(self._running)

    def port_of(self, key):
        return 1

    def healthy(self, port, timeout=1.0):
        return False

    def start(self, key, wait=True, timeout=240):
        self.events.append(("start", key))

    def stop(self, key):
        self.events.append(("stop", key))


def test_neural_build_never_evicts_a_running_model(monkeypatch):
    L = FakeLauncher(running=["chatterbox"])
    monkeypatch.setattr(tts_neural, "launcher", L)
    monkeypatch.setattr(tts_neural, "installed", lambda key: True)
    monkeypatch.setattr(tts_neural, "ENGINES", {"kokoro2", "chatterbox"})
    monkeypatch.setattr(tts_neural, "stream", lambda *a, **k: pytest.fail("must not synthesize"))
    logs = []
    stats = tp.build(["kokoro2"], voices={"kokoro2": {"de": ["thorsten"]}}, log=logs.append)
    assert L.events == [] and stats["rendered"] == 0
    assert any("skipped" in m for m in logs)


def test_neural_build_starts_and_stops_its_own_server(monkeypatch):
    L = FakeLauncher()
    monkeypatch.setattr(tts_neural, "launcher", L)
    monkeypatch.setattr(tts_neural, "installed", lambda key: True)
    monkeypatch.setattr(tts_neural, "ENGINES", {"kokoro2"})

    def stream(key, text, voice="", lang="de", speed=1.0, instruct=""):
        assert ("start", "kokoro2") in L.events and speed == 1.0 and instruct == ""
        yield PCM, 24000

    monkeypatch.setattr(tts_neural, "stream", stream)
    stats = tp.build(["kokoro2"], voices={"kokoro2": {"de": ["thorsten"], "en": ["af_heart"]}},
                     log=lambda m: None)
    assert stats["rendered"] == 2
    assert L.events == [("start", "kokoro2"), ("stop", "kokoro2")]


def test_neural_build_skips_when_not_installed(monkeypatch):
    monkeypatch.setattr(tts_neural, "launcher", FakeLauncher())
    monkeypatch.setattr(tts_neural, "installed", lambda key: False)
    monkeypatch.setattr(tts_neural, "ENGINES", {"qwen3"})
    assert tp.build(["qwen3"], voices={"qwen3": {"de": ["x"]}}, log=lambda m: None)["rendered"] == 0


def test_cli_list_and_clear(preview_dir, capsys):
    tp.store("edge", "v", "de", PCM, 24000)
    assert tp.main(["list"]) == 0
    assert "edge" in capsys.readouterr().out
    assert tp.main(["clear"]) == 0
    assert tp.lookup("edge", "v", "de") is None
