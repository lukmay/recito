"""Engine dispatch of bin/piper-read-daemon.py with fakes: Piper, the audio
sink and the neural servers are replaced, nothing binds a port or plays sound."""
import importlib.util
import json
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

import tts_neural
import tts_platform

BIN = Path(__file__).resolve().parent.parent / "bin"


class _Chunk:
    sample_rate = 22050
    sample_channels = 1

    def __init__(self, text):
        self.audio_int16_bytes = text.encode()


class _Voice:
    def synthesize(self, text, syn_config=None):
        yield _Chunk("piper:" + text)


class _Sink:
    def __init__(self, played):
        self.played = played

    def write(self, pcm):
        self.played.append(pcm.decode())
        return True

    def close(self):
        pass

    def kill(self):
        pass

    def pause_toggle(self):
        pass


@pytest.fixture
def daemon(monkeypatch, write_conf, isolated_dirs):
    """Load the daemon module with voices.conf from the test; returns a
    helper that runs one IPC payload and returns the played chunks."""
    piper = types.ModuleType("piper")
    piper.PiperVoice = types.SimpleNamespace(load=lambda *a, **k: _Voice())
    piper.SynthesisConfig = lambda **k: None
    monkeypatch.setitem(sys.modules, "piper", piper)
    (isolated_dirs["PIPER_DIR"] / "de.onnx").write_bytes(b"")

    calls = []
    played = []

    def stream(key, text, voice="", lang="de", speed=1.0, instruct=""):
        if "fail" in text:
            raise RuntimeError("model error")
        calls.append(("stream", key, text, voice, lang, instruct))
        yield ("neural:" + text).encode(), 24000

    monkeypatch.setattr(tts_neural, "stream", stream)
    monkeypatch.setattr(tts_neural, "stop_others", lambda keep=None: calls.append(("stop_others", keep)))
    monkeypatch.setattr(tts_neural, "prewarm", lambda key: calls.append(("prewarm", key)))
    monkeypatch.setattr(tts_neural, "start_idle_reaper", lambda *a: None)
    monkeypatch.setattr(tts_neural, "installed", lambda key: True)
    monkeypatch.setattr(tts_neural, "usable", lambda key: True)
    monkeypatch.setattr(tts_platform, "make_audio_sink", lambda rate, ch: _Sink(played))

    def load(conf):
        # The fakes return text instead of PCM, so loudness processing is off
        # unless a test turns it on.
        write_conf("de=de\nloudness_norm=off\n" + conf)
        spec = importlib.util.spec_from_file_location("piper_read_daemon",
                                                      BIN / "piper-read-daemon.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        def run(payload):
            played.clear()
            mod.handle(payload)
            mod._current.thread.join(5)
            return list(played)

        mod.run = run
        mod.calls = calls
        return mod

    return load


def test_piper_only(daemon):
    d = daemon("engine=piper\n")
    assert d.ACTIVE is None
    assert d.run('{"text": "Eins. Zwei.", "voice": "de"}') == ["piper:Eins.", "piper:Zwei."]


def test_neural_engine_with_fallback(daemon):
    d = daemon("engine=kokoro2\nkokoro2_de=thorsten\n")
    assert d.ACTIVE is d.ENGINES["kokoro2"] and not d.ACTIVE.online
    assert d.run('{"text": "fail here. ok there.", "voice": "de"}') == \
        ["piper:fail here.", "neural:ok there."]          # local engine: fallback per sentence
    assert ("stream", "kokoro2", "ok there.", "thorsten", "de", "") in d.calls


def test_fallback_off_skips_sentence(daemon):
    d = daemon("engine=kokoro2\nfallback=off\n")
    assert d.run('{"text": "fail here. ok there.", "voice": "de"}') == ["neural:ok there."]


def test_preview_uses_requested_engine(daemon):
    d = daemon("engine=piper\n")
    assert d.run('{"cmd": "test", "engine": "kokoro2", "voice": "martin", "key": "de",'
                 ' "instruct": "calm", "text": "Probe."}') == ["neural:Probe."]
    assert ("stream", "kokoro2", "Probe.", "martin", "de", "calm") in d.calls
    assert d.run('{"cmd": "test", "engine": "piper", "voice": "de", "text": "Probe."}') == \
        ["piper:Probe."]


def test_engine_table_covers_neural_registry(daemon):
    d = daemon("engine=piper\n")
    assert set(tts_neural.ENGINES) <= set(d.ENGINES)
    assert d.ENGINES["edge"].online and d.ENGINES["elevenlabs"].online
    assert "kokoro" not in d.ENGINES                       # kokoro-onnx engine was removed


def test_legacy_kokoro_maps_to_kokoro2(daemon):
    d = daemon("engine=kokoro\nkokoro_de=martin\nkokoro_en=af_bella\nkokoro2_en=bf_emma\n")
    assert d.ENGINE == "kokoro2" and d.ACTIVE is d.ENGINES["kokoro2"]
    assert d.NEURAL_CONF == {"de": "martin", "en": "bf_emma"}
    assert d.VOICE_MAP == {"de": "de"}                      # kokoro_* are no Piper voices
    assert d.run('{"text": "Hallo.", "voice": "de"}') == ["neural:Hallo."]
    assert ("stream", "kokoro2", "Hallo.", "martin", "de", "") in d.calls


def test_legacy_kokoro_without_kokoro2_uses_piper(daemon, monkeypatch):
    monkeypatch.setattr(tts_neural, "installed", lambda key: False)
    d = daemon("engine=kokoro\nkokoro_de=martin\n")
    assert d.ENGINE == "piper" and d.ACTIVE is None
    assert d.run('{"text": "Hallo.", "voice": "de"}') == ["piper:Hallo."]


def test_edge_ffmpeg_starts_without_console(daemon, monkeypatch):
    """Edge decodes through ffmpeg; on Windows that child must not get a
    console window (the daemon runs under pythonw)."""
    d = daemon("engine=piper\n")
    seen = {}

    def fake_popen(args, **kw):
        seen.update(kw, args=args)
        raise OSError("no ffmpeg in tests")

    monkeypatch.setattr(tts_platform, "IS_WINDOWS", True)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    with pytest.raises(OSError):
        next(d.edge_chunks("Hallo.", "de-DE-KatjaNeural", 1.0))
    assert seen["args"][0] == "ffmpeg"
    assert seen["creationflags"] & tts_platform.CREATE_NO_WINDOW
    assert seen["stdin"] == subprocess.PIPE                 # explicit stdin is kept


def test_complete_preview_is_cached(daemon, tmp_path, monkeypatch):
    import tts_previews
    monkeypatch.setenv("TTS_PREVIEW_DIR", str(tmp_path / "previews"))
    d = daemon("engine=piper\n")
    sample = tts_previews.sample("de")
    d.run(json.dumps({"cmd": "test", "engine": "kokoro2", "voice": "thorsten", "key": "de",
                      "text": sample, "length_scale": 1.0, "cache": True}))
    assert tts_previews.lookup("kokoro2", "thorsten", "de") is not None
    # custom text, style instructions and own voices always play live
    d.run(json.dumps({"cmd": "test", "engine": "kokoro2", "voice": "martin", "key": "de",
                      "text": "Probe.", "cache": True}))
    d.run(json.dumps({"cmd": "test", "engine": "kokoro2", "voice": "ref:anna", "key": "de",
                      "text": sample, "cache": True}))
    d.run(json.dumps({"cmd": "test", "engine": "kokoro2", "voice": "victoria", "key": "de",
                      "text": sample}))
    assert len(tts_previews.read_index()["items"]) == 1


def test_failed_preview_is_not_cached(daemon, tmp_path, monkeypatch):
    import tts_previews
    monkeypatch.setenv("TTS_PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setitem(tts_previews.SAMPLE, "de", "Erster Satz. Dann fail hier.")
    d = daemon("engine=piper\n")
    d.run(json.dumps({"cmd": "test", "engine": "kokoro2", "voice": "thorsten", "key": "de",
                      "text": tts_previews.sample("de"), "cache": True}))
    assert tts_previews.lookup("kokoro2", "thorsten", "de") is None


def _pcm_daemon(daemon, monkeypatch, conf):
    """Daemon whose neural fake returns real PCM (1 s, 24 kHz) and whose sink
    keeps raw bytes. Returns (module, played bytes, requested speeds)."""
    import numpy as np
    d = daemon(conf)
    t = np.arange(24000) / 24000
    pcm = (0.03 * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2").tobytes()
    speeds, raw = [], []

    def stream(key, text, voice="", lang="de", speed=1.0, instruct=""):
        speeds.append(speed)
        for i in range(0, len(pcm), 8192):
            yield pcm[i:i + 8192], 24000

    class Sink(_Sink):
        def write(self, b):
            raw.append(b)
            return True

    monkeypatch.setattr(tts_neural, "stream", stream)
    monkeypatch.setattr(tts_platform, "make_audio_sink", lambda rate, ch: Sink(raw))
    return d, raw, speeds


def test_engine_without_native_speed_is_stretched(daemon, monkeypatch):
    d, raw, speeds = _pcm_daemon(daemon, monkeypatch, "engine=chatterbox\n")
    assert not tts_neural.native_speed("chatterbox")
    d.run('{"text": "Hallo.", "voice": "de", "length_scale": 0.8}')
    assert speeds == [1.0]                                  # the model gets no speed
    assert len(b"".join(raw)) // 2 == round(24000 / 1.25)   # the daemon stretched it


def test_engine_with_native_speed_is_not_stretched(daemon, monkeypatch):
    d, raw, speeds = _pcm_daemon(daemon, monkeypatch, "engine=kokoro2\n")
    d.run('{"text": "Hallo.", "voice": "de", "length_scale": 0.8}')
    assert speeds == [pytest.approx(1.25)]
    assert len(b"".join(raw)) // 2 == 24000


def test_loudness_normalization_applies_to_engine_output(daemon, monkeypatch):
    import numpy as np

    import tts_audio
    d, raw, _ = _pcm_daemon(daemon, monkeypatch, "engine=kokoro2\nloudness_norm=on\n")
    assert d.LOUDNESS is not None and d.LOUDNESS.target == tts_audio.TARGET_LUFS
    d.run('{"text": "Hallo.", "voice": "de"}')
    y = tts_audio.to_float(b"".join(raw))
    assert len(y) == 24000
    assert tts_audio.integrated_loudness(y, 24000) == pytest.approx(-18.0, abs=1.5)
    assert np.abs(y).max() <= tts_audio.CEILING + 1 / 32768
    assert tts_audio.state_path().exists()                 # estimate kept for the next start


def test_speed_mapping_of_rate_engines(daemon):
    d = daemon("engine=piper\n")
    assert d._rate_steps(1 / 1.5) == 4                      # SAPI/balcon: 3 ** (r / 10)
    assert d._rate_pace(1.5) == pytest.approx(3 ** 0.4)
    assert d._rate_pace(1.0) == 1.0
    assert d.ENGINES["edge"].native(1.5) == 1.5
    assert d.ENGINES["edge"].native(3.0) == 2.0             # beyond Edge's range: stretched


def test_status_reports_playback_state(daemon, monkeypatch):
    d = daemon("engine=piper\n")
    assert d.status() == {"state": "idle"}
    assert d._is_status('{"cmd": "status"}') and not d._is_status('{"text": "status"}')
    gate = threading.Event()

    class _Blocking(_Sink):
        def write(self, pcm):
            gate.wait(5)
            return True

    monkeypatch.setattr(tts_platform, "make_audio_sink", lambda rate, ch: _Blocking([]))
    d.handle('{"text": "Eins.", "voice": "de"}')
    for _ in range(100):                    # wait until the sink is open
        if d._current.sink is not None:
            break
        time.sleep(0.02)
    assert d.status() == {"state": "speaking"}
    d.handle('{"cmd": "pause"}')
    assert d.status() == {"state": "paused"}
    gate.set()
    d._current.thread.join(5)
    assert d.status() == {"state": "idle"}
