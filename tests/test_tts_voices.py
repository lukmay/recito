"""bin/tts_voices.py: preparation, checks, WAV I/O, ids and voice storage.
All files go to the temporary TTS_NEURAL_DIR."""
import json
import os
import time
import wave

import numpy as np
import pytest

import tts_voices

SR = tts_voices.SR


def tone(seconds, amp=0.3, freq=220.0):
    t = np.arange(int(seconds * SR)) / SR
    # Syllable-like amplitude modulation, so the level varies like speech.
    env = 0.6 + 0.4 * np.sin(2 * np.pi * 3 * t)
    return (amp * env * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds):
    return np.zeros(int(seconds * SR), dtype=np.float32)


def speech(seconds, amp=0.3):
    """Speech-like signal: 0.4 s of sound, 0.25 s pause, repeated."""
    unit = np.concatenate([tone(0.4, amp), silence(0.25)])
    reps = int(np.ceil(seconds * SR / len(unit)))
    return np.tile(unit, reps)[: int(seconds * SR)]


def rms_db(y):
    return 20 * np.log10(np.sqrt((y ** 2).mean()))


def test_voices_dir_follows_env(isolated_dirs):
    assert tts_voices.voices_dir() == isolated_dirs["TTS_NEURAL_DIR"] / "voices"


@pytest.mark.parametrize("name, expected", [
    ("Meine Stimme", "meine_stimme"),
    ("Größe Ärger Öl", "grosse_arger_ol"),
    ("  --  ", "voice"),
    ("x" * 60, "x" * 40),
])
def test_slug(name, expected):
    assert tts_voices.slug(name) == expected


def test_wav_roundtrip(tmp_path):
    y = np.array([0.0, 0.5, -0.5, 1.5], dtype=np.float32)
    p = tmp_path / "a.wav"
    tts_voices.write_wav(p, y)
    x, sr = tts_voices.read_wav(p)
    assert sr == SR
    assert np.allclose(x, [0.0, 0.5, -0.5, 1.0], atol=1e-4)


def test_read_wav_stereo_and_width(tmp_path):
    p = tmp_path / "st.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(np.array([1000, 3000, -2000, 0], dtype="<i2").tobytes())
    x, sr = tts_voices.read_wav(p)
    assert sr == 8000
    assert np.allclose(x * 32768, [2000, -1000])

    p8 = tmp_path / "u8.wav"
    with wave.open(str(p8), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(8000)
        w.writeframes(b"\x80\x90")
    with pytest.raises(RuntimeError):
        tts_voices.read_wav(p8)


def test_resample_length():
    x = np.ones(48000, dtype=np.float32)
    assert abs(len(tts_voices.resample(x, 48000)) - SR) <= 1
    assert len(tts_voices.resample(x, SR)) == 48000


def test_prepare_trims_and_normalizes():
    raw = np.concatenate([silence(1.0), speech(3.0), silence(2.0), speech(2.0), silence(1.0)])
    y = tts_voices.prepare(raw)
    assert len(y) < len(raw) - 2.5 * SR             # edges trimmed, long pause shortened
    assert len(y) > 4.5 * SR                        # speech kept
    assert np.abs(y).max() <= 0.97 + 1e-6
    assert abs(rms_db(y) + 20) < 0.5


def test_prepare_caps_length():
    y = tts_voices.prepare(speech(20.0))
    assert 10 * SR < len(y) <= tts_voices.MAX_REF_S * SR


@pytest.mark.parametrize("raw", [np.zeros(0, dtype=np.float32), silence(2.0)])
def test_prepare_rejects_empty_or_silent(raw):
    with pytest.raises(RuntimeError):
        tts_voices.prepare(raw)


def notes_for(raw):
    return tts_voices.check(raw, tts_voices.prepare(raw))


def test_check_good_recording():
    assert notes_for(speech(12.0)) == (True, [])


@pytest.mark.parametrize("raw, ok", [
    (speech(3.0), False),                               # too short
    (speech(6.5), True),                                # short but usable
    (speech(12.0, amp=0.01), True),                     # very quiet
    (np.clip(speech(12.0, amp=3.0), -1, 1), True),      # clipped
])
def test_check_single_note(raw, ok):
    got_ok, notes = notes_for(raw)
    assert got_ok is ok and len(notes) == 1


def test_check_noise():
    rng = np.random.default_rng(0)
    raw = speech(12.0) + rng.normal(0, 0.1, int(12 * SR)).astype(np.float32)
    ok, notes = notes_for(raw)
    assert ok and len(notes) == 1


def test_save_list_delete(isolated_dirs, write_conf):
    vid = tts_voices.save("Meine Stimme", "de", tone(10.0), "mic")
    assert vid == "meine_stimme"
    assert tts_voices.save("Meine Stimme", "de", tone(10.0), "file") == "meine_stimme_2"

    d = isolated_dirs["TTS_NEURAL_DIR"] / "voices"
    meta = json.loads((d / "meine_stimme.json").read_text(encoding="utf-8"))
    assert meta["name"] == "Meine Stimme" and meta["lang"] == "de" and meta["source"] == "mic"
    assert meta["seconds"] == 10.0

    tts_voices.write_wav(d / "en_male_1.wav", tone(1.0))      # shipped voice, no .json
    voices = {v["id"]: v for v in tts_voices.list_voices()}
    assert set(voices) == {"meine_stimme", "meine_stimme_2", "en_male_1"}
    assert voices["meine_stimme"]["own"] is True and voices["meine_stimme"]["seconds"] == 10.0
    assert voices["en_male_1"] == {"id": "en_male_1", "name": "en_male_1", "lang": "en",
                                   "seconds": 1.0, "source": "", "created": "", "own": False}

    write_conf("engine=chatterbox\nchatterbox_de=ref:meine_stimme\nchatterbox_en=ref:other\n")
    (d / "meine_stimme.txt").write_text("Hallo", encoding="utf-8")
    assert tts_voices.delete("meine_stimme") == ["chatterbox_de"]
    assert not any(d.glob("meine_stimme.*"))
    assert {v["id"] for v in tts_voices.list_voices()} == {"meine_stimme_2", "en_male_1"}


def test_list_voices_empty():
    assert tts_voices.list_voices() == []


def test_tmp_dir_removes_old_recordings(isolated_dirs):
    d = tts_voices.tmp_dir()
    assert d == isolated_dirs["TTS_NEURAL_DIR"] / "tmp"
    old, new = d / "aufnahme_1.wav", d / "aufnahme_2.wav"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    past = time.time() - tts_voices.TMP_MAX_AGE_S - 10
    os.utime(old, (past, past))
    tts_voices.tmp_dir()
    assert not old.exists() and new.exists()


def test_decode_file_wav_without_ffmpeg(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: None)
    p = tmp_path / "in.wav"
    tts_voices.write_wav(p, np.zeros(48000, dtype=np.float32), sr=48000)     # 1 s at 48 kHz
    x = tts_voices.decode_file(p)
    assert abs(len(x) - SR) <= 1
    with pytest.raises(RuntimeError):
        tts_voices.decode_file(tmp_path / "in.mp3")
