# -*- coding: utf-8 -*-
"""The preset rules and their groups: shipped definitions, golden equivalence
with the former obsidian filter, the one-time migration of filters=,
restore and reset, group switches, code rules, command line and prompt."""
import json
import re
from pathlib import Path

import pytest

import golden_corpus as gc
import legacy_filters
import tts_filters
import tts_rules

BIN = Path(__file__).resolve().parent.parent / "bin"
ALL_ON = {g: True for g in tts_rules.ALL_GROUPS}


@pytest.fixture
def store(isolated_dirs):
    return tts_rules.RuleStore()


def presets():
    return [tts_rules.preset_rule(p["preset_id"]) for p in tts_filters.PRESETS]


def run(text, rules, groups=None, lang=None):
    comp = tts_rules.compile_all(rules, groups=groups or ALL_ON, reg=tts_filters.BUILTINS)
    return tts_rules.apply(text, lang, comp)


def norm(t):
    """Whitespace-insensitive form: the old filter and the presets leave
    spaces in different places, which the voice does not read."""
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" ([.,;:!?])", r"\1", t)
    t = re.sub(r" *\n *", "\n", t)
    return re.sub(r"\n+", "\n", t).strip()


def ids(rules):
    return [r["id"] for r in rules]


# --- Shipped definitions ------------------------------------------------------ #
def test_presets_are_valid_and_unique():
    seen = set()
    for p in tts_filters.PRESETS:
        assert p["preset_id"] not in seen
        seen.add(p["preset_id"])
        assert p["group"] in tts_filters.GROUPS and p["note"] and p["preset_version"] == 1
        assert tts_rules.check(p, tts_filters.BUILTINS) == "", p["preset_id"]
    groups = [p["group"] for p in tts_filters.PRESETS]
    assert groups == sorted(groups, key=tts_filters.GROUPS.index)        # one block per group
    assert [p["preset_id"] for p in tts_filters.PRESETS if p["kind"] == "builtin"] == ["dollar-math", "latex"]


@pytest.mark.parametrize("lang", ["en", "de"])
def test_every_preset_and_group_has_a_title(lang):
    cat = json.loads((BIN / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
    for p in tts_filters.PRESETS:
        assert cat.get(f"preset.{p['preset_id']}"), p["preset_id"]
        if p["kind"] == "builtin":
            assert cat.get(f"preset.{p['preset_id']}.desc")
    for g in tts_filters.ALL_GROUPS:
        assert cat.get(f"rules.group.{g}") and cat.get(f"rules.group.{g}.desc")
    for g in tts_filters.GROUPS:
        assert cat.get(f"rules.addGroup.{g}")
    assert not any("\u2014" in v for v in cat.values() if isinstance(v, str))


# --- Golden tests against the former obsidian filter ------------------------ #
@pytest.mark.parametrize("name", list(gc.SAME))
def test_golden_same_as_obsidian_and_latex(name):
    text = gc.SAME[name]
    old = tts_filters.latex(legacy_filters.obsidian(text))
    assert norm(run(text, presets())) == norm(old)


@pytest.mark.parametrize("name", list(gc.SAME))
def test_golden_same_as_obsidian_alone(name):
    text = gc.SAME[name]
    groups = {**ALL_ON, "formulas": False}
    assert norm(run(text, presets(), groups)) == norm(legacy_filters.obsidian(text))


@pytest.mark.parametrize("name", list(gc.DIFFERENT))
def test_golden_intended_differences(name):
    text, old, new = gc.DIFFERENT[name]
    assert norm(tts_filters.latex(legacy_filters.obsidian(text))) == norm(old)
    assert norm(run(text, presets())) == norm(new)


@pytest.mark.parametrize("text", [
    "Ein ganz normaler Satz, mit Komma; und Semikolon!",
    "Preise: 5 € oder $5, C# und F# sind Sprachen, Ticket #123.",
    "E-Mail an a.b@example.com, Datei C:/tmp/x_y_z.txt, 3 * 4 = 12.",
    "Die Formel a - b ist kurz. 1. FC Köln.",
])
def test_presets_leave_prose_alone(text):
    assert run(text, presets()) == text


def test_formulas_alone_match_the_latex_filter():
    text = gc.SAME["readest copy"]
    only = {"obsidian": False, "markdown": False, "formulas": True, "user": True}
    assert run(text, presets(), only) == tts_filters.latex(text)


def test_dollar_math_follows_pandoc():
    assert tts_filters.dollar_math("Kosten $5 bis $10.") == "Kosten $5 bis $10."
    assert tts_filters.dollar_math("Wert $x^2$.").split() == ["Wert", "x", "hoch", "zwei", "."]
    assert tts_filters.dollar_math("$$a=b$$").strip() == ". a gleich b."


# --- Migration of filters= ---------------------------------------------------- #
def legacy_pipeline(names, text, user_rules):
    """How the former daemon read text: filters in order, rules at 'replace'
    or last."""
    fns = {"obsidian": legacy_filters.obsidian, "latex": tts_filters.latex}
    placed = False
    for n in names:
        if n == "replace":
            text, placed = run(text, user_rules), True
        elif n in fns:
            text = fns[n](text)
    return text if placed else run(text, user_rules)


CANON = ["obsidian", "markdown", "formulas", "user"]
LATE = ["formulas", "user", "obsidian", "markdown"]          # obsidian came after 'replace'
MATRIX = [
    # filters= line, groups on, layout of the list by group
    (None, {"markdown", "formulas"}, CANON),
    ("obsidian,latex", {"obsidian", "markdown", "formulas"}, CANON),
    ("latex", {"formulas"}, CANON),
    ("", set(), CANON),
    ("obsidian", {"obsidian", "markdown"}, CANON),
    ("latex,obsidian", {"obsidian", "markdown", "formulas"}, CANON),
    ("obsidian,latex,replace", {"obsidian", "markdown", "formulas"}, CANON),
    ("latex,replace,obsidian", {"obsidian", "markdown", "formulas"}, LATE),
    ("replace,obsidian", {"obsidian", "markdown"}, LATE),
]
SAMPLE = "## **Titel** mit [[Seite|Alias]] und $x^2$, dazu SQL und \\alpha.\n- Punkt"


@pytest.mark.parametrize("line, on, layout", MATRIX)
def test_migration_matrix_sounds_the_same(store, write_conf, line, on, layout):
    if line is not None:
        write_conf(f"engine=piper\nfilters={line}\n")
    a = store.add({"pattern": "SQL", "replacement": "S Q L"})
    b = store.add({"pattern": "Titel", "replacement": "Title", "kind": "literal"})
    groups = tts_rules.migrate_presets(store)
    assert {g for g in tts_filters.GROUPS if groups[g]} == on and groups["user"] is True
    rules = store.load()
    assert list(dict.fromkeys(r["group"] for r in rules)) == layout
    assert [r["id"] for r in rules if r["group"] == "user"] == [a["id"], b["id"]]
    assert len(rules) == len(tts_filters.PRESETS) + 2
    assert tts_rules.group_state(rules) == groups
    if line is None:
        return
    names = [n for n in line.split(",") if n]
    new = tts_rules.LiveRules().apply(SAMPLE)
    if line == "latex,obsidian":
        # Intended: the list runs Obsidian, Markdown, Formulas, so LaTeX no
        # longer reads the "- " of a bullet as "minus" before Markdown drops it
        names = ["obsidian", "latex"]
        assert "minus" in legacy_pipeline(["latex", "obsidian"], "- α eins", [])
        assert "minus" not in tts_rules.LiveRules().apply("- α eins")
    old = legacy_pipeline(names, SAMPLE, [a, b])
    latex_first = {"latex", "obsidian"} <= set(names) and names.index("latex") < names.index("obsidian")
    if latex_first:
        # LaTeX first eats a space next to [[...]]; only spacing may differ
        assert re.sub(r"\s", "", new) == re.sub(r"\s", "", old)
    else:
        assert norm(new) == norm(old)


def test_migration_runs_once_and_keeps_a_backup(store, write_conf):
    write_conf("filters=latex\n")
    store.add({"pattern": "a", "replacement": "b"})
    before = store.path.read_text(encoding="utf-8")
    assert tts_rules.migrate_presets(store) is not None
    assert store.backup_path.read_text(encoding="utf-8") == before
    after = store.path.read_text(encoding="utf-8")
    assert json.loads(after)["presets"] == tts_filters.PRESET_VERSION
    assert tts_rules.migrate_presets(store) is None                 # idempotent
    assert store.path.read_text(encoding="utf-8") == after
    write_conf("filters=obsidian,latex\n")                          # filters= is not read again
    tts_rules.migrate(store)
    assert store.path.read_text(encoding="utf-8") == after


def test_migration_keeps_deleted_presets_deleted(store):
    tts_rules.migrate_presets(store)
    store.remove("md-bold")
    tts_rules.migrate(store)
    assert "md-bold" not in ids(store.load())


def test_migration_of_a_new_setup_without_any_file(store):
    groups = tts_rules.migrate_presets(store)
    assert groups == {"obsidian": False, "markdown": True, "formulas": True, "user": True}
    assert ids(store.load()) == [p["preset_id"] for p in tts_filters.PRESETS]
    assert not store.backup_path.exists()


def test_migration_with_replace_conf_keeps_its_order_after_the_presets(store, write_conf):
    write_conf("filters=obsidian,replace\n")
    store.path.with_name("replace.conf").write_text("eins=>1\nzwei=>2\n", encoding="utf-8")
    mine = store.add({"pattern": "drei", "replacement": "3"})
    tts_rules.migrate(store)
    user = [r for r in store.load() if r["group"] == "user"]
    assert [r["pattern"] for r in user] == ["drei", "eins", "zwei"]
    assert user[0]["id"] == mine["id"] and all(r["enabled"] for r in user)
    assert store.load()[0]["group"] == "obsidian"


def test_migration_turns_own_filters_into_code_rules(store, write_conf, isolated_dirs):
    (isolated_dirs["PIPER_DIR"] / "tts_filters.py").write_text(
        "FILTERS = {'upper': lambda t: t.upper()}\n", encoding="utf-8")
    write_conf("filters=latex,upper\n")
    tts_rules.migrate_presets(store)
    code = [r for r in store.load() if r.get("builtin") == "upper"]
    assert len(code) == 1 and code[0]["group"] == "user" and code[0]["kind"] == "builtin"
    assert tts_rules.LiveRules().apply("hallo") == "HALLO"


def test_broken_rules_file_is_not_migrated(store):
    store.path.write_text("{broken", encoding="utf-8")
    logs = []
    assert tts_rules.migrate_presets(store, logs.append) is None
    assert store.path.read_text(encoding="utf-8") == "{broken" and logs


# --- Restore and reset ------------------------------------------------------- #
@pytest.fixture
def seeded(store):
    tts_rules.migrate_presets(store)
    return store


def test_restore_adds_deleted_presets_back_in_place(seeded):
    store = seeded
    original = ids(store.load())
    for rid in ("obs-comment", "md-bold", "md-blank-lines", "latex"):
        store.remove(rid)
    mine = store.add({"pattern": "x", "replacement": "y"})
    done = store.restore()
    assert sorted(done["added"]) == sorted(["obs-comment", "md-bold", "md-blank-lines", "latex"])
    assert ids(store.load()) == original + [mine["id"]]


def test_restore_resets_changed_presets_and_keeps_switches(seeded):
    store = seeded
    store.update("md-bold", {"replacement": "fett", "note": "mine"}, by="agent")
    store.set_enabled("md-bold", False)
    rule = next(r for r in store.load() if r["id"] == "md-bold")
    assert tts_rules.preset_changes(rule) == ["replacement", "note"]
    done = store.restore()
    rule = next(r for r in store.load() if r["id"] == "md-bold")
    assert done == {"added": [], "reset": ["md-bold"]}
    assert tts_rules.preset_changes(rule) == [] and rule["enabled"] is False and rule["reviewed"] is True


def test_restore_one_group_switches_it_on(seeded):
    store = seeded
    for r in [r for r in store.load() if r["group"] == "obsidian"]:
        store.remove(r["id"])
    store.remove("md-bold")
    assert tts_rules.group_state(store.load())["obsidian"] is False
    done = store.restore("obsidian")
    rules = store.load()
    assert len(done["added"]) == 8 and "md-bold" not in ids(rules)
    assert rules[0]["id"] == "obs-comment" and tts_rules.group_state(rules)["obsidian"] is True
    with pytest.raises(tts_rules.RuleError) as exc:
        store.restore("user")
    assert exc.value.code == "group"


def test_reset_one_preset(seeded):
    store = seeded
    store.update("md-heading", {"pattern": "^#+ "})
    pos = ids(store.load()).index("md-heading")
    r = store.reset("md-heading")
    assert r["pattern"] == tts_filters.PRESETS_BY_ID["md-heading"]["pattern"]
    assert ids(store.load()).index("md-heading") == pos
    mine = store.add({"pattern": "a", "replacement": "b"})
    with pytest.raises(tts_rules.RuleError) as exc:
        store.reset(mine["id"])
    assert exc.value.code == "notPreset"


def test_whole_group_deleted_comes_back_between_its_neighbours(seeded):
    store = seeded
    store.remove("latex")
    mine = store.add({"pattern": "a", "replacement": "b"})
    store.restore("formulas")
    assert ids(store.load())[-2:] == ["latex", mine["id"]]


# --- Groups ------------------------------------------------------------------- #
def test_group_switch_keeps_rule_switches(seeded):
    store = seeded
    store.set_enabled("md-italic", False)
    live = tts_rules.LiveRules()
    assert live.apply("**fett**") == "fett"
    assert store.set_group("markdown", False)["markdown"] is False
    assert live.apply("**fett**") == "**fett**"
    store.set_group("markdown", True)
    rules = store.load()
    assert next(r for r in rules if r["id"] == "md-italic")["enabled"] is False
    assert live.apply("**fett** *kursiv*") == "fett *kursiv*"
    with pytest.raises(tts_rules.RuleError):
        store.set_group("nope", False)


def test_new_rule_goes_to_the_end_of_its_group(seeded):
    store = seeded
    r = store.add({"pattern": "a", "replacement": "b"}, group="markdown")
    rules = store.load()
    i = ids(rules).index(r["id"])
    assert rules[i - 1]["id"] == "md-blank-lines" and rules[i + 1]["id"] == "latex"
    u = store.add({"pattern": "c", "replacement": "d"})
    assert store.load()[-1]["id"] == u["id"] and u["group"] == "user"


def test_user_rules_switched_off_as_a_group(seeded):
    store = seeded
    store.add({"pattern": "SQL", "replacement": "S Q L"})
    store.set_group("user", False)
    assert tts_rules.LiveRules().apply("SQL") == "SQL"


# --- Code rules ---------------------------------------------------------------- #
def test_code_rules_cannot_be_edited_but_switched(seeded):
    store = seeded
    with pytest.raises(tts_rules.RuleError) as exc:
        store.update("latex", {"pattern": "x"})
    assert exc.value.code == "codeFixed"
    store.update("latex", {"note": "my note", "lang": "de"})
    store.set_enabled("latex", False)
    r = next(r for r in store.load() if r["id"] == "latex")
    assert r["note"] == "my note" and r["builtin"] == "latex" and r["pattern"] == ""


def test_unknown_code_rule_is_skipped_and_reported(store):
    store.path.write_text(json.dumps({"presets": 1, "rules": [
        {"id": "c1", "kind": "builtin", "builtin": "nope"},
        {"id": "ok", "kind": "literal", "pattern": "a", "replacement": "b"}]}), encoding="utf-8")
    logs = []
    assert tts_rules.LiveRules(log=logs.append).apply("a") == "b"
    assert any("c1" in m and "skipped" in m for m in logs)
    t = tts_rules.trace("a", None, store.load(), reg=tts_filters.BUILTINS)
    assert t["skipped"][0]["id"] == "c1" and "nope" in t["skipped"][0]["error"]


def test_trace_marks_what_a_code_rule_changed():
    rules = [tts_rules.preset_rule("dollar-math")]
    t = tts_rules.trace("Es gilt $x^2$ immer.", None, rules, reg=tts_filters.BUILTINS)
    assert t["output"] == tts_filters.dollar_math("Es gilt $x^2$ immer.")
    assert "".join(r["text"] for r in t["after"]) == t["output"]
    assert t["before"][0] == {"text": "Es gilt ", "rule": ""}
    assert t["fired"][0]["id"] == "dollar-math" and t["fired"][0]["kind"] == "builtin"


def test_trace_equals_apply_on_the_whole_list(seeded):
    rules = seeded.load()
    for text in list(gc.SAME.values()) + [v[0] for v in gc.DIFFERENT.values()]:
        assert tts_rules.trace(text, "de", rules)["output"] == tts_rules.LiveRules().apply(text, "de")


# --- Command line -------------------------------------------------------------- #
def cli(capsys, *argv):
    code = tts_rules.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_list_shows_groups(capsys, store, write_conf):
    write_conf("filters=latex\n")
    code, out, _ = cli(capsys, "list")
    assert code == 0
    assert "[obsidian: OFF, 8 rules]" in out and "[formulas: on, 1 rule]" in out
    assert "code latex" in out
    code, out, _ = cli(capsys, "list", "--group", "formulas", "--json")
    data = json.loads(out)
    assert [r["id"] for r in data["rules"]] == ["latex"] and data["groups"]["markdown"] is False


def test_cli_group_switch_reset_restore(capsys, store):
    assert cli(capsys, "disable", "--group", "markdown")[0] == 0
    assert tts_rules.group_state(store.load())["markdown"] is False
    assert cli(capsys, "edit", "md-bold", "--replacement", "X")[0] == 0
    code, out, _ = cli(capsys, "list", "--group", "markdown")
    assert "[changed preset" in out and "[markdown: OFF" in out
    code, out, _ = cli(capsys, "remove", "md-italic")
    assert "restore brings it back" in out
    code, out, _ = cli(capsys, "list", "--json")
    data = json.loads(out)
    assert data["missing_presets"] == ["md-italic"]
    assert next(r for r in data["rules"] if r["id"] == "md-bold")["changed"] == ["replacement"]
    code, out, _ = cli(capsys, "reset", "md-bold", "--json")
    assert code == 0 and json.loads(out)["rule"]["replacement"] == r"\1\2"
    code, out, _ = cli(capsys, "restore", "--group", "markdown", "--json")
    assert code == 0 and json.loads(out)["added"] == ["md-italic"]
    assert tts_rules.group_state(store.load())["markdown"] is True
    code, out, _ = cli(capsys, "restore", "--all")
    assert code == 0 and "added back 0 presets, reset 0" in out


def test_cli_usage_errors(capsys, store):
    for argv in (["enable"], ["disable", "x", "--group", "user"],
                 ["restore", "--all", "--group", "markdown"]):
        with pytest.raises(SystemExit) as exc:
            tts_rules.main(argv)
        assert exc.value.code == tts_rules.EXIT_USAGE
    capsys.readouterr()
    r = store.add({"pattern": "a", "replacement": "b"})
    code, out, _ = cli(capsys, "reset", r["id"], "--json")
    assert code == tts_rules.EXIT_INVALID and json.loads(out)["code"] == "notPreset"


def test_cli_add_to_a_group_and_add_code(capsys, store, isolated_dirs):
    code, out, _ = cli(capsys, "add", "~", "", "--kind", "literal", "--group", "markdown", "--json")
    rid = json.loads(out)["rule"]["id"]
    rules = store.load()
    assert rules[ids(rules).index(rid) - 1]["id"] == "md-blank-lines"
    (isolated_dirs["PIPER_DIR"] / "tts_filters.py").write_text(
        "FILTERS = {'shout': lambda t: t + '!'}\n", encoding="utf-8")
    code, out, _ = cli(capsys, "add-code", "shout", "--json")
    assert code == 0 and json.loads(out)["rule"]["builtin"] == "shout"
    code, out, _ = cli(capsys, "test", "ja", "--json")
    assert json.loads(out)["output"] == "ja!"
    code, out, _ = cli(capsys, "add-code", "nope", "--json")
    assert code == tts_rules.EXIT_INVALID and json.loads(out)["code"] == "builtin"


def test_cli_test_shows_presets_in_order(capsys, store):
    code, out, _ = cli(capsys, "test", "# **Titel** mit $x^2$")
    assert code == 0 and "after:  Titel mit x hoch zwei" in out
    fired = [line.split()[0] for line in out.splitlines()[3:]]
    assert fired[:3] == ["dollar-math", "md-heading", "md-bold"]


def test_export_import_keeps_groups_and_presets(capsys, store, tmp_path):
    tts_rules.migrate_presets(store)
    store.add({"pattern": "a", "replacement": "b"})
    out = tmp_path / "x.json"
    assert cli(capsys, "export", "--out", str(out))[0] == 0
    other = tts_rules.RuleStore(tmp_path / "o" / "rules.json")
    added, skipped = other.import_rules(tts_rules.parse(out.read_text(encoding="utf-8")),
                                        by="user", replace_all=True)
    rules = other.load()
    assert added == len(tts_filters.PRESETS) + 1 and skipped == 0
    assert [r["group"] for r in rules] == [r["group"] for r in store.load()]
    assert [r.get("preset_id") for r in rules][:3] == ["obs-comment", "obs-embed", "obs-callout"]


# --- Prompt ---------------------------------------------------------------------- #
def test_prompt_explains_groups_and_presets():
    text = tts_rules.agent_prompt(path="/x/rules.json", windows=False, python="/usr/bin/python3",
                                  script="/x/tts_rules.py", wrapper="")
    for word in ("obsidian", "markdown", "formulas", "user", "restore", "reset <id>", "--group",
                 "Presets come with the app", "Do not change, switch off, reset or remove presets"):
        assert word in text, word
    assert "built-in filters" not in text and "\u2014" not in text


# --- Settings window backend ----------------------------------------------------- #
def test_settings_backend_groups_and_restore(store, write_conf):
    pytest.importorskip("PySide6.QtWidgets")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    import tts_settings
    write_conf("filters=obsidian,latex\n")
    b = tts_settings.Backend()
    r = b.ruleGroupToggle("obsidian", False)
    assert r["ok"] and isinstance(r["groups"], list)          # the state keeps its group list
    assert next(g for g in r["groups"] if g["key"] == "obsidian")["on"] is False
    b.ruleRemove("md-bold")
    b.ruleSave({"id": "md-heading", "kind": "regex", "pattern": "^#+ ", "replacement": "",
                "lang": "all", "note": ""})
    st = b.rulesState()
    md = next(g for g in st["groups"] if g["key"] == "markdown")
    assert (md["missing"], md["changed"], st["canRestore"]) == (1, 1, True)
    r = b.rulesRestore("")
    assert (r["added"], r["reset"]) == (1, 1) and not r["canRestore"]
    assert not b.ruleSave({"id": "latex", "kind": "regex", "pattern": "x"})["ok"]
    t = b.rulesTry("# **Hallo**", "de")
    assert t["output"] == "Hallo" and [f["id"] for f in t["fired"]] == ["md-heading", "md-bold"]
    assert "filters" not in b.initial()
