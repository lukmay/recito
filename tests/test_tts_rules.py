"""tts_rules: store, validation, migration of replace.conf, command line and
live reload. Presets, groups and the migration of filters= are in
test_presets.py. Every file lives in the temporary PIPER_DIR from conftest."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import tts_rules

BIN = Path(__file__).resolve().parent.parent / "bin"


@pytest.fixture
def store(isolated_dirs):
    return tts_rules.RuleStore()


@pytest.fixture
def plain(store):
    """A store whose list is already set up without presets, so the command
    line adds none and positions stay as the test writes them."""
    store.path.write_text('{"version": 1, "presets": 1, "rules": []}', encoding="utf-8")
    return store


def add(store, pattern, replacement, **kw):
    return store.add({"pattern": pattern, "replacement": replacement, **kw}, by=kw.pop("by", "user"))


def run(text, store, lang=None):
    return tts_rules.apply(text, lang, tts_rules.compile_all(store.load()))


# --- Store and validation ------------------------------------------------ #
def test_default_path_is_in_piper_dir(isolated_dirs):
    assert tts_rules.default_path() == isolated_dirs["PIPER_DIR"] / "rules.json"


def test_add_writes_versioned_file_with_all_fields(store):
    r = add(store, "SQL", "S Q L", note="acronym")
    data = json.loads(store.path.read_text(encoding="utf-8"))
    assert data["version"] == tts_rules.VERSION and data["about"]
    saved = data["rules"][0]
    assert list(saved)[:len(tts_rules.FIELD_ORDER)] == list(tts_rules.FIELD_ORDER)
    assert saved["id"] == r["id"] and saved["order"] == 1
    assert saved["kind"] == "word" and saved["lang"] == "all" and saved["enabled"] is True
    assert saved["created_by"] == "user" and saved["reviewed"] is True
    assert saved["created"].endswith("Z") and saved["updated"].endswith("Z")


@pytest.mark.parametrize("fields, code", [
    ({"pattern": ""}, "empty"),
    ({"pattern": "   "}, "empty"),
    ({"pattern": "(a", "kind": "regex"}, "regex"),
    ({"pattern": "x*", "kind": "regex"}, "emptyMatch"),
    ({"pattern": "(a)", "kind": "regex", "replacement": r"\2"}, "template"),
    ({"pattern": "a", "kind": "regex", "replacement": r"\q"}, "template"),
    ({"pattern": "a", "kind": "fuzzy"}, "kind"),
    ({"pattern": "a", "lang": "xx"}, "lang"),
])
def test_invalid_rules_are_refused(store, fields, code):
    with pytest.raises(tts_rules.RuleError) as exc:
        store.add(fields)
    assert exc.value.code == code
    assert not store.path.exists()                 # nothing written


def test_check_returns_message_or_empty():
    assert tts_rules.check({"pattern": "a", "kind": "literal"}) == ""
    assert "compile" in tts_rules.check({"pattern": "(", "kind": "regex"})


def test_kinds(store):
    add(store, "SQL", "S Q L", kind="word")
    add(store, "°C", " Grad Celsius", kind="literal")
    add(store, r"(\d+) ?km/h", r"\1 Kilometer pro Stunde", kind="regex")
    add(store, "z.B.", "zum Beispiel", kind="word")
    out = run("SQL, MySQL, 20°C, 50 km/h, z.B. hier", store)
    assert out == "S Q L, MySQL, 20 Grad Celsius, 50 Kilometer pro Stunde, zum Beispiel hier"


def test_literal_replacement_is_not_a_template(store):
    add(store, "a", r"\1 and $0", kind="literal")
    assert run("a", store) == r"\1 and $0"


def test_language_scope(store):
    add(store, "z.B.", "zum Beispiel", kind="literal", lang="de")
    add(store, "e.g.", "for example", kind="literal", lang="en")
    add(store, "SQL", "S Q L")
    text = "SQL z.B. e.g."
    assert run(text, store, "de") == "S Q L zum Beispiel e.g."
    assert run(text, store, "en") == "S Q L z.B. for example"
    assert run(text, store, None) == "S Q L z.B. e.g."


def test_rules_run_in_order_on_each_others_output(store):
    a = add(store, "A", "B", kind="literal")
    add(store, "B", "C", kind="literal")
    assert run("A", store) == "C"
    assert store.move(a["id"], 2) == 2
    assert [r["order"] for r in store.load()] == [1, 2]
    assert run("A", store) == "B"


def test_move_clamps_and_add_at_position(store):
    a = add(store, "a", "1")
    b = add(store, "b", "2")
    c = store.add({"pattern": "c", "replacement": "3"}, at=1)
    assert [r["id"] for r in store.load()] == [c["id"], a["id"], b["id"]]
    store.move(c["id"], 99)
    assert [r["id"] for r in store.load()][-1] == c["id"]
    store.move(c["id"], 0)
    assert store.load()[0]["id"] == c["id"]


def test_edit_enable_disable_remove(store):
    r = add(store, "a", "b")
    store.update(r["id"], {"replacement": "c", "pattern": None})
    assert store.load()[0]["replacement"] == "c" and store.load()[0]["pattern"] == "a"
    with pytest.raises(tts_rules.RuleError):
        store.update(r["id"], {"kind": "regex", "pattern": "("})
    assert store.load()[0]["kind"] == "word"       # failed edit leaves the rule alone
    store.set_enabled(r["id"], False)
    assert run("a", store) == "a"
    store.set_enabled(r["id"], True)
    assert run("a", store) == "c"
    store.remove(r["id"])
    assert store.load() == []
    with pytest.raises(tts_rules.NotFound):
        store.remove(r["id"])


def test_agent_changes_need_review(store):
    r = store.add({"pattern": "a", "replacement": "b"}, by="agent")
    assert r["created_by"] == "agent" and r["reviewed"] is False
    store.review(r["id"])
    assert store.load()[0]["reviewed"] is True
    store.update(r["id"], {"replacement": "c"}, by="agent")
    assert store.load()[0]["reviewed"] is False and store.load()[0]["updated_by"] == "agent"
    store.update(r["id"], {}, by="user")                # keeping it in the window
    assert store.load()[0]["reviewed"] is True and store.load()[0]["created_by"] == "agent"


def test_undo_is_one_step_and_swaps(store):
    assert store.undo() is False
    add(store, "a", "1")
    add(store, "b", "2")
    assert store.backup_path.exists()
    assert store.undo() is True
    assert [r["pattern"] for r in store.load()] == ["a"]
    assert store.undo() is True                     # second undo redoes
    assert [r["pattern"] for r in store.load()] == ["a", "b"]


def test_undo_never_restores_a_broken_backup(store):
    add(store, "a", "1")
    add(store, "b", "2")
    store.backup_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(tts_rules.StoreError):
        store.undo()
    assert len(store.load()) == 2


def test_write_is_atomic_and_leaves_no_temp_files(store):
    for i in range(5):
        add(store, f"w{i}", "x")
    names = sorted(p.name for p in store.path.parent.iterdir())
    assert names == ["rules.json", "rules.json.bak"]


def test_busy_lock_times_out(store, monkeypatch):
    lock = store.path.with_name("rules.json.lock")
    lock.write_text("")
    with pytest.raises(tts_rules.StoreError):
        with store._locked(timeout=0.2):
            pass
    os.utime(lock, (0, 0))                          # stale lock from a crash is taken over
    with store._locked(timeout=0.2):
        pass
    assert not lock.exists()


def test_broken_file_is_reported_not_overwritten(store):
    store.path.write_text("{not json", encoding="utf-8")
    with pytest.raises(tts_rules.StoreError):
        store.load()
    with pytest.raises(tts_rules.StoreError):
        add(store, "a", "b")
    assert store.path.read_text(encoding="utf-8") == "{not json"


def test_hand_edited_file_keeps_unknown_and_broken_rules(store):
    store.path.write_text(json.dumps({"version": 1, "rules": [
        {"id": "x1", "pattern": "(", "kind": "regex", "order": 2, "custom": 7},
        {"id": "x2", "pattern": "ok", "replacement": "fine", "kind": "literal", "order": 1},
    ]}), encoding="utf-8")
    rules = store.load()
    assert [r["id"] for r in rules] == ["x2", "x1"]           # sorted by order
    assert run("ok", store) == "fine"                          # broken rule skipped
    add(store, "z", "y")
    saved = json.loads(store.path.read_text(encoding="utf-8"))["rules"]
    assert saved[1]["custom"] == 7 and saved[1]["pattern"] == "("


def test_newer_file_version_is_refused(store):
    store.path.write_text(json.dumps({"version": 99, "rules": []}), encoding="utf-8")
    with pytest.raises(tts_rules.StoreError):
        store.load()


def test_import_is_all_or_nothing_and_skips_duplicates(store):
    add(store, "a", "1")
    with pytest.raises(tts_rules.RuleError):
        store.import_rules([{"pattern": "b", "replacement": "2"}, {"pattern": ""}])
    assert len(store.load()) == 1
    added, skipped = store.import_rules([{"pattern": "a", "replacement": "1"},
                                         {"pattern": "b", "replacement": "2"}])
    assert (added, skipped) == (1, 1)
    assert store.load()[1]["created_by"] == "agent"
    added, _ = store.import_rules([{"pattern": "c", "replacement": "3"}], by="user", replace_all=True)
    assert [r["pattern"] for r in store.load()] == ["c"]


# --- Trace (test command, highlighting) --------------------------------- #
def test_trace_matches_apply_and_marks_spans(store):
    add(store, "SQL", "S Q L")
    add(store, r"(\d+) ?km/h", r"\1 km pro h", kind="regex")
    add(store, "z.B.", "zum Beispiel", kind="literal", lang="de")
    text = "SQL bei 50 km/h, z.B. heute"
    t = tts_rules.trace(text, "de", store.load())
    assert t["output"] == run(text, store, "de")
    assert "".join(r["text"] for r in t["before"]) == text
    assert "".join(r["text"] for r in t["after"]) == t["output"]
    hit = [r["text"] for r in t["before"] if r["rule"]]
    assert hit == ["SQL", "50 km/h", "z.B."]
    written = [r["text"] for r in t["after"] if r["rule"]]
    assert written == ["S Q L", "50 km pro h", "zum Beispiel"]
    assert [f["count"] for f in t["fired"]] == [1, 1, 1]


def test_trace_reports_broken_rules(store):
    store.path.write_text(json.dumps({"rules": [{"id": "bad", "pattern": "(", "kind": "regex"}]}),
                          encoding="utf-8")
    t = tts_rules.trace("x", None, store.load())
    assert t["output"] == "x" and t["skipped"][0]["id"] == "bad"


def test_zero_width_regex_inserts(store):
    add(store, r"(?<=\d)(?=kg)", " ", kind="regex")
    assert run("3kg", store) == "3 kg"
    assert tts_rules.trace("3kg", None, store.load())["output"] == "3 kg"


# --- Live reload ----------------------------------------------------------- #
def test_live_rules_reload_on_change(store):
    logs = []
    live = tts_rules.LiveRules(log=logs.append)
    assert live.apply("SQL") == "SQL"
    add(store, "SQL", "S Q L")
    assert live.apply("SQL") == "S Q L"
    r = store.load()[0]
    store.update(r["id"], {"replacement": "Sequel"})
    assert live.apply("SQL") == "Sequel"
    store.path.write_text("{broken", encoding="utf-8")          # keeps the last good rules
    assert live.apply("SQL") == "Sequel"
    assert any("not reloaded" in m for m in logs)
    store.path.unlink()
    assert live.apply("SQL") == "SQL"


def test_broken_regex_in_file_is_logged_and_skipped(store):
    store.path.write_text(json.dumps({"rules": [
        {"id": "bad", "pattern": "(", "kind": "regex"},
        {"id": "ok", "pattern": "a", "replacement": "b", "kind": "literal"}]}), encoding="utf-8")
    logs = []
    live = tts_rules.LiveRules(log=logs.append)
    assert live.apply("a") == "b"
    assert any("bad" in m and "skipped" in m for m in logs)


def test_rule_error_at_runtime_does_not_stop_reading(store):
    add(store, "a", "b")
    compiled = tts_rules.compile_all(store.load())

    class Boom:
        def sub(self, *a):
            raise RuntimeError("boom")
    compiled.insert(0, tts_rules.Compiled({"id": "x", "kind": "literal", "replacement": ""},
                                          Boom()))
    logs = []
    assert tts_rules.apply("a", None, compiled, logs.append) == "b"
    assert logs and "boom" in logs[0]


# --- Migration of replace.conf --------------------------------------------- #
CONF = "# comment\n\\bz\\.B\\.=>zum Beispiel\n°C=> Grad Celsius\n(bad=>x\nno arrow here\n\n"


def test_migration_imports_once_and_renames(store, write_conf):
    write_conf("filters=obsidian,replace\n")
    conf = store.path.with_name("replace.conf")
    conf.write_text(CONF, encoding="utf-8")
    assert tts_rules.migrate_replace_conf(store) == 3
    assert not conf.exists()
    assert conf.with_name("replace.conf.migrated").read_text(encoding="utf-8") == CONF
    rules = store.load()
    assert [(r["kind"], r["pattern"], r["enabled"]) for r in rules] == [
        ("regex", r"\bz\.B\.", True), ("literal", "°C", True), ("regex", "(bad", False)]
    assert "does not work" in rules[2]["note"]
    assert run("z.B. 5°C", store) == "zum Beispiel 5Grad Celsius"
    assert tts_rules.migrate_replace_conf(store) is None        # nothing left to do


def test_migration_keeps_rules_off_when_replace_filter_was_off(store, write_conf):
    write_conf("filters=latex\n")
    store.path.with_name("replace.conf").write_text("a=>b\n", encoding="utf-8")
    tts_rules.migrate_replace_conf(store)
    assert store.load()[0]["enabled"] is False


def test_migration_merges_and_never_overwrites(store, write_conf):
    write_conf("filters=replace\n")
    add(store, "x", "y")
    conf = store.path.with_name("replace.conf")
    conf.with_name("replace.conf.migrated").write_text("old", encoding="utf-8")
    conf.write_text("a=>b\nx=>y\n", encoding="utf-8")
    assert tts_rules.migrate_replace_conf(store) == 2       # x=>y is literal, differs from word x
    assert conf.with_name("replace.conf.migrated").read_text(encoding="utf-8") == "old"
    assert conf.with_name("replace.conf.migrated.1").read_text(encoding="utf-8") == "a=>b\nx=>y\n"
    conf.write_text("a=>b\n", encoding="utf-8")
    assert tts_rules.migrate_replace_conf(store) == 0       # duplicate skipped
    assert len(store.load()) == 3


def test_migration_of_comments_only_file(store):
    conf = store.path.with_name("replace.conf")
    conf.write_text("# only comments\n#   \\bz\\.B\\.=>zum Beispiel\n", encoding="utf-8")
    assert tts_rules.migrate_replace_conf(store) == 0
    assert conf.with_name("replace.conf.migrated").exists() and not conf.exists()


def test_migration_waits_when_rules_file_is_broken(store):
    store.path.write_text("{broken", encoding="utf-8")
    conf = store.path.with_name("replace.conf")
    conf.write_text("a=>b\n", encoding="utf-8")
    assert tts_rules.migrate_replace_conf(store) is None
    assert conf.exists()                                       # nothing lost


# --- Command line ------------------------------------------------------------ #
def cli(capsys, *argv):
    code = tts_rules.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_add_list_json(capsys, store):
    code, out, _ = cli(capsys, "add", "SQL", "S Q L", "--note", "acronym", "--json")
    assert code == 0
    r = json.loads(out)["rule"]
    assert r["created_by"] == "agent" and r["reviewed"] is False
    code, out, _ = cli(capsys, "add", "z.B.", "zum Beispiel", "--kind", "literal", "--lang", "de",
                       "--by", "user")
    assert code == 0 and "added" in out
    code, out, _ = cli(capsys, "list", "--group", "user", "--json")
    data = json.loads(out)
    assert data["ok"] and [x["pattern"] for x in data["rules"]] == ["SQL", "z.B."]
    assert data["rules"][1]["created_by"] == "user"
    code, out, _ = cli(capsys, "list")
    assert "agent*" in out and "SQL" in out


def test_cli_exit_codes(capsys, store):
    code, out, _ = cli(capsys, "add", "(", "x", "--kind", "regex", "--json")
    assert code == tts_rules.EXIT_INVALID and json.loads(out)["code"] == "regex"
    code, _, err = cli(capsys, "remove", "nope")
    assert code == tts_rules.EXIT_NOT_FOUND and "nope" in err
    code, out, _ = cli(capsys, "undo", "--json")
    assert code == tts_rules.EXIT_STORE and json.loads(out)["ok"] is False
    with pytest.raises(SystemExit) as exc:
        tts_rules.main(["frobnicate"])
    assert exc.value.code == tts_rules.EXIT_USAGE
    capsys.readouterr()


def test_cli_edit_move_enable_disable_remove_undo(capsys, plain):
    store = plain
    a = add(store, "a", "1")
    b = add(store, "b", "2")
    assert cli(capsys, "edit", a["id"], "--replacement", "one")[0] == 0
    assert store.load()[0]["replacement"] == "one" and store.load()[0]["reviewed"] is False
    assert cli(capsys, "move", b["id"], "--to", "1")[0] == 0
    assert store.load()[0]["id"] == b["id"]
    assert cli(capsys, "disable", a["id"])[0] == 0
    assert store.load()[1]["enabled"] is False
    assert cli(capsys, "enable", a["id"])[0] == 0
    assert cli(capsys, "rm", a["id"])[0] == 0
    assert [r["id"] for r in store.load()] == [b["id"]]
    assert cli(capsys, "undo")[0] == 0
    assert len(store.load()) == 2
    code, out, _ = cli(capsys, "show", a["id"], "--json")
    assert json.loads(out)["rule"]["id"] == a["id"]


def test_cli_test_command(capsys, store, write_conf):
    """test runs the whole list, presets included; --rules-only only the user's."""
    write_conf("filters=obsidian\n")
    sql = add(store, "SQL", "S Q L")
    zb = add(store, "z.B.", "zum Beispiel", kind="literal", lang="de")
    code, out, _ = cli(capsys, "test", "**SQL** z.B.", "--lang", "de", "--json")
    data = json.loads(out)
    assert code == 0 and data["output"] == "S Q L zum Beispiel"
    assert [f["id"] for f in data["fired"]] == ["md-bold", sql["id"], zb["id"]]
    assert data["fired"][0]["group"] == "markdown" and data["fired"][1]["group"] == "user"
    code, out, _ = cli(capsys, "test", "**SQL** z.B.", "--rules-only")
    assert "after:  **S Q L** z.B." in out and "rules that fired" in out
    code, out, _ = cli(capsys, "test", "**SQL** z.B.", "--group", "markdown", "--json")
    assert json.loads(out)["output"] == "SQL z.B."


def test_cli_export_import_roundtrip(capsys, store, tmp_path):
    add(store, "a", "1")
    out_file = tmp_path / "export.json"
    assert cli(capsys, "export", "--out", str(out_file))[0] == 0
    other = tmp_path / "other" / "rules.json"
    code, out, _ = cli(capsys, "--file", str(other), "import", str(out_file), "--json")
    assert code == 0 and json.loads(out)["added"] == 1          # the presets are already there
    mine = [r for r in tts_rules.RuleStore(other).load() if r["group"] == "user"]
    assert [r["pattern"] for r in mine] == ["a"]


def test_cli_path_and_file_option(capsys, store, tmp_path):
    assert cli(capsys, "path")[1].strip() == str(store.path)
    f = tmp_path / "x" / "rules.json"
    assert cli(capsys, "path", "--file", str(f))[1].strip() == str(f)


def test_cli_migrates_on_first_use(capsys, store):
    store.path.with_name("replace.conf").write_text("a=>b\n", encoding="utf-8")
    code, out, _ = cli(capsys, "list", "--group", "user", "--json")
    assert json.loads(out)["rules"][0]["pattern"] == "a"


def test_cli_as_subprocess(isolated_dirs):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    script = str(BIN / "tts_rules.py")
    p = subprocess.run([sys.executable, script, "add", "°C", " Grad", "--kind", "literal", "--json"],
                       capture_output=True, text=True, encoding="utf-8", env=env)
    assert p.returncode == 0, p.stderr
    p = subprocess.run([sys.executable, script, "test", "20°C", "--json"],
                       capture_output=True, text=True, encoding="utf-8", env=env)
    assert json.loads(p.stdout)["output"] == "20 Grad"
    p = subprocess.run([sys.executable, "-m", "tts_rules", "list", "--group", "user", "--json"],
                       cwd=str(BIN),
                       capture_output=True, text=True, encoding="utf-8", env=env)
    assert p.returncode == 0 and len(json.loads(p.stdout)["rules"]) == 1


# --- Instructions for AI assistants ----------------------------------------- #
ROOT = BIN.parent
WIN_USER = "C:\\Users\\Jörg O'Neil"
WIN_PY = WIN_USER + "\\AppData\\Local\\piper-venv\\Scripts\\python.exe"
WIN_SCRIPT = WIN_USER + "\\AppData\\Local\\piper-bin\\tts_rules.py"
WIN_RULES = WIN_USER + "\\AppData\\Roaming\\piper\\rules.json"
LX_HOME = "/home/jörg o'neil"
LX_RULES = LX_HOME + "/.local/share/piper/rules.json"


@pytest.mark.parametrize("case, kw, want", [
    ("windows installed", dict(path=WIN_RULES, windows=True, python=WIN_PY, script=WIN_SCRIPT), {
        "powershell": "& 'C:\\Users\\Jörg O''Neil\\AppData\\Local\\piper-venv\\Scripts\\python.exe' "
                      "'C:\\Users\\Jörg O''Neil\\AppData\\Local\\piper-bin\\tts_rules.py' "
                      "--file 'C:\\Users\\Jörg O''Neil\\AppData\\Roaming\\piper\\rules.json'",
        "gitbash": "MSYS_NO_PATHCONV=1 "
                   "'C:/Users/Jörg O'\\''Neil/AppData/Local/piper-venv/Scripts/python.exe' "
                   "'C:/Users/Jörg O'\\''Neil/AppData/Local/piper-bin/tts_rules.py' "
                   "--file 'C:/Users/Jörg O'\\''Neil/AppData/Roaming/piper/rules.json'",
    }),
    ("windows dev, OneDrive", dict(
        path="C:\\Users\\jo\\AppData\\Roaming\\piper\\rules.json", windows=True,
        python="C:\\Users\\jo\\OneDrive - Contoso & Co\\tts\\.venv\\Scripts\\python.exe",
        script="C:\\Users\\jo\\OneDrive - Contoso & Co\\tts\\bin\\tts_rules.py"), {
        "powershell": "& 'C:\\Users\\jo\\OneDrive - Contoso & Co\\tts\\.venv\\Scripts\\python.exe' "
                      "'C:\\Users\\jo\\OneDrive - Contoso & Co\\tts\\bin\\tts_rules.py' "
                      "--file 'C:\\Users\\jo\\AppData\\Roaming\\piper\\rules.json'",
        "gitbash": "MSYS_NO_PATHCONV=1 'C:/Users/jo/OneDrive - Contoso & Co/tts/.venv/Scripts/python.exe' "
                   "'C:/Users/jo/OneDrive - Contoso & Co/tts/bin/tts_rules.py' "
                   "--file C:/Users/jo/AppData/Roaming/piper/rules.json",
    }),
    ("linux installed", dict(path=LX_RULES, windows=False,
                             python=LX_HOME + "/.local/share/piper-venv/bin/python",
                             script=LX_HOME + "/.local/bin/tts_rules.py",
                             wrapper=LX_HOME + "/.local/bin/tts-rules"), {
        "posix": "'/home/jörg o'\\''neil/.local/bin/tts-rules' "
                 "--file '/home/jörg o'\\''neil/.local/share/piper/rules.json'",
    }),
    ("linux dev", dict(path="/home/jo/.local/share/piper/rules.json", windows=False,
                       python="/usr/bin/python3", script="/home/jo/src/tts setup/bin/tts_rules.py",
                       wrapper=""), {
        "posix": "/usr/bin/python3 '/home/jo/src/tts setup/bin/tts_rules.py' "
                 "--file /home/jo/.local/share/piper/rules.json",
    }),
    ("linux PIPER_DIR", dict(path="/srv/my piper/rules.json", windows=False, python="/usr/bin/python3",
                             script="/opt/tts/tts_rules.py", wrapper="/opt/tts/tts-rules"), {
        "posix": "/opt/tts/tts-rules --file '/srv/my piper/rules.json'",
    }),
])
def test_run_commands_exact(case, kw, want):
    assert tts_rules.run_commands(**kw) == want


@pytest.mark.parametrize("windows", [True, False])
def test_agent_prompt_shows_only_this_os(windows):
    kw = (dict(path=WIN_RULES, python=WIN_PY, script=WIN_SCRIPT) if windows else
          dict(path=LX_RULES, python="/usr/bin/python3", script=LX_HOME + "/.local/bin/tts_rules.py",
               wrapper=LX_HOME + "/.local/bin/tts-rules"))
    text = tts_rules.agent_prompt(windows=windows, **kw)
    cmds = tts_rules.run_commands(windows=windows, **kw)
    for shell, line in cmds.items():
        title, quote = tts_rules.SHELLS[shell]
        assert f"{title}\n\n    {line} <command>\n" in text
        assert f"{title}\n\n    {line} doctor {quote('Grüße')}\n" in text
    assert f"(never edit it by hand): {kw['path']}\n" in text
    if windows:
        assert set(cmds) == {"powershell", "gitbash"}
        assert "on Windows." in text and "bash, zsh or fish" not in text and "In fish" not in text
        assert "'it''s'" in text and "import <file>" in text and "cmd.exe" in text
    else:
        assert set(cmds) == {"posix"}
        assert "on Linux." in text or "on macOS." in text
        for word in ("PowerShell", "Git Bash", "cmd.exe", "MSYS"):
            assert word not in text
    for word in ("doctor", "list", "test", "undo", "literal", "word", "regex", "--lang", "never edit",
                 'the last line is "ok"', "stop"):
        assert word in text
    assert "—" not in text and len(text.split()) < 1500
    assert text.index("## Step 1") < text.index("## Commands")


def test_agent_prompt_follows_platform_and_env(monkeypatch, tmp_path):
    """Defaults: the OS from sys.platform, the rules file from the same env
    the app reads (PIPER_DIR, APPDATA, HOME; XDG_* do not move it)."""
    import tts_platform
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(tts_platform, "IS_WINDOWS", True)
    monkeypatch.delenv("PIPER_DIR")
    roaming = tmp_path / "Jörg O'Neil" / "AppData" / "Roaming"
    monkeypatch.setenv("APPDATA", str(roaming))
    text = tts_rules.agent_prompt()
    assert "In PowerShell" in text
    assert f"--file {tts_rules.quote_ps(roaming / 'piper' / 'rules.json')}" in text

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(tts_platform, "IS_WINDOWS", False)
    home = tmp_path / "home with space"
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg config"))
    text = tts_rules.agent_prompt()
    want = home / ".local" / "share" / "piper" / "rules.json"
    assert "PowerShell" not in text and f"--file {tts_rules.quote_sh(want)} <command>" in text

    monkeypatch.setenv("PIPER_DIR", str(tmp_path / "my piper"))
    want = tmp_path / 'my piper' / 'rules.json'
    assert f"--file {tts_rules.quote_sh(want)} <command>" in tts_rules.agent_prompt()


def test_quote_ps_doubles_every_single_quote():
    assert tts_rules.quote_ps("O'Neil") == "'O''Neil'"
    assert tts_rules.quote_ps("O\u2019Neil \u2018x\u2019") == "'O\u2019\u2019Neil \u2018\u2018x\u2019\u2019'"
    assert tts_rules.quote_ps("$env:x `n") == "'$env:x `n'"


@pytest.mark.parametrize("s, want", [
    ("/usr/bin/python3", "/usr/bin/python3"),
    ("a b", "'a b'"),
    ("it's", "'it'\\''s'"),
    ("=x", "'=x'"),                  # zsh expands a leading =
    ("~/x", "'~/x'"),
    ("Grüße", "'Grüße'"),
    ("$HOME", "'$HOME'"),
])
def test_quote_sh(s, want):
    assert tts_rules.quote_sh(s) == want


def test_installed_wrapper_is_found_next_to_the_script(tmp_path):
    script = tmp_path / "tts_rules.py"
    script.write_text("")
    assert tts_rules.installed_wrapper(script) == ""
    (tmp_path / "tts-rules").write_text("")
    assert tts_rules.installed_wrapper(script) == str(tmp_path / "tts-rules")


def test_console_python_prefers_python_exe(tmp_path):
    (tmp_path / "pythonw.exe").write_text("")
    (tmp_path / "python.exe").write_text("")
    assert tts_rules.console_python(tmp_path / "pythonw.exe") == tmp_path / "python.exe"
    assert tts_rules.console_python(tmp_path / "python3") == tmp_path / "python3"


# --- Wrapper written by the install scripts ---------------------------------- #
def test_wrapper_text_windows_is_relative_and_ascii():
    local = "C:\\Users\\Jörg O'Neil 100%\\AppData\\Local"
    text = tts_rules.wrapper_text(local + "\\piper-bin\\tts-rules.cmd", True,
                                  local + "\\piper-venv\\Scripts\\python.exe",
                                  local + "\\piper-bin\\tts_rules.py")
    assert text == '@"%~dp0..\\piper-venv\\Scripts\\python.exe" "%~dp0tts_rules.py" %*\r\n'
    cmd, script = "C:\\bin\\tts-rules.cmd", "C:\\bin\\tts_rules.py"
    text = tts_rules.wrapper_text(cmd, True, "D:\\py 100%\\python.exe", script)
    assert text == '@"D:\\py 100%%\\python.exe" "%~dp0tts_rules.py" %*\r\n'
    with pytest.raises(ValueError):
        tts_rules.wrapper_text(cmd, True, "D:\\Jörg\\python.exe", script)


def test_wrapper_text_posix_quotes_paths():
    text = tts_rules.wrapper_text(LX_HOME + "/.local/bin/tts-rules", False,
                                  LX_HOME + "/.local/share/piper-venv/bin/python",
                                  LX_HOME + "/.local/bin/tts_rules.py")
    assert text == ("#!/bin/sh\nexec '/home/jörg o'\\''neil/.local/share/piper-venv/bin/python' "
                    "'/home/jörg o'\\''neil/.local/bin/tts_rules.py' \"$@\"\n")


def test_cli_write_wrapper(capsys, tmp_path):
    dest = tmp_path / "Jörg O'Neil" / ("tts-rules.cmd" if os.name == "nt" else "tts-rules")
    assert cli(capsys, "write-wrapper", str(dest))[0] == 0
    data = dest.read_bytes()
    if os.name == "nt":
        assert data.isascii() and data.endswith(b"%*\r\n")
    else:
        assert data.startswith(b"#!/bin/sh\n") and os.access(dest, os.X_OK)


def test_install_scripts_write_the_wrapper_through_tts_rules():
    ps1 = (ROOT / "install.ps1").read_text(encoding="utf-8")
    sh = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "write-wrapper (Join-Path $BinDir 'tts-rules.cmd')" in ps1
    assert "'tts-rules.cmd') -Encoding" not in ps1
    assert '"$BIN/tts_rules.py" write-wrapper "$BIN/tts-rules"' in sh
    assert 'cat > "$BIN/tts-rules"' not in sh
    # install.sh writes the wrapper after it copied tts_rules.py
    assert sh.index('"$REPO/bin/tts_rules.py" "$BIN/"') < sh.index("write-wrapper")
    assert 'rm -f "$HOME/.local/bin/tts-rules"' in (ROOT / "uninstall.sh").read_text(encoding="utf-8")


# --- doctor, encodings, PowerShell 5.1 quirks --------------------------------- #
def test_cli_doctor(capsys, store, write_conf):
    store.path.with_name("replace.conf").write_text("a=>b\n", encoding="utf-8")
    code, out, _ = cli(capsys, "doctor", "Grüße")
    assert code == 0 and out.splitlines()[-1] == "ok"
    assert f"rules file:  {store.path} (not created yet" in out and "check text:  Grüße" in out
    assert not store.path.exists()                        # read-only, no migration
    code, out, _ = cli(capsys, "doctor", "GrÃ¼ÃŸe", "--json")
    data = json.loads(out)
    assert code == tts_rules.EXIT_ENCODING and data["ok"] is False and out.isascii()
    add(store, "a", "b")
    code, out, _ = cli(capsys, "doctor", "--json")
    assert code == 0 and json.loads(out)["rules"] == 1
    store.path.write_text("{broken", encoding="utf-8")
    code, out, _ = cli(capsys, "doctor")
    assert code == tts_rules.EXIT_STORE and out.splitlines()[-1] == "not ok"


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as exc:
        tts_rules.main(["--version"])
    assert exc.value.code == 0 and "tts-rules" in capsys.readouterr().out


def test_cli_empty_arguments_dropped_by_powershell_51(capsys, plain):
    """Windows PowerShell 5.1 drops '' before it starts Python, so
    add 'x' '' arrives as add x and edit --replacement '' as edit --replacement."""
    store = plain
    code, out, _ = cli(capsys, "add", "TODO: ", "--kind", "literal", "--json")
    rid = json.loads(out)["rule"]["id"]
    assert code == 0 and store.load()[0]["replacement"] == ""
    store.update(rid, {"replacement": "x"})
    assert cli(capsys, "edit", rid, "--replacement")[0] == 0
    assert store.load()[0]["replacement"] == ""


def test_cli_json_output_is_ascii(capsys, store):
    code, out, _ = cli(capsys, "add", "Grüße", "Grüsse", "--kind", "literal", "--json")
    assert code == 0 and out.isascii() and json.loads(out)["rule"]["pattern"] == "Grüße"
    code, out, _ = cli(capsys, "remove", "nöpe", "--json")
    assert code == tts_rules.EXIT_NOT_FOUND and out.isascii()


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16"])
def test_cli_import_reads_powershell_encodings(capsys, plain, tmp_path, encoding):
    store = plain
    f = tmp_path / "r.json"
    f.write_text(json.dumps([{"pattern": 'say "hi"', "replacement": "Grüße", "kind": "literal"}]),
                 encoding=encoding)
    assert cli(capsys, "import", str(f))[0] == 0
    assert store.load()[0]["pattern"] == 'say "hi"' and store.load()[0]["replacement"] == "Grüße"


def test_rules_file_in_another_encoding_is_a_store_error(store):
    store.path.write_bytes(b'{"rules": [{"pattern": "\xe4", "kind": "literal"}]}')
    with pytest.raises(tts_rules.StoreError):
        store.load()
    logs = []
    assert tts_rules.LiveRules(log=logs.append).apply("x") == "x"      # the daemon keeps reading
    assert any("not reloaded" in m for m in logs)


def test_text_that_cannot_be_saved_is_refused(store):
    with pytest.raises(tts_rules.RuleError) as exc:
        store.add({"pattern": "a\udcff", "replacement": "b"})
    assert exc.value.code == "text" and not store.path.exists()


def test_cli_prints_utf8_into_pipes(isolated_dirs):
    """Without PYTHONIOENCODING a Windows pipe would get the ANSI code page."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    script = str(BIN / "tts_rules.py")
    p = subprocess.run([sys.executable, script, "add", "Grüße", "x", "--kind", "literal"],
                       capture_output=True, env=env)
    assert p.returncode == 0 and "Grüße".encode() in p.stdout
    p = subprocess.run([sys.executable, script, "doctor", "Grüße"], capture_output=True, env=env)
    assert p.returncode == 0 and b"check text:  Gr\xc3\xbc\xc3\x9fe" in p.stdout


# --- The settings dialog shows what it copies ------------------------------- #
def test_settings_dialog_copies_the_shown_text():
    qml = (BIN / "tts_settings.qml").read_text(encoding="utf-8")
    dlg = qml[qml.index("id: aiDlg"):qml.index("// --- Snackbar")]
    assert "prompt = backend.agentPrompt()" in dlg
    assert "text: aiDlg.prompt" in dlg and "textFormat: TextEdit.PlainText" in dlg
    assert "backend.copyText(aiDlg.prompt)" in dlg
    py = (BIN / "tts_settings.py").read_text(encoding="utf-8")
    assert "return tts_rules.agent_prompt(self._rules.path)" in py


def test_settings_text_area_and_clipboard_keep_the_whole_prompt(store):
    """Qt round trip: the read-only TextArea shows every character of the
    prompt, and copyText puts all of it on the clipboard."""
    pytest.importorskip("PySide6.QtQuick")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QByteArray
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    app = QGuiApplication.instance() or QGuiApplication([])
    prompt = tts_rules.agent_prompt(store.path, windows=True)
    engine = QQmlEngine()
    comp = QQmlComponent(engine)
    comp.setData(QByteArray(b"import QtQuick\nimport QtQuick.Controls\n"
                            b"TextArea { readOnly: true; wrapMode: TextEdit.Wrap; "
                            b"textFormat: TextEdit.PlainText }"), "")
    area = comp.create()
    assert area is not None, comp.errors()
    area.setProperty("text", prompt)
    assert area.property("length") == len(prompt)
    assert area.getText(0, area.property("length")) == prompt
    import tts_settings
    tts_settings.Backend.copyText(None, prompt)
    assert app.clipboard().text() == prompt
