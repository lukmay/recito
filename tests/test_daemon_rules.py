"""The daemon reads through the rules list (presets, then the user's rules),
with the language of the request, and picks up rule changes without a
restart."""
import json

import tts_rules
from test_daemon import daemon  # noqa: F401  (fixture)


def _add(pattern, replacement, **kw):
    tts_rules.RuleStore().add({"pattern": pattern, "replacement": replacement, **kw})


def test_rules_apply_after_filters(daemon):  # noqa: F811
    _add("Titel", "Title", kind="literal")
    d = daemon("engine=piper\nfilters=obsidian\n")
    assert d.run('{"text": "## **Titel**", "voice": "de"}') == ["piper:Title"]


def test_rules_reload_without_restart(daemon):  # noqa: F811
    d = daemon("engine=piper\n")
    assert d.run('{"text": "SQL", "voice": "de"}') == ["piper:SQL"]
    _add("SQL", "S Q L")
    assert d.run('{"text": "SQL", "voice": "de"}') == ["piper:S Q L"]


def test_language_scope_follows_voice(daemon):  # noqa: F811
    _add("SQL", "Es Ku El", lang="de")
    d = daemon("engine=piper\nen=de\n")
    assert d.run('{"text": "SQL hier", "voice": "de"}') == ["piper:Es Ku El hier"]
    assert d.run('{"text": "SQL hier", "voice": "en"}') == ["piper:SQL hier"]
    assert d.rules_lang("auto", "x") == "de"          # no detector: default voice


def test_broken_rule_never_stops_reading(daemon, isolated_dirs):  # noqa: F811
    (isolated_dirs["PIPER_DIR"] / "rules.json").write_text(
        '{"rules": [{"id": "bad", "pattern": "(", "kind": "regex"},'
        ' {"id": "ok", "pattern": "a", "replacement": "b", "kind": "literal"}]}', encoding="utf-8")
    d = daemon("engine=piper\n")
    assert d.run('{"text": "a", "voice": "de"}') == ["piper:b"]


def test_replace_conf_is_migrated_at_startup(daemon, isolated_dirs):  # noqa: F811
    (isolated_dirs["PIPER_DIR"] / "replace.conf").write_text("foo=>bar\n", encoding="utf-8")
    d = daemon("engine=piper\nfilters=latex,replace\n")
    assert (isolated_dirs["PIPER_DIR"] / "replace.conf.migrated").exists()
    assert d.run('{"text": "foo", "voice": "de"}') == ["piper:bar"]


def test_previews_skip_rules(daemon):  # noqa: F811
    _add("Probe", "Test")
    d = daemon("engine=piper\n")
    assert d.run('{"cmd": "test", "engine": "piper", "voice": "de", "text": "Probe."}') == ["piper:Probe."]


def test_filters_line_is_migrated_once(daemon, isolated_dirs):  # noqa: F811
    d = daemon("engine=piper\nfilters=latex\n")
    assert d.run(json.dumps({"text": "Wert \\alpha", "voice": "de"})) == ["piper:Wert Alpha"]
    assert d.run('{"text": "**fett**", "voice": "de"}') == ["piper:**fett**"]
    tts_rules.RuleStore().set_group("markdown", True)           # picked up without a restart
    assert d.run('{"text": "**fett**", "voice": "de"}') == ["piper:fett"]


def test_new_setup_cleans_markdown(daemon):  # noqa: F811
    d = daemon("engine=piper\n")
    assert d.run('{"text": "## [Titel](https://x.de)", "voice": "de"}') == ["piper:Titel"]
