"""Follow window colors: every mark color and the read text keep WCAG AA
contrast in light and dark."""
import pytest

pytest.importorskip("PySide6.QtGui", exc_type=ImportError)
import tts_theme  # noqa: E402


def _lum(hex_color):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    h = hex_color.lstrip("#")[-6:]
    r, g, b = (ch(int(h[i:i + 2], 16)) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_mark_colors_keep_text_readable_and_stand_out(scheme):
    c = tts_theme.S2_LIGHT if scheme == "light" else tts_theme.S2_DARK
    for name, (light, dark) in tts_theme.MARKS.items():
        mark = light if scheme == "light" else dark
        assert _contrast(mark, c["on_mark"]) >= 4.5, name
        assert _contrast(mark, c["elevated"]) >= 1.5, name


@pytest.mark.parametrize("colors", [tts_theme.S2_LIGHT, tts_theme.S2_DARK])
def test_read_and_unread_text_meet_aa(colors):
    assert _contrast(colors["content_read"], colors["elevated"]) >= 4.5
    assert _contrast(colors["content"], colors["elevated"]) >= 4.5


def test_default_mark_exists():
    assert tts_theme.DEFAULT_MARK in tts_theme.MARKS
