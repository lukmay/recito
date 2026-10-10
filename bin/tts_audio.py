"""
tts_audio: output processing of the daemon, the same for every engine.

  Stretch    pitch-preserving time stretch (WSOLA) for engines without
             native speed control, streaming with state across chunks
  Leveler    loudness normalization to a LUFS target, with one running
             loudness estimate per (engine, voice)
  Limiter    peak limiter with a -1 dBFS ceiling
  Chain      the three in a row, fed with s16le chunks by the daemon

  integrated_loudness(x, sr)   ITU-R BS.1770-4 integrated loudness (LUFS)

Loudness is measured with the BS.1770 K-weighting, applied as a power
weighting in the frequency domain per 100 ms segment, then gated like
BS.1770 (400 ms blocks with 75 % overlap, -70 LUFS absolute gate, -10 LU
relative gate). numpy only.
"""

import json
import math
import os
import threading
from pathlib import Path

import numpy as np

TARGET_LUFS = -18.0
CEILING = 10 ** (-1.0 / 20)          # limiter ceiling, -1 dBFS
SEG_S = 0.1                          # loudness segment; 4 segments = one 400 ms block
ABS_GATE = -70.0
REL_GATE = -10.0


# --- K-weighting --------------------------------------------------------------
def _biquads(sr):
    """BS.1770 pre-filter (high shelf) and RLB high-pass for any sample rate."""
    out = []
    for kind, gain_db, q, fc in (("shelf", 4.0, 1 / math.sqrt(2), 1500.0),
                                 ("hp", 0.0, 0.5, 38.0)):
        a_ = 10 ** (gain_db / 40)
        w0 = 2 * math.pi * fc / sr
        c, alpha = math.cos(w0), math.sin(w0) / (2 * q)
        if kind == "shelf":
            s = 2 * math.sqrt(a_) * alpha
            b = [a_ * ((a_ + 1) + (a_ - 1) * c + s), -2 * a_ * ((a_ - 1) + (a_ + 1) * c),
                 a_ * ((a_ + 1) + (a_ - 1) * c - s)]
            a = [(a_ + 1) - (a_ - 1) * c + s, 2 * ((a_ - 1) - (a_ + 1) * c),
                 (a_ + 1) - (a_ - 1) * c - s]
        else:
            b = [(1 + c) / 2, -(1 + c), (1 + c) / 2]
            a = [1 + alpha, -2 * c, 1 - alpha]
        out.append((np.array(b) / a[0], np.array(a) / a[0]))
    return out


_KW = {}


def _k_weights(n, sr):
    """Per rfft bin: |H_K|^2 times the one-sided spectrum factor, scaled so
    that sum(weights * |X|^2) is the mean square of the K-weighted frame."""
    key = (n, sr)
    if key not in _KW:
        z = np.exp(-1j * np.pi * np.arange(n // 2 + 1) / (n / 2))
        p = np.ones(len(z))
        for b, a in _biquads(sr):
            num = b[0] + b[1] * z + b[2] * z * z
            den = a[0] + a[1] * z + a[2] * z * z
            p *= np.abs(num / den) ** 2
        side = np.full(len(z), 2.0)
        side[0] = 1.0
        if n % 2 == 0:
            side[-1] = 1.0
        _KW[key] = p * side / (n * n)
    return _KW[key]


def segment_powers(x, sr, seg=None):
    """Mean square of the K-weighted signal per full segment (default 100 ms)."""
    seg = seg or int(round(SEG_S * sr))
    n = len(x) // seg
    if n == 0:
        return np.zeros(0)
    spec = np.fft.rfft(np.asarray(x[:n * seg], dtype=np.float64).reshape(n, seg), axis=1)
    return (np.abs(spec) ** 2) @ _k_weights(seg, sr)


def _lufs(power):
    return -0.691 + 10 * math.log10(power) if power > 0 else -math.inf


def integrated_loudness(x, sr):
    """BS.1770-4 integrated loudness of a whole signal (float, -1..1), LUFS."""
    p = segment_powers(x, sr)
    if len(p) == 0:
        return -math.inf
    blocks = np.convolve(p, np.ones(4) / 4, "valid") if len(p) >= 4 else np.array([p.mean()])
    lufs = -0.691 + 10 * np.log10(np.maximum(blocks, 1e-20))
    kept = blocks[lufs > ABS_GATE]
    if len(kept) == 0:
        return -math.inf
    rel = _lufs(kept.mean()) + REL_GATE
    kept = blocks[(lufs > ABS_GATE) & (lufs > rel)]
    return _lufs(kept.mean())


# --- Loudness normalization ----------------------------------------------------
class _Level:
    """Running loudness of one (engine, voice): exponentially weighted mean
    power of the gated 400 ms blocks."""
    __slots__ = ("energy", "weight", "recent", "rest")

    def __init__(self, energy=0.0, weight=0.0):
        self.energy = energy
        self.weight = weight
        self.recent = []           # last 3 segment powers, for overlapping blocks
        self.rest = np.zeros(0, np.float32)

    def lufs(self):
        return _lufs(self.energy / self.weight) if self.weight > 0 else None


class Leveler:
    """Loudness normalization for streamed audio.

    Each chunk first updates the loudness estimate of its (engine, voice),
    then gets the gain target - estimate. The estimate averages over the last
    `tau_s` seconds of speech, so the gain is nearly constant within a voice:
    no pumping. A voice heard for the first time is measured from its first
    chunk; while its estimate is younger than `young_s`, the gain follows the
    estimate freely, later at most `slew_db_s` dB per second. A switch to
    another voice ramps the gain over `ramp_s`. Estimates are kept in `path`
    (JSON), so a known voice starts at the right level.

    Gain is limited to +max_boost / -max_cut dB. Thread-safe."""

    def __init__(self, target=TARGET_LUFS, path=None, tau_s=30.0, young_s=2.0,
                 slew_db_s=3.0, ramp_s=0.03, max_boost=15.0, max_cut=20.0):
        self.target = float(target)
        self.path = Path(path) if path else None
        self.tau_s, self.young_s = tau_s, young_s
        self.slew_db_s, self.ramp_s = slew_db_s, ramp_s
        self.max_boost, self.max_cut = max_boost, max_cut
        self.levels = {}
        self.gain_db = 0.0          # gain at the end of the last chunk
        self.key = None
        self._lock = threading.Lock()
        self._dirty = False
        self._load()

    def _load(self):
        if not self.path:
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for k, (e, w) in raw.items():
                if e > 0 and w > 0:
                    self.levels[k] = _Level(float(e), float(w))
        except Exception:
            pass

    def save(self):
        """Write the estimates if they changed. Errors are ignored."""
        if not self.path or not self._dirty:
            return
        with self._lock:
            data = {k: [s.energy, s.weight] for k, s in self.levels.items() if s.weight > 0}
            self._dirty = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception:
            pass

    def level(self, key):
        s = self.levels.get(key)
        return s.lufs() if s else None

    def _measure(self, st, x, sr):
        seg = int(round(SEG_S * sr))
        buf = np.concatenate([st.rest, x]) if len(st.rest) else x
        n = len(buf) // seg
        st.rest = buf[n * seg:].copy()
        decay = math.exp(-SEG_S / self.tau_s)
        for p in segment_powers(buf[:n * seg], sr, seg):
            st.recent.append(p)
            if len(st.recent) < 4:
                continue
            block = sum(st.recent) / 4
            del st.recent[0]
            lufs = _lufs(block)
            if lufs <= ABS_GATE:
                continue
            cur = st.lufs()
            if cur is not None and lufs < cur + REL_GATE:
                continue
            st.energy = st.energy * decay + block
            st.weight = st.weight * decay + 1.0
            self._dirty = True

    def _provisional(self, st, x, sr):
        """Estimate from a voice's first audio, before a full block exists."""
        p = segment_powers(x, sr, len(x))
        if len(p) and _lufs(p[0]) > ABS_GATE:
            return _lufs(p[0])
        return None

    def process(self, key, x, sr):
        """Normalize one float32 chunk of voice `key`; returns a new array."""
        if len(x) == 0:
            return x
        with self._lock:
            st = self.levels.get(key)
            first = st is None or st.weight == 0
            if st is None:
                st = self.levels[key] = _Level()
            self._measure(st, x, sr)
            cur = st.lufs()
            if cur is None and first:
                cur = self._provisional(st, x, sr)
            g0 = self.gain_db
            if cur is None:
                g1 = g0 if key == self.key else 0.0
            else:
                g1 = min(self.max_boost, max(-self.max_cut, self.target - cur))
            young = st.weight * SEG_S < self.young_s
            switch = key != self.key
            if not switch and not young:
                step = self.slew_db_s * len(x) / sr
                g1 = min(g0 + step, max(g0 - step, g1))
            self.key, self.gain_db = key, g1
        a0, a1 = 10 ** (g0 / 20), 10 ** (g1 / 20)
        if a0 == a1:
            return (x * a1).astype(np.float32)
        n = len(x) if not switch else min(len(x), max(1, int(self.ramp_s * sr)))
        g = np.full(len(x), a1, np.float32)
        g[:n] = np.linspace(a0, a1, n, endpoint=False, dtype=np.float32)
        return x * g


# --- Peak limiter ----------------------------------------------------------------
class Limiter:
    """Block-wise peak limiter, no added latency.

    Gain knots sit at 5 ms block borders. A knot is at most the gain that
    keeps the blocks on both sides under the ceiling, and rises after a peak
    by at most exp(block / release_s) per block. The gain is interpolated
    linearly between knots, so it never exceeds what a block needs (look-ahead
    of one block inside a chunk). A hard clip at the ceiling covers a peak
    right at the start of a chunk."""

    def __init__(self, sr, ceiling=CEILING, block_s=0.005, release_s=0.08):
        self.ceiling = ceiling
        self.block = max(1, int(round(block_s * sr)))
        self.rise = math.exp(block_s / release_s)
        self.knot = 1.0

    def process(self, x):
        n = len(x)
        if n == 0:
            return x
        b = self.block
        nb = -(-n // b)
        pad = np.zeros(nb * b, np.float32)
        pad[:n] = np.abs(x)
        peaks = pad.reshape(nb, b).max(1)
        req = np.minimum(1.0, self.ceiling / np.maximum(peaks, 1e-9))
        if req.min() >= 1.0 and self.knot >= 1.0:
            return np.clip(x, -self.ceiling, self.ceiling)
        knots = np.empty(nb + 1)
        knots[0] = self.knot
        for i in range(1, nb + 1):
            lim = req[i - 1] if i == nb else min(req[i - 1], req[i])
            knots[i] = min(lim, knots[i - 1] * self.rise, 1.0)
        self.knot = knots[nb]
        t = np.arange(nb * b, dtype=np.float32) / b
        g = np.interp(t, np.arange(nb + 1), knots).astype(np.float32)[:n]
        return np.clip(x * g, -self.ceiling, self.ceiling)


# --- Time stretch ----------------------------------------------------------------
class Stretch:
    """Pitch-preserving time stretch by WSOLA, streamed.

    rate > 1 is faster (rate 1.5 gives 1/1.5 of the duration). Frames of
    `frame_s` with a Hann window are overlap-added at half a frame hop; each
    analysis frame is moved by up to `tol_s` to the position that best
    continues the previous frame (normalized cross-correlation), which keeps
    the waveform continuous and the pitch unchanged.

    process() takes any chunk sizes and returns what is final; flush() ends
    the stream. The output does not depend on how the input was chunked, and
    its length is round(input / rate)."""

    def __init__(self, rate, sr, frame_s=0.03, tol_s=0.012):
        self.rate = float(rate)
        n = 2 * int(round(frame_s * sr / 2))
        self.n, self.hs = n, n // 2
        self.ha = self.hs * self.rate
        self.tol = int(round(tol_s * sr))
        self.win = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / n)).astype(np.float32)
        self.buf = np.zeros(self.hs, np.float32)      # leading half frame of zeros
        self.base = 0                                  # input index of buf[0]
        self.k = 0
        self.prev = 0
        self.ola = np.zeros(n, np.float32)
        self.n_in = 0
        self.n_out = 0
        self.skip = self.hs                            # output of the zero padding
        nfft = 1
        while nfft < 2 * n + 2 * self.tol:
            nfft *= 2
        self.nfft = nfft

    def _need(self, k):
        a = int(round(k * self.ha))
        return max(a + self.tol + self.n, self.prev + self.hs + self.n)

    def _frame(self):
        k = self.k
        a = int(round(k * self.ha))
        if k == 0:
            pos = 0
        else:
            lo = max(0, a - self.tol)
            hi = a + self.tol
            tpl = self._get(self.prev + self.hs, self.n)
            reg = self._get(lo, hi - lo + self.n)
            f = self.nfft
            corr = np.fft.irfft(np.fft.rfft(reg, f) * np.conj(np.fft.rfft(tpl, f)), f)
            corr = corr[:hi - lo + 1]
            e = np.cumsum(np.concatenate([[0.0], reg.astype(np.float64) ** 2]))
            energy = e[self.n:self.n + len(corr)] - e[:len(corr)]
            pos = lo + int(np.argmax(corr / np.sqrt(energy + 1e-9)))
        self.ola += self._get(pos, self.n) * self.win
        out = self.ola[:self.hs].copy()
        self.ola[:-self.hs] = self.ola[self.hs:]
        self.ola[-self.hs:] = 0
        self.prev = pos
        self.k += 1
        drop = min(len(self.buf), max(0, min(pos + self.hs, int(round(self.k * self.ha))
                                             - self.tol) - self.base))
        if drop > 4 * self.n:
            self.buf = self.buf[drop:]
            self.base += drop
        return out

    def _get(self, start, length):
        i = start - self.base
        seg = self.buf[i:i + length]
        if len(seg) < length:
            seg = np.concatenate([seg, np.zeros(length - len(seg), np.float32)])
        return seg

    def _emit(self, parts, limit=None):
        out = np.concatenate(parts) if parts else np.zeros(0, np.float32)
        if self.skip:
            cut = min(self.skip, len(out))
            out, self.skip = out[cut:], self.skip - cut
        if limit is not None:
            out = out[:max(0, limit - self.n_out)]
        self.n_out += len(out)
        return out

    def process(self, x):
        x = np.asarray(x, np.float32)
        self.n_in += len(x)
        self.buf = np.concatenate([self.buf, x])
        end = self.base + len(self.buf)
        parts = []
        while self._need(self.k) <= end:
            parts.append(self._frame())
        return self._emit(parts)

    def flush(self):
        total = int(round(self.n_in / self.rate))
        parts = []
        emitted = self.n_out
        while emitted + sum(len(p) for p in parts) - self.skip < total:
            parts.append(self._frame())
        return self._emit(parts, total)


# --- Chain used by the daemon ---------------------------------------------------
def state_path():
    """Where the loudness estimates are kept (a cache, safe to delete)."""
    if os.environ.get("TTS_AUDIO_STATE"):
        return Path(os.environ["TTS_AUDIO_STATE"])
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData/Local")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "tts-audio" / "loudness.json"


def to_float(pcm):
    return np.frombuffer(pcm[:len(pcm) & ~1], "<i2").astype(np.float32) / 32768.0


def to_pcm(x):
    return (np.clip(x, -1.0, 32767 / 32768) * 32768.0).round().astype("<i2").tobytes()


def normalize_pcm(pcm, sr, target=TARGET_LUFS, max_boost=15.0, max_cut=20.0):
    """Whole s16le mono clip to `target` LUFS plus limiter (cached previews)."""
    x = to_float(pcm)
    lufs = integrated_loudness(x, sr)
    if not math.isfinite(lufs):
        return pcm
    gain = min(max_boost, max(-max_cut, target - lufs))
    return to_pcm(Limiter(sr).process(x * np.float32(10 ** (gain / 20))))


class Chain:
    """Per job: stretch (per unit), loudness, limiter on s16le mono chunks.

    process(pcm, sr, key, tempo) -> bytes; end_unit(sr) -> bytes flushes the
    stretch at the end of a unit. leveler=None skips loudness and limiter."""

    def __init__(self, leveler=None):
        self.leveler = leveler
        self.limiter = None
        self.stretch = None
        self.sr = None
        self.key = None

    def _post(self, x, sr, key):
        if self.leveler is not None:
            x = self.leveler.process(key, x, sr)
            if self.limiter is None or self.sr != sr:
                self.limiter = Limiter(sr)
            x = self.limiter.process(x)
        self.sr = sr
        return x

    def active(self, tempo=1.0):
        """False when process() would return the input unchanged."""
        return self.leveler is not None or abs(tempo - 1.0) >= 0.01 or self.stretch is not None

    def process(self, pcm, sr, key, tempo=1.0):
        if not self.active(tempo):
            return pcm
        x = to_float(pcm)
        out = b""
        if self.stretch is not None and self.sr != sr:
            self.stretch = None                  # source changed format: drop the tail
        elif self.stretch is not None and (self.stretch.rate != tempo or self.key != key):
            out = self.end_unit()
        self.key = key
        if abs(tempo - 1.0) >= 0.01:
            if self.stretch is None:
                self.stretch = Stretch(tempo, sr)
            x = self.stretch.process(x)
        return out + to_pcm(self._post(x, sr, key))

    def end_unit(self):
        st, self.stretch = self.stretch, None
        if st is None or self.sr is None:
            return b""
        return to_pcm(self._post(st.flush(), self.sr, self.key))
