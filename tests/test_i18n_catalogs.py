"""UI catalogs: complete against en.json (scripts/check_i18n.py) and offered
with their native names."""
import importlib.util
import json
from pathlib import Path

import pytest

import tts_i18n

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_i18n", ROOT / "scripts" / "check_i18n.py")
check_i18n = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_i18n)


@pytest.mark.parametrize("code", check_i18n.codes())
def test_catalog_matches_english(code):
    problems = check_i18n.check_catalog(code)
    assert {k: v for k, v in problems.items() if v} == {}


def test_code_uses_only_existing_keys():
    assert check_i18n.check_all()[1] == []


def test_check_finds_problems(tmp_path, monkeypatch):
    en = {"_meta": {"name": "English", "locale": "en_US", "decimal": "."},
          "a": "Hello {name}", "b": "Save", "c": "Ctrl"}
    xx = {"_meta": {"name": "X", "locale": "xx_XX"},
          "a": "Hallo {nom}", "b": "Save", "d": "extra — dash"}
    for code, data in (("en", en), ("xx", xx)):
        (tmp_path / f"{code}.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(check_i18n, "CATALOGS", tmp_path)
    p = check_i18n.check_catalog("xx")
    assert p["missing"] == ["c"] and p["extra"] == ["d"]
    assert p["placeholders"] == ["a"] and p["english"] == ["b"]
    assert p["dashes"] == ["d"] and p["meta"] == ["decimal"]


def test_languages_offered_with_native_names():
    langs = dict(tts_i18n.available())
    assert tts_i18n.available()[0][0] == "en"
    assert {"en": "English", "de": "Deutsch", "fr": "Français", "es": "Español",
            "it": "Italiano"}.items() <= langs.items()


@pytest.mark.parametrize("code", ["fr", "es", "it"])
def test_new_catalogs_switch_live(code, monkeypatch):
    monkeypatch.delenv("TTS_UI_LANG", raising=False)
    monkeypatch.setattr(tts_i18n, "_current", None)
    tts_i18n.set_language(code, persist=False)
    assert tts_i18n.language() == code
    assert tts_i18n.tr("nav.voice") != json.loads(
        (ROOT / "bin/i18n/en.json").read_text(encoding="utf-8"))["nav.voice"]
    assert tts_i18n.fmt_num(1234.5, 1) == "1.234,5"
    tts_i18n.set_language("en", persist=False)
