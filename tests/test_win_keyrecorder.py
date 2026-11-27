"""Names the shortcut recorder (bin/win_keyrecorder.py) gives keys, and that
what it records survives storage in voices.conf and parses for
RegisterHotKey."""
import sys

import pytest

import tts_platform
import win_keyrecorder as kr


@pytest.mark.parametrize("vk, name", [
    (0x45, "E"), (0x31, "1"), (0x70, "F1"), (0x87, "F24"), (0x20, "Space"),
    (0x21, "PgUp"), (0x22, "PgDown"), (0x2E, "Del"), (0x25, "Left"),
])
def test_vk_name(vk, name):
    assert kr.vk_name(vk) == name


@pytest.mark.parametrize("vk", [0xA2, 0x10, 0xBC, 0xDB, 0xBA, 0x60])
def test_vk_name_rejects_modifiers_and_layout_keys(vk):
    # modifiers, comma, brackets, umlauts and the numpad cannot be stored
    assert kr.vk_name(vk) is None


def test_combo_text_uses_qt_order():
    assert kr.combo_text({"Shift", "Alt", "Ctrl"}, "E") == "Ctrl+Alt+Shift+E"
    assert kr.combo_text({"Meta", "Ctrl"}, "F5") == "Meta+Ctrl+F5"
    assert kr.combo_text({"Alt", "Ctrl"}, "") == "Ctrl+Alt"


@pytest.mark.parametrize("combo", ["Ctrl+Alt+E", "Ctrl+Shift+1", "Alt+F9",
                                   "Ctrl+Alt+PgUp", "Meta+Ctrl+Space"])
def test_recorded_combo_round_trips_through_voices_conf(combo):
    stored = tts_platform._qt_to_kbd(combo)
    assert tts_platform._kbd_to_qt(stored) == combo


@pytest.mark.skipif(sys.platform != "win32", reason="RegisterHotKey parser is Windows only")
@pytest.mark.parametrize("combo, mods, vk", [
    ("Ctrl+Alt+E", 0x3, 0x45), ("Ctrl+Shift+1", 0x6, 0x31),
    ("Ctrl+Alt+PgUp", 0x3, 0x21), ("Meta+Ctrl+F5", 0xA, 0x74),
])
def test_recorded_combo_parses_for_register_hotkey(combo, mods, vk):
    import win_hotkeys
    assert win_hotkeys._parse(tts_platform._qt_to_kbd(combo)) == (mods, vk)
