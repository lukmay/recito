"""Word timing for the follow window: spans of the units' words in the text,
weights and the word at a play position."""
import numpy as np

import tts_timing


def _pcm(seconds, rate=1000, loud=True):
    n = int(seconds * rate)
    return (np.full(n, 3000 if loud else 0, dtype=np.int16)).tobytes()


def test_word_spans_follow_the_text_across_whitespace():
    text = "Erster  Satz.\n\nZweiter, langer Satz."
    units = ["Erster Satz.", "Zweiter,", "langer Satz."]
    spans = tts_timing.word_spans(text, units)
    words = [[text[s:e] for s, e in u] for u in spans]
    assert words == [["Erster", "Satz."], ["Zweiter,"], ["langer", "Satz."]]
    assert spans[2][1][0] > spans[0][1][0]          # the second "Satz." is found, not the first


def test_word_spans_skip_a_word_that_is_not_in_the_text():
    assert tts_timing.word_spans("a b", ["a x b"]) == [[(0, 1), (2, 3)]]


def test_weights_count_digits_double_and_pause_after_punctuation():
    text = "Im Jahr 1984, sagte er."
    spans = tts_timing.word_spans(text, [text])[0]
    w = tts_timing.word_weights(text, spans)
    assert w[2] == 1 + 4 * 2 + 1 + 3                # "1984,": digits double, the comma, its pause
    assert w[-1] == 1 + len("er.")                  # no pause after the last word


def test_pick_walks_through_the_words():
    assert [tts_timing.pick([1, 1, 2], f) for f in (0.0, 0.3, 0.6, 0.99, 1.0)] == [0, 1, 2, 2, 2]


def test_unit_audio_finds_the_voiced_range():
    a = tts_timing.UnitAudio()
    a.add(_pcm(0.2, loud=False) + _pcm(1.0), 1000, 1)
    a.add(_pcm(0.3, loud=False) + b"x", 1000, 1)    # odd length (fake engines) does not fail
    assert (a.first, a.last, a.bps) == (400, 2400, 2000)


def test_tracker_maps_the_play_position_to_words():
    text = "eins zwei drei"
    t = tts_timing.Tracker(text, [text])
    sink = object()
    assert t.current(sink, 0) is None               # nothing started yet
    a = t.unit_audio(0)
    a.add(_pcm(0.5, loud=False) + _pcm(3.0) + _pcm(0.5, loud=False), 1000, 1)
    t.unit_done(0)
    t.mark(0, sink, 0)
    word = lambda pos: text[slice(*t.current(sink, pos))]
    assert word(0) == "eins"                        # leading silence: first word
    assert word(1000 + 2400) == "zwei"              # 1.2 s into 3 s of voice
    assert word(1000 + 5900) == "drei"
    assert word(9000) == "drei"                     # trailing silence: last word stays
    assert t.current(object(), 3000) is None        # another sink


def test_tracker_estimates_a_unit_that_is_still_streaming():
    text = "a b c d e f g h"
    t = tts_timing.Tracker(text, [text])
    sink = object()
    t.unit_audio(0).add(_pcm(0.1), 1000, 1)
    t.mark(0, sink, 0)
    s, e = t.current(sink, 10 ** 6)                 # far beyond the estimate: capped
    assert text[s:e] != "" and e <= len(text)
