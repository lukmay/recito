"""voices.conf parsing (bin/tts_config.py) as the daemon uses it, and the
comment-preserving writer in tts_platform."""
import tts_config
import tts_platform

SAMPLE = """
# comment
de=de_DE-thorsten-low
en = en_US-amy-low
engine=Edge
device=GPU
fallback=aus
lang_detect=Sentence
filters=obsidian, latex,,
pause_sentence=250
prebuffer_ms=350
speed_normal=1.0
hotkey_read=ctrl+alt+p
autostart=on
edge_de=de-DE-KatjaNeural
kokoro_en=af_heart
sapi_de=Hedda
mssp_cmd=wine /opt/balcon/balcon.exe
mssp_en=ZiraPro
elevenlabs_model=eleven_flash_v2_5
elevenlabs_de=abc123
neural_idle_min=30
qwen3_de=Dylan
qwen3_instruct=calm = slow
kokoro2_de=thorsten
de=de_DE-kerstin-low
"""


def test_parse_basics():
    v = tts_config.parse(SAMPLE)
    assert v["en"] == "en_US-amy-low"
    assert v["de"] == "de_DE-kerstin-low"            # last occurrence wins
    assert v["qwen3_instruct"] == "calm = slow"     # split on the first '=' only
    assert "# comment" not in v and "" not in v


def test_read_missing_file(tmp_path):
    assert tts_config.read(tmp_path / "nope.conf") == {}


def test_daemon_config():
    cfg = tts_config.daemon_config(tts_config.parse(SAMPLE), {"qwen3", "kokoro2"})
    assert cfg.piper_voices == {"de": "de_DE-kerstin-low", "en": "en_US-amy-low"}
    assert cfg.engine == "edge"
    assert cfg.device == "gpu"
    assert cfg.fallback == "off"
    assert cfg.lang_detect == "sentence"
    assert cfg.filters == ["obsidian", "latex"]
    assert cfg.pause_ms == 250
    assert cfg.prebuffer_ms == 350
    assert cfg.mssp_cmd == "wine /opt/balcon/balcon.exe"
    assert cfg.voices("edge") == {"de": "de-DE-KatjaNeural"}
    assert cfg.voices("kokoro") == {}                 # removed engine, voices ignored
    assert "kokoro_en" not in cfg.piper_voices
    assert cfg.voices("sapi") == {"de": "Hedda"}
    assert cfg.voices("mssp") == {"en": "ZiraPro"}
    assert cfg.voices("unknown") == {}
    assert cfg.prefixed("elevenlabs") == {"model": "eleven_flash_v2_5", "de": "abc123"}
    assert cfg.prefixed("qwen3") == {"de": "Dylan", "instruct": "calm = slow"}


def test_daemon_config_defaults_and_bad_numbers():
    cfg = tts_config.daemon_config({"pause_sentence": "long", "prebuffer_ms": "-5",
                                    "fallback": "piper"})
    assert cfg.engine == "piper" and cfg.device == "cpu" and cfg.lang_detect == "text"
    assert cfg.pause_ms == 0
    assert cfg.prebuffer_ms == 0
    assert cfg.fallback == "piper"
    assert tts_config.daemon_config({"prebuffer_ms": "x"}).prebuffer_ms == \
        tts_config.DEFAULT_PREBUFFER_MS


def test_migrate_legacy_kokoro():
    values = {"engine": "Kokoro", "kokoro_de": "martin", "kokoro_en": "af_heart",
              "kokoro2_en": "bf_emma", "kokoro2_de": ""}
    out, note = tts_config.migrate(values, lambda k: k == "kokoro2")
    assert out["engine"] == "kokoro2" and "kokoro2" in note
    assert out["kokoro2_de"] == "martin"             # empty successor voice is filled
    assert out["kokoro2_en"] == "bf_emma"            # set successor voice wins
    assert values["engine"] == "Kokoro"              # input stays untouched
    out, note = tts_config.migrate(values)
    assert out["engine"] == "piper" and "not installed" in note
    same, note = tts_config.migrate({"engine": "edge"}, lambda k: True)
    assert same == {"engine": "edge"} and note == ""


def test_neural_keys_are_not_piper_voices():
    cfg = tts_config.daemon_config({"qwen3": "x", "qwen3_en": "y", "fish_v2_de": "z"},
                                   {"qwen3", "fish_v2"})
    assert cfg.piper_voices == {}


def test_settings_keys_are_not_piper_voices():
    cfg = tts_config.daemon_config({"de": "de_DE-thorsten-low", "ui_language": "auto",
                                    "read_langs": "de,en", "onboarded": "1"}, set())
    assert cfg.piper_voices == {"de": "de_DE-thorsten-low"}


def test_platform_conf_roundtrip(write_conf, isolated_dirs):
    p = write_conf("# keep me\nengine=edge\nde=voice-a\n")
    assert tts_platform.conf_path() == p
    tts_platform.conf_write({"engine": "piper", "speed_fast": "0.7"})
    text = p.read_text(encoding="utf-8")
    assert text.splitlines() == ["# keep me", "engine=piper", "de=voice-a", "speed_fast=0.7"]
    assert tts_platform.conf_read() == {"engine": "piper", "de": "voice-a", "speed_fast": "0.7"}
