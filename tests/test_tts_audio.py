"""bin/tts_audio.py with synthetic signals: loudness meter, leveler, limiter,
time stretch. numpy only; pyloudnorm is used for a cross-check if installed."""
import math

import numpy as np
import pytest

import tts_audio

SR = 24000
RNG = np.random.default_rng(7)


def sine(seconds, freq=997.0, amp=1.0, sr=SR):
    t = np.arange(int(seconds * sr)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def bursts(seconds, amp, sr=SR):
    """Speech-like: 0.3 s noise bursts with 0.2 s pauses."""
    unit = np.concatenate([RNG.normal(0, amp, int(0.3 * sr)), np.zeros(int(0.2 * sr))])
    return np.tile(unit, int(math.ceil(seconds / 0.5)))[:int(seconds * sr)].astype(np.float32)


def chunks(x, sizes=(4096,)):
    i, k = 0, 0
    while i < len(x):
        n = sizes[k % len(sizes)]
        yield x[i:i + n]
        i, k = i + n, k + 1


def stream(stage, x, sizes=(4096,)):
    return np.concatenate([stage(c) for c in chunks(x, sizes)])


# --- loudness meter ---------------------------------------------------------------
@pytest.mark.parametrize("sr", [48000, 24000, 22050])
def test_full_scale_997hz_sine_is_minus_3_lufs(sr):
    # BS.1770 reference: a 0 dBFS 997 Hz sine reads -3.01 LUFS
    assert tts_audio.integrated_loudness(sine(3, sr=sr), sr) == pytest.approx(-3.01, abs=0.1)
    assert tts_audio.integrated_loudness(sine(3, amp=0.1, sr=sr), sr) == \
        pytest.approx(-23.01, abs=0.1)


def test_silence_is_gated():
    tone = sine(8, amp=0.1)
    padded = np.concatenate([np.zeros(3 * SR, np.float32), tone, np.zeros(3 * SR, np.float32)])
    assert tts_audio.integrated_loudness(padded, SR) == \
        pytest.approx(tts_audio.integrated_loudness(tone, SR), abs=0.3)
    assert tts_audio.integrated_loudness(np.zeros(SR, np.float32), SR) == -math.inf


def test_meter_matches_pyloudnorm():
    pyln = pytest.importorskip("pyloudnorm")
    x = bursts(6, 0.05)
    ref = pyln.Meter(SR).integrated_loudness(x.astype(np.float64))
    assert tts_audio.integrated_loudness(x, SR) == pytest.approx(ref, abs=0.3)


# --- leveler --------------------------------------------------------------------
def test_leveler_brings_quiet_and_loud_voices_to_target():
    lev = tts_audio.Leveler(-18.0)
    quiet, loud = bursts(8, 0.03), bursts(8, 0.25)       # about -31 and -13 LUFS
    out = {}
    for _ in range(2):                                   # second round: warm estimates
        out["q"] = stream(lambda c: lev.process("a|de|q", c, SR), quiet)
        out["l"] = stream(lambda c: lev.process("b|en|l", c, SR), loud)
    for y in out.values():
        assert tts_audio.integrated_loudness(y, SR) == pytest.approx(-18.0, abs=1.0)


def test_leveler_first_chunk_already_close():
    lev = tts_audio.Leveler(-18.0)
    x = bursts(3, 0.02)
    y = lev.process("new|de|v", x[:4096], SR)            # first chunk of an unknown voice
    gain_db = 20 * math.log10(np.abs(y).max() / np.abs(x[:4096]).max())
    expect = -18.0 - tts_audio.integrated_loudness(x, SR)
    assert gain_db == pytest.approx(expect, abs=3.0)


def test_leveler_keeps_dynamics_within_a_voice():
    """Loud and soft syllables of one voice keep their difference: the gain is
    slow (no pumping)."""
    lev = tts_audio.Leveler(-18.0)
    lev.process("v", bursts(10, 0.05), SR)               # warm up
    soft, strong = bursts(1, 0.02), bursts(1, 0.08)       # 12 dB apart
    gains = []
    for part in (soft, strong, soft, strong):
        for c in chunks(part):
            y = lev.process("v", c, SR)
            nz = np.abs(c) > 1e-4
            if not nz.any():
                continue
            gains.append(20 * np.log10(np.median(np.abs(y[nz]) / np.abs(c[nz]))))
    assert max(gains) - min(gains) < 1.0


def test_leveler_voice_switch_ramps_without_step():
    lev = tts_audio.Leveler(-18.0, ramp_s=0.03)
    lev.process("a", bursts(4, 0.2), SR)                 # loud voice: gain < 1
    lev.process("b", bursts(4, 0.01), SR)                # quiet voice: gain > 1
    x = np.full(4800, 0.01, np.float32)                  # constant input shows the gain
    ga = lev.process("a", x, SR)[-1] / 0.01
    g = lev.process("b", x, SR) / 0.01
    gb, ramp = g[-1], int(0.03 * SR)
    assert gb > 5 * ga
    assert g[0] == pytest.approx(ga, rel=0.05)                  # starts where "a" ended
    assert np.abs(np.diff(g)).max() <= 1.5 * (gb - ga) / ramp   # a ramp, not a step
    assert g[ramp] == pytest.approx(gb, rel=1e-3)               # done after ramp_s


def test_leveler_persists_estimates(tmp_path):
    p = tmp_path / "loudness.json"
    lev = tts_audio.Leveler(-18.0, path=p)
    lev.process("edge|de|katja", bursts(5, 0.05), SR)
    lev.save()
    again = tts_audio.Leveler(-18.0, path=p)
    assert again.level("edge|de|katja") == pytest.approx(lev.level("edge|de|katja"), abs=1e-6)


# --- limiter ----------------------------------------------------------------------
def test_limiter_never_exceeds_ceiling():
    lim = tts_audio.Limiter(SR)
    x = (RNG.normal(0, 1.0, 5 * SR) * np.repeat(RNG.uniform(0.1, 4.0, 50), SR // 10)).astype(np.float32)
    y = stream(lim.process, x, sizes=(97, 4096, 1000, 13))
    assert len(y) == len(x)
    assert np.abs(y).max() <= tts_audio.CEILING + 1e-6


def test_limiter_leaves_quiet_audio_alone():
    lim = tts_audio.Limiter(SR)
    x = sine(1, amp=0.5)
    assert np.array_equal(stream(lim.process, x), x)


def test_limiter_recovers_after_a_peak():
    lim = tts_audio.Limiter(SR, release_s=0.08)
    x = np.concatenate([sine(0.05, amp=3.0), sine(1.0, amp=0.5)])
    y = stream(lim.process, x, sizes=(480,))
    assert np.abs(y[-SR // 4:]).max() == pytest.approx(0.5, abs=0.01)   # back to unity


# --- time stretch -----------------------------------------------------------------
@pytest.mark.parametrize("rate", [0.8, 1.25, 1.5, 2.0])
def test_stretch_duration(rate):
    x = bursts(3, 0.1)
    st = tts_audio.Stretch(rate, SR)
    y = np.concatenate([stream(st.process, x), st.flush()])
    assert len(y) == round(len(x) / rate)


@pytest.mark.parametrize("rate", [1.25, 1.5])
def test_stretch_keeps_pitch(rate):
    x = sine(2, freq=220.0, amp=0.5)
    st = tts_audio.Stretch(rate, SR)
    y = np.concatenate([stream(st.process, x), st.flush()])
    mid = y[SR // 4: -SR // 4]
    spec = np.abs(np.fft.rfft(mid * np.hanning(len(mid))))
    peak = np.argmax(spec) * SR / len(mid)
    assert peak == pytest.approx(220.0, rel=0.02)


def test_stretch_does_not_depend_on_chunking():
    x = bursts(2, 0.1)
    a = tts_audio.Stretch(1.5, SR)
    one = np.concatenate([a.process(x), a.flush()])
    b = tts_audio.Stretch(1.5, SR)
    many = np.concatenate([stream(b.process, x, sizes=(1, 333, 4096, 7, 2000)), b.flush()])
    assert np.allclose(one, many, atol=1e-6)


def test_stretch_has_no_discontinuity_at_chunk_joins():
    x = sine(2, freq=200.0, amp=0.5)
    st = tts_audio.Stretch(1.5, SR)
    y = np.concatenate([stream(st.process, x, sizes=(1000, 4096, 517)), st.flush()])
    max_slope = 2 * np.pi * 200.0 / SR * 0.5
    body = y[SR // 20: -SR // 20]
    assert np.abs(np.diff(body)).max() < 1.5 * max_slope
    assert np.abs(body).max() == pytest.approx(0.5, abs=0.05)          # no comb dips or bumps


# --- chain ------------------------------------------------------------------------
def test_chain_passes_through_without_processing():
    ch = tts_audio.Chain(None)
    pcm = b"\x01\x02\x03"
    assert ch.process(pcm, SR, "k", 1.0) is pcm
    assert ch.end_unit() == b""


def test_chain_stretch_and_level():
    ch = tts_audio.Chain(tts_audio.Leveler(-18.0))
    x = bursts(4, 0.01)
    pcm = tts_audio.to_pcm(x)
    out = b"".join(ch.process(pcm[i:i + 8192], SR, "cb|en|v", 1.5)
                   for i in range(0, len(pcm), 8192)) + ch.end_unit()
    y = tts_audio.to_float(out)
    assert len(y) == round(len(x) / 1.5)
    assert np.abs(y).max() <= tts_audio.CEILING + 1 / 32768
    assert tts_audio.integrated_loudness(y, SR) > tts_audio.integrated_loudness(x, SR) + 10


def test_normalize_pcm():
    pcm = tts_audio.to_pcm(bursts(3, 0.03))
    y = tts_audio.to_float(tts_audio.normalize_pcm(pcm, SR, -18.0))
    assert tts_audio.integrated_loudness(y, SR) == pytest.approx(-18.0, abs=0.5)
    silent = b"\x00\x00" * 100
    assert tts_audio.normalize_pcm(silent, SR) == silent
