"""
tts_timing: which word of a job is being heard right now, for the follow
window (tts_follow).

The engines return audio without word times, so a word's time is estimated
inside its playback unit. The voiced part of the unit (leading and trailing
silence cut off) is spread over its words by weight: the characters of the
word, digits counted double because they are spoken as longer words, plus a
pause after punctuation. Units are a sentence or a clause, so the estimate
stays close to the voice.

The producer of a job reports each unit's audio (UnitAudio.add), the player
reports where in the sink a unit starts (Tracker.mark), and Tracker.current
turns the sink's play position into a character span of the job's text.
"""

import threading

import numpy as np

# |sample| above this counts as voice (about -38 dBFS)
SILENCE = 400
# Extra weight for the pause after a word that ends with this mark
_PAUSE = {",": 3, ";": 4, ":": 4, "–": 3, "—": 3, ".": 5, "!": 5, "?": 5, "…": 5}
# Spoken characters per second at pace 1.0, until a unit has been measured
_CPS = 14.0


def word_spans(text: str, units) -> list:
    """Character spans of the words of each unit in `text`, one list per
    unit. Units come from splitting `text` at whitespace, so their words
    appear in `text` in order; a word that cannot be found is skipped."""
    out, pos = [], 0
    for unit in units:
        spans = []
        for word in unit.split():
            i = text.find(word, pos)
            if i < 0:
                continue
            spans.append((i, i + len(word)))
            pos = i + len(word)
        out.append(spans)
    return out


def word_weights(text: str, spans) -> list:
    weights = []
    for n, (s, e) in enumerate(spans):
        word = text[s:e]
        w = 1 + sum(2 if c.isdigit() else 1 for c in word)
        if n + 1 < len(spans):           # the pause after the last word is silence
            w += _PAUSE.get(word.rstrip("\"')]»“”")[-1:], 0)
        weights.append(w)
    return weights


def pick(weights, f: float) -> int:
    """Index of the word at fraction f (0..1) of the unit's voiced audio."""
    goal = f * sum(weights)
    acc = 0
    for i, w in enumerate(weights):
        acc += w
        if goal < acc:
            return i
    return len(weights) - 1


class UnitAudio:
    """Byte count and voiced range of one unit's audio, filled as its chunks
    pass through the producer."""

    def __init__(self):
        self.bytes = 0
        self.first = None                # byte offset of the first voiced sample
        self.last = None                 # byte offset after the last voiced sample
        self.bps = 0                     # bytes per second of the unit's format

    def add(self, pcm: bytes, rate: int, channels: int):
        self.bps = rate * channels * 2
        n = len(pcm) - len(pcm) % 2
        if n:
            x = np.frombuffer(pcm[:n], dtype=np.int16)
            loud = np.flatnonzero((x > SILENCE) | (x < -SILENCE))
            if loud.size:
                if self.first is None:
                    self.first = self.bytes + int(loud[0]) * 2
                self.last = self.bytes + (int(loud[-1]) + 1) * 2
        self.bytes += len(pcm)


class Tracker:
    """Timeline of one job: the words of every unit, the audio the producer
    made for it and where the player started it in which sink."""

    def __init__(self, text: str, units, pace: float = 1.0):
        self.text = text
        self.units = list(units)
        self.spans = word_spans(text, self.units)
        self.weights = [word_weights(text, s) for s in self.spans]
        self.audio = {}                  # unit -> UnitAudio
        self.done = set()                # units whose audio is complete
        self.marks = []                  # (unit, sink, start byte) in playback order
        self.cps = _CPS * (pace or 1.0)
        self._lock = threading.Lock()

    # --- producer ----------------------------------------------------------- #
    def unit_audio(self, i: int) -> UnitAudio:
        with self._lock:
            return self.audio.setdefault(i, UnitAudio())

    def unit_done(self, i: int):
        a = self.audio.get(i)
        with self._lock:
            self.done.add(i)
            if a is not None and a.first is not None and a.bps:
                secs = (a.last - a.first) / a.bps
                chars = len(self.units[i])
                if secs > 0.3 and chars:
                    self.cps = 0.7 * self.cps + 0.3 * chars / secs

    # --- player ------------------------------------------------------------- #
    def mark(self, i: int, sink, start: int):
        with self._lock:
            self.marks.append((i, sink, start))

    # --- follow window ------------------------------------------------------ #
    def current(self, sink, played: int):
        """(start, end) of the word being heard, or None before the first unit."""
        with self._lock:
            hit = None
            for i, s, start in reversed(self.marks):
                if s is sink and start <= played:
                    hit = (i, start)
                    break
            if hit is None:
                return None
            i, start = hit
            spans, weights = self.spans[i], self.weights[i]
            if not spans:
                return None
            a = self.audio.get(i)
            t = played - start
            if a is None or not a.bps:
                return spans[0]
            lo = a.first or 0
            if i in self.done and a.last is not None:
                hi, cap = a.last, 1.0
            else:                        # still streaming: estimate the length
                hi, cap = lo + len(self.units[i]) / self.cps * a.bps, 0.95
            f = min(max((t - lo) / max(hi - lo, 1), 0.0), cap)
            return spans[pick(weights, f)]
