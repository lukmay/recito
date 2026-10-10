"""bin/tts_neural.py: voice list merging, capabilities and start backoff."""
import json
from types import SimpleNamespace

import pytest

import tts_neural

META = {
    "voices": [
        {"id": "thorsten", "lang": "de"},
        {"id": "af_heart", "lang": "en"},
        {"id": "random", "lang": "multi"},
        {"id": "ref:server_side", "lang": "de"},    # refs come from the voices folder only
    ],
    "default": {"de": "martin", "en": "af_heart"},
}
REFS = [{"id": "mine", "lang": "de"}, {"id": "en_male_1", "lang": "en"},
        {"id": "thorsten", "lang": "de"}]


def test_merge_orders_default_model_refs():
    out = tts_neural.merge_voice_lists(META, REFS)
    assert out["de"] == ["martin", "thorsten", "random",
                         "ref:mine", "ref:thorsten", "ref:en_male_1"]
    assert out["en"] == ["af_heart", "random", "ref:en_male_1", "ref:mine", "ref:thorsten"]


def test_merge_without_cache_or_refs():
    assert tts_neural.merge_voice_lists({}, []) == {"de": [], "en": []}
    assert tts_neural.merge_voice_lists({"default": {"de": "x"}}, []) == {"de": ["x"], "en": []}


def test_merge_drops_duplicates():
    meta = {"voices": [{"id": "a", "lang": "de"}, {"id": "a", "lang": None}],
            "default": {"de": "a"}}
    assert tts_neural.merge_voice_lists(meta, [])["de"] == ["a"]


@pytest.fixture
def registry(monkeypatch):
    reg = {"cloner": {"title": "Cloner", "clone": True, "tags": True},
           "plain": {"title": "Plain", "cpu_ok": True}}
    monkeypatch.setattr(tts_neural, "REGISTRY", reg)
    return reg


def test_capabilities(registry):
    assert tts_neural.capabilities("cloner") == {
        "clone": True, "cpu_ok": False, "instruct": False, "tags": True, "markup": False}
    assert tts_neural.capabilities("plain")["cpu_ok"] is True
    assert tts_neural.capabilities("missing") == dict.fromkeys(tts_neural.CAPABILITIES, False)
    assert tts_neural.clones("cloner") and not tts_neural.clones("plain")
    assert tts_neural.title("plain") == "Plain" and tts_neural.title("missing") == "missing"


def test_voice_lists_reads_cache_and_voices_folder(registry, isolated_dirs, monkeypatch):
    import tts_voices
    root = isolated_dirs["TTS_NEURAL_DIR"]
    (root / "cloner").mkdir()
    (root / "cloner" / "voices.json").write_text(json.dumps(META), encoding="utf-8")
    monkeypatch.setattr(tts_voices, "list_voices", lambda: REFS)
    assert tts_neural.voice_lists("cloner") == tts_neural.merge_voice_lists(META, REFS)
    assert tts_neural.voice_lists("plain") == {"de": [], "en": []}


@pytest.fixture
def fake_launcher(monkeypatch):
    clock = SimpleNamespace(now=1000.0)
    state = SimpleNamespace(healthy=False, starts=0, fail=True)

    def start(key, wait=True, timeout=0):
        state.starts += 1
        if state.fail:
            raise RuntimeError("guard says no")
        state.healthy = True

    fake = SimpleNamespace(port_of=lambda key: 40000,
                           healthy=lambda port, timeout=1.0: state.healthy,
                           start=start)
    monkeypatch.setattr(tts_neural, "launcher", fake)
    monkeypatch.setattr(tts_neural, "_broken_until", {})
    monkeypatch.setattr(tts_neural.time, "time", lambda: clock.now)
    return SimpleNamespace(clock=clock, state=state)


def test_ensure_backoff_after_failed_start(fake_launcher):
    st, clock = fake_launcher.state, fake_launcher.clock
    with pytest.raises(RuntimeError, match="guard says no"):
        tts_neural.ensure("m")
    assert st.starts == 1

    clock.now += tts_neural.START_BACKOFF_S - 1
    with pytest.raises(RuntimeError, match="failed recently"):
        tts_neural.ensure("m")
    assert st.starts == 1                          # no new attempt during the backoff

    clock.now += 2
    st.fail = False
    assert tts_neural.ensure("m") == 40000
    assert st.starts == 2


def test_ensure_skips_start_when_healthy(fake_launcher):
    fake_launcher.state.healthy = True
    assert tts_neural.ensure("m") == 40000
    assert fake_launcher.state.starts == 0


def test_without_launcher(monkeypatch):
    monkeypatch.setattr(tts_neural, "launcher", None)
    assert tts_neural.installed("x") is False
    assert tts_neural.voices_cache("x") == {}
    tts_neural.stop_others()                       # no-op
    tts_neural.start_idle_reaper(["x"], 30)        # no-op
