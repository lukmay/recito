"""Follow window: the daemon's character positions land on the same
characters in the Qt text document, also with Windows line breaks and emoji."""
import pytest

pytest.importorskip("PySide6.QtGui", exc_type=ImportError)
from PySide6.QtGui import QTextDocument  # noqa: E402

import tts_follow  # noqa: E402

TEXTS = [
    "Mehr als 150 nominierte Frauen.\r\nEine gemeinsame Mission.",
    "Das Voting ist eröffnet – Jetzt\r\n\r\nseid Ihr gefragt! 🎉 Weiter 👩‍💻 geht's.",
    "old Mac\rline breaks and plain\nones",
]


@pytest.mark.parametrize("text", TEXTS)
def test_every_character_maps_to_itself_in_the_document(text):
    shown, pos = tts_follow.qt_text(text)
    doc = QTextDocument()
    doc.setPlainText(shown)
    assert len(pos) == len(text) + 1
    for i, ch in enumerate(text):
        if ch in "\r\n":
            continue                    # line breaks become block separators
        got = doc.characterAt(pos[i])
        if ord(ch) > 0xFFFF:            # first UTF-16 unit of a surrogate pair
            assert 0xD800 <= ord(got) <= 0xDBFF
        else:
            assert got == ch, (i, ch, got)


def test_word_span_after_windows_line_breaks():
    text = "Zeile eins.\r\nZeile zwei.\r\nDigitalisierung aktiv"
    shown, pos = tts_follow.qt_text(text)
    start = text.index("Digitalisierung")
    end = start + len("Digitalisierung")
    assert shown[pos[start]:pos[end]] == "Digitalisierung"
