# -*- coding: utf-8 -*-
r"""
tts_rules: the text rules, applied before reading aloud.

A rule finds a piece of text and replaces it, so the voice says it right
("SQL" -> "S Q L", "z.B." -> "zum Beispiel", "**bold**" -> "bold"). All
rules live in one ordered list in rules.json in the config folder
(tts_platform.piper_dir()). The settings window and the command line below
write it; the daemon reads it and reloads it when the file changes.

The list starts with the presets the app ships (tts_filters.PRESETS), in the
groups obsidian, markdown and formulas; the user's own rules follow in the
group user. Presets are ordinary rules: they can be changed, switched off,
moved and deleted, and `restore` brings them back as shipped. Each group can
be switched off as a whole, which keeps the switches of its rules.

File format (version 1):

    {
      "version": 1,
      "about": "...",
      "presets": 1,
      "groups": {"obsidian": false, "markdown": true, "formulas": true, "user": true},
      "rules": [
        {"id": "md-bold", "order": 1, "enabled": true, "group": "markdown", "kind": "regex",
         "pattern": "...", "replacement": "\1\2", "lang": "all", "note": "...",
         "created_by": "app", ..., "preset_id": "md-bold", "preset_version": 1},
        {"id": "r3f9a1c", "order": 2, "enabled": true, "group": "user", "kind": "word",
         "pattern": "SQL", "replacement": "S Q L", "lang": "all", "note": "",
         "created_by": "user", "updated_by": "user", "reviewed": true,
         "created": "2026-10-10T08:00:00Z", "updated": "2026-10-10T08:00:00Z"}
      ]
    }

  kind      literal  the exact text, anywhere
            word     the exact text, not as part of a longer word
            regex    Python regular expression; the replacement may use \1 or \g<name>
            builtin  a code rule from tts_filters (field "builtin" names it);
                     pattern and replacement stay empty
  group     obsidian | markdown | formulas | user
  lang      all or a language code (tts_langs.SUPPORTED), the language of
            the text being read
  order     position, 1 = first; rules run top to bottom, each on the result
            of the one before
  reviewed  false while a rule added or changed by an AI assistant waits for
            the user's review in the settings window
  presets   the preset version the list was set up with; "groups" holds the
            group switches (a missing group is on)

Every write validates the rule, is atomic, and keeps the previous file as
rules.json.bak; `undo` swaps the two. migrate() sets the list up once: a
replace.conf from older versions is imported and renamed to
replace.conf.migrated, then the presets are added, switched on or off to match
the former filters= line in voices.conf, which is not read after that.

Command line: python tts_rules.py --help (also python -m tts_rules, and the
tts-rules wrapper the install scripts write with `write-wrapper`). `doctor`
checks that the tool runs in the current shell; `prompt` prints the
instructions for AI assistants with the exact command lines for this machine.
"""

import argparse
import contextlib
import difflib
import json
import ntpath
import os
import platform
import re
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import tts_filters
import tts_langs

VERSION = 1
FILE_NAME = "rules.json"
KINDS = ("literal", "word", "regex")           # what a user writes
CODE = "builtin"                               # code rule from tts_filters
LANGS = ("all",) + tts_langs.SUPPORTED
AUTHORS = ("user", "agent")
EDITABLE = ("enabled", "kind", "pattern", "replacement", "lang", "note")
GROUPS = tts_filters.GROUPS                    # preset groups, in list order
USER_GROUP = tts_filters.USER_GROUP
ALL_GROUPS = tts_filters.ALL_GROUPS
PRESET_FIELDS = ("kind", "pattern", "replacement", "lang", "note", "builtin")
ABOUT = ("Text rules of the read-aloud app, applied top to bottom before reading. The first ones "
         "come with the app (groups obsidian, markdown, formulas), the user's own rules follow "
         "(group user). Managed by the app: change them in the settings window (Text clean-up) "
         "or with the tts-rules command line, not by hand.")

# Exit codes of the command line
EXIT_OK, EXIT_INVALID, EXIT_USAGE, EXIT_NOT_FOUND, EXIT_STORE, EXIT_ENCODING = 0, 1, 2, 3, 4, 5


class RuleError(ValueError):
    """Invalid rule. code is stable (for translations), detail is free text."""

    def __init__(self, code, message, detail=""):
        super().__init__(message)
        self.code = code
        self.detail = detail


class NotFound(LookupError):
    pass


class StoreError(RuntimeError):
    """The rules file cannot be read or written."""


def default_path() -> Path:
    import tts_platform
    return tts_platform.piper_dir() / FILE_NAME


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
#  Code rules (tts_filters)
# --------------------------------------------------------------------------- #
_CODE_CACHE = {}


def code_rules(folder=None, log=None):
    """{name: function} of the code rules: tts_filters.BUILTINS plus the
    FILTERS of a tts_filters.py in folder (default: the config folder).
    Reloaded when that file changes."""
    if folder is None:
        import tts_platform
        folder = tts_platform.piper_dir()
    user = Path(folder) / "tts_filters.py"
    try:
        sig = user.stat().st_mtime_ns
    except OSError:
        sig = None
    key = (str(folder), sig)
    if key not in _CODE_CACHE:
        if len(_CODE_CACHE) > 8:
            _CODE_CACHE.clear()
        _CODE_CACHE[key] = tts_filters.registry(folder, log)
    return _CODE_CACHE[key]


def _code_fn(rule, reg=None):
    name = str(rule.get("builtin") or "")
    fn = (code_rules() if reg is None else reg).get(name)
    if fn is None:
        raise RuleError("builtin", f"there is no code rule '{name}'", name)
    return fn


# --------------------------------------------------------------------------- #
#  Validation and compiling
# --------------------------------------------------------------------------- #
def _regex_for(kind, pattern):
    if kind == "literal":
        return re.escape(pattern)
    if kind == "word":
        # Word boundaries only where the pattern starts or ends with a letter
        # or digit, so "SQL" skips "MySQL" while "°C" still matches "20°C".
        head = r"(?<!\w)" if re.match(r"\w", pattern[0]) else ""
        tail = r"(?!\w)" if re.match(r"\w", pattern[-1]) else ""
        return head + re.escape(pattern) + tail
    return pattern


def compile_rule(rule, reg=None):
    """Compiled pattern of a rule, None for a code rule; raises RuleError."""
    kind, pattern = rule.get("kind"), rule.get("pattern")
    if kind == CODE:
        _code_fn(rule, reg)
        return None
    if kind not in KINDS:
        raise RuleError("kind", f"kind must be one of {', '.join(KINDS)}")
    if not isinstance(pattern, str) or not pattern.strip():
        raise RuleError("empty", "the pattern must not be empty")
    try:
        rx = re.compile(_regex_for(kind, pattern))
    except re.error as exc:
        raise RuleError("regex", f"the regular expression does not compile: {exc}", str(exc)) from exc
    if rx.search("") is not None:
        raise RuleError("emptyMatch", "the pattern also matches empty text, so it would fire everywhere")
    if kind == "regex":
        try:
            rx.sub(rule.get("replacement", ""), "")      # compiles the template
        except (re.error, IndexError) as exc:
            raise RuleError("template", f"the replacement does not fit the pattern: {exc}",
                            str(exc)) from exc
    return rx


def validate(fields, reg=None):
    """Check and normalize the editable fields of a rule; raises RuleError.
    A code rule keeps its "builtin" name and has no pattern."""
    out = {
        "enabled": bool(fields.get("enabled", True)),
        "kind": str(fields.get("kind", "word")),
        "pattern": fields.get("pattern", ""),
        "replacement": fields.get("replacement", ""),
        "lang": str(fields.get("lang", "all") or "all"),
        "note": str(fields.get("note", "") or ""),
    }
    if out["kind"] == CODE:
        out["pattern"] = out["replacement"] = ""
        out["builtin"] = str(fields.get("builtin") or "")
    if not isinstance(out["pattern"], str):
        raise RuleError("empty", "the pattern must be text")
    if not isinstance(out["replacement"], str):
        raise RuleError("replacement", "the replacement must be text")
    if out["lang"] not in LANGS:
        raise RuleError("lang", f"lang must be one of {', '.join(LANGS)}")
    for key in ("pattern", "replacement", "note"):
        try:
            out[key].encode("utf-8")
        except UnicodeEncodeError as exc:         # undecodable bytes from the command line
            raise RuleError("text", f"the {key} holds characters that cannot be saved", str(exc)) from exc
    compile_rule(out, reg)
    return out


def check(fields, reg=None):
    """'' if the rule is valid, else the error message (for live validation)."""
    try:
        validate(fields, reg)
        return ""
    except RuleError as exc:
        return str(exc)


def preset_changes(rule):
    """Fields in which a preset rule differs from the shipped one; [] for an
    unchanged preset and for rules that are not presets."""
    p = tts_filters.PRESETS_BY_ID.get(rule.get("preset_id"))
    if p is None:
        return []
    return [k for k in PRESET_FIELDS if (rule.get(k) or "") != (p.get(k) or "")]


def group_state(rules):
    """{group: on} for the four groups and any other group in rules."""
    saved = getattr(rules, "meta", {}).get("groups") or {}
    names = list(ALL_GROUPS) + [r.get("group") for r in rules if r.get("group") not in ALL_GROUPS]
    return {g: bool(saved.get(g, True)) for g in dict.fromkeys(names)}


def is_active(rule, groups=None):
    """Switched on, and its group too."""
    return bool(rule.get("enabled", True)) and (groups or {}).get(rule.get("group", USER_GROUP), True)


class Compiled:
    __slots__ = ("rule", "rx", "repl", "fn")

    def __init__(self, rule, rx, fn=None):
        self.rule = rule
        self.rx = rx
        self.fn = fn                     # code rule: text -> text
        rep = rule.get("replacement", "")
        self.repl = rep if rule.get("kind") == "regex" else (lambda m, rep=rep: rep)

    def applies(self, lang):
        scope = self.rule.get("lang", "all")
        return scope == "all" or scope == lang

    def expand(self, m):
        return m.expand(self.repl) if isinstance(self.repl, str) else self.repl(m)

    def run(self, text):
        return self.fn(text) if self.fn is not None else self.rx.sub(self.repl, text)


def compile_all(rules, log=None, groups=None, reg=None):
    """Active rules that compile, in order. Broken ones are skipped and logged.
    groups: {group: on}; default: the group switches saved with rules."""
    groups = group_state(rules) if groups is None else groups
    out = []
    for r in rules:
        if not is_active(r, groups):
            continue
        try:
            if r.get("kind") == CODE:
                out.append(Compiled(r, None, _code_fn(r, reg)))
            else:
                out.append(Compiled(r, compile_rule(r)))
        except RuleError as exc:
            if log:
                log(f"rule {r.get('id', '?')} skipped: {exc}")
    return out


def apply(text, lang, compiled, log=None):
    """Run compiled rules over text. lang: a language code or None (only 'all' rules)."""
    for c in compiled:
        if not c.applies(lang):
            continue
        try:
            text = c.run(text)
        except Exception as exc:            # one rule must never stop reading
            if log:
                log(f"rule {c.rule.get('id', '?')} failed: {exc}")
    return text


def _runs(chars):
    """[(text, tag)] runs of equal tags; chars: [(char, tag)]."""
    out = []
    for ch, tag in chars:
        if out and out[-1]["rule"] == tag:
            out[-1]["text"] += ch
        else:
            out.append({"text": ch, "rule": tag})
    return out


def _fired(c, count):
    r = c.rule
    return {"id": r.get("id", ""), "count": count, "kind": r.get("kind", ""),
            "pattern": r.get("pattern", ""), "replacement": r.get("replacement", ""),
            "group": r.get("group", USER_GROUP), "preset_id": r.get("preset_id", ""),
            "builtin": r.get("builtin", ""), "note": r.get("note", "")}


def trace(text, lang, rules, groups=None, reg=None):
    """Like apply(), and also report what happened, for `test` and the
    settings window. Returns {output, fired, before, after, skipped}:
    before/after are runs [{text, rule}] where rule is the id that matched
    (before) or wrote (after) that text, '' elsewhere. A code rule marks the
    stretches it changed."""
    groups = group_state(rules) if groups is None else groups
    compiled = compile_all(rules, groups=groups, reg=reg)
    skipped = []
    for r in rules:
        if is_active(r, groups) and not any(c.rule is r for c in compiled):
            skipped.append({"id": r.get("id", ""), "error": check(r, reg)})
    chars = [(ch, i, "") for i, ch in enumerate(text)]     # char, origin, writer
    hit = [""] * len(text)
    fired = []
    for c in compiled:
        if not c.applies(lang):
            continue
        rid = c.rule.get("id", "")
        cur = "".join(ch for ch, _, _ in chars)
        new, count = [], 0
        if c.fn is not None:
            try:
                out = c.fn(cur)
            except Exception:
                continue
            ops = difflib.SequenceMatcher(None, cur, out, autojunk=False).get_opcodes()
            for op, s, e, s2, e2 in ops:
                if op == "equal":
                    new.extend(chars[s:e])
                    continue
                for _, origin, _ in chars[s:e]:
                    if origin >= 0 and not hit[origin]:
                        hit[origin] = rid
                new.extend((ch, -1, rid) for ch in out[s2:e2])
                count += 1
        else:
            pos = 0
            for m in c.rx.finditer(cur):
                s, e = m.span()
                new.extend(chars[pos:s])
                for _, origin, _ in chars[s:e]:
                    if origin >= 0 and not hit[origin]:
                        hit[origin] = rid
                new.extend((ch, -1, rid) for ch in c.expand(m))
                pos, count = e, count + 1
            new.extend(chars[pos:])
        if count:
            chars = new
            fired.append(_fired(c, count))
    return {
        "output": "".join(ch for ch, _, _ in chars),
        "fired": fired,
        "skipped": skipped,
        "before": _runs(zip(text, hit, strict=True)),
        "after": _runs((ch, w) for ch, _, w in chars),
    }


# --------------------------------------------------------------------------- #
#  The store
# --------------------------------------------------------------------------- #
class RuleList(list):
    """Rules in order; meta holds the other fields of the file (presets, groups)."""

    def __init__(self, items=(), meta=None):
        super().__init__(items)
        self.meta = dict(meta or {})


def _normalize(raw, index):
    """Fill defaults into a rule read from disk. Unknown fields stay, and
    invalid rules are kept (they are skipped when applied), so a load never
    loses data."""
    r = dict(raw)
    r["id"] = str(r.get("id") or f"r{uuid.uuid4().hex[:6]}")
    r.setdefault("enabled", True)
    if not isinstance(r.get("group"), str) or not r.get("group"):
        r["group"] = USER_GROUP
    r.setdefault("kind", "regex")
    r.setdefault("pattern", "")
    r.setdefault("replacement", "")
    r.setdefault("lang", "all")
    r.setdefault("note", "")
    r.setdefault("created_by", "user")
    r.setdefault("updated_by", r["created_by"])
    r.setdefault("reviewed", r["created_by"] != "agent")
    r.setdefault("created", "")
    r.setdefault("updated", r["created"])
    try:
        r["order"] = int(r.get("order", index + 1))
    except (TypeError, ValueError):
        r["order"] = index + 1
    return r


def parse(text):
    """Rules (a RuleList) from the text of a rules file or an export; raises StoreError."""
    try:
        data = json.loads(text) if text.strip() else {"rules": []}
    except ValueError as exc:
        raise StoreError(f"the rules file is not valid JSON ({exc})") from exc
    if isinstance(data, list):
        data = {"rules": data}
    if not isinstance(data, dict) or not isinstance(data.get("rules", []), list):
        raise StoreError("the rules file has no 'rules' list")
    if int(data.get("version", VERSION) or VERSION) > VERSION:
        raise StoreError(f"the rules file is from a newer version ({data.get('version')})")
    rules = [_normalize(r, i) for i, r in enumerate(data.get("rules", [])) if isinstance(r, dict)]
    rules.sort(key=lambda r: r["order"])                 # stable: ties keep file order
    meta = {k: v for k, v in data.items() if k not in ("version", "about", "rules")}
    if not isinstance(meta.get("groups", {}), dict):
        meta.pop("groups")
    return RuleList(rules, meta)


FIELD_ORDER = ("id", "order", "enabled", "group", "kind", "pattern", "replacement", "lang", "note",
               "created_by", "updated_by", "reviewed", "created", "updated")
META_ORDER = ("presets", "groups")


def dump(rules):
    """File text; order is renumbered from the list position."""
    out = []
    for i, r in enumerate(rules):
        r["order"] = i + 1
        known = {k: r[k] for k in FIELD_ORDER if k in r}
        out.append({**known, **{k: v for k, v in r.items() if k not in known}})
    meta = getattr(rules, "meta", {})
    head = {k: meta[k] for k in META_ORDER if k in meta}
    head.update({k: v for k, v in meta.items() if k not in head})
    return json.dumps({"version": VERSION, "about": ABOUT, **head, "rules": out},
                      ensure_ascii=False, indent=2) + "\n"


def read_text(path):
    """Text of a rules or export file: UTF-8 with or without BOM, or UTF-16
    with BOM (Windows PowerShell 5.1 writes both). Raises StoreError."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise StoreError(f"cannot read {path}: {exc}") from exc
    try:
        if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
            return data.decode("utf-16")
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise StoreError(f"{path} is not UTF-8 text ({exc})") from exc


def _atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        for _ in range(40):            # Windows: a reader may hold the file briefly
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                time.sleep(0.05)
        raise StoreError(f"cannot replace {path}")
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def preset_rule(preset_id, enabled=True):
    """A fresh copy of a shipped preset, as it goes into the list."""
    p = tts_filters.PRESETS_BY_ID[preset_id]
    now = _now()
    return {"id": preset_id, "enabled": enabled, **p, "created_by": "app", "updated_by": "app",
            "reviewed": True, "created": now, "updated": now}


def missing_presets(rules, group=None):
    """Ids of shipped presets (of one group) that are not in the list."""
    have = {r.get("preset_id") for r in rules}
    return [p["preset_id"] for p in tts_filters.PRESETS
            if p["preset_id"] not in have and group in (None, p["group"])]


def _preset_slot(rules, preset_id):
    """Where a deleted preset goes back: after the nearest earlier preset in
    the list, else before the nearest later one, else first."""
    order = [p["preset_id"] for p in tts_filters.PRESETS]
    pos = {r.get("preset_id"): i for i, r in enumerate(rules) if r.get("preset_id")}
    k = order.index(preset_id)
    for pid in reversed(order[:k]):
        if pid in pos:
            return pos[pid] + 1
    for pid in order[k + 1:]:
        if pid in pos:
            return pos[pid]
    return 0


def _group_slot(rules, group):
    """Position for a new rule at the end of a group."""
    last = [i for i, r in enumerate(rules) if r.get("group") == group]
    if last:
        return last[-1] + 1
    if group in GROUPS:                           # empty preset group: before later groups
        later = GROUPS[GROUPS.index(group) + 1:] + (USER_GROUP,)
        for i, r in enumerate(rules):
            if r.get("group") in later:
                return i
    return len(rules)


class RuleStore:
    """Reads and writes rules.json. Every change runs under a lock file,
    validates, writes atomically and keeps the previous file as .bak."""

    def __init__(self, path=None):
        self.path = Path(path) if path else default_path()

    @property
    def backup_path(self):
        return self.path.with_name(self.path.name + ".bak")

    def code_rules(self, log=None):
        return code_rules(self.path.parent, log)

    def signature(self):
        """Changes whenever the file is replaced or edited (for reloading)."""
        try:
            st = self.path.stat()
            return (st.st_mtime_ns, st.st_size, st.st_ino)
        except OSError:
            return None

    def load(self):
        if not self.path.exists():
            return RuleList()
        return parse(read_text(self.path))

    def can_undo(self):
        return self.backup_path.exists()

    @contextlib.contextmanager
    def _locked(self, timeout=5.0):
        lock = self.path.with_name(self.path.name + ".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.monotonic()
        while True:
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:                                   # left behind by a crash
                    if time.time() - lock.stat().st_mtime > 10:
                        lock.unlink()
                        continue
                except OSError:
                    pass
                if time.monotonic() - t0 > timeout:
                    raise StoreError("the rules file is busy, try again") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            os.close(fd)
            with contextlib.suppress(OSError):
                lock.unlink()

    def _write(self, rules):
        if self.path.exists():
            _atomic_write(self.backup_path, read_text(self.path))
        _atomic_write(self.path, dump(rules))

    @staticmethod
    def _state(rules):
        return json.dumps([rules, rules.meta], sort_keys=True)

    @contextlib.contextmanager
    def edit(self):
        """with store.edit() as rules: change the list (and rules.meta) in
        place; it is saved at the end unless an exception leaves the block."""
        with self._locked():
            rules = self.load()
            before = self._state(rules)
            yield rules
            if self._state(rules) != before:
                self._write(rules)

    @staticmethod
    def _find(rules, rid):
        for i, r in enumerate(rules):
            if r["id"] == rid:
                return i
        raise NotFound(f"no rule with id '{rid}'")

    @staticmethod
    def _touch(rule, by):
        if by not in AUTHORS:
            raise RuleError("by", "by must be 'user' or 'agent'")
        rule["updated"] = _now()
        rule["updated_by"] = by
        rule["reviewed"] = by == "user"

    @staticmethod
    def _new_id(rules):
        taken = {r["id"] for r in rules}
        while True:
            rid = "r" + uuid.uuid4().hex[:6]
            if rid not in taken:
                return rid

    @staticmethod
    def _check_group(rules, group):
        if group not in group_state(rules):
            raise RuleError("group", f"group must be one of {', '.join(group_state(rules))}", str(group))

    # --- Changes ----------------------------------------------------------- #
    def add(self, fields, by="user", at=None, group=None):
        """Add a rule (at: 1-based position, default the end of its group,
        group default user); returns it."""
        clean = validate(fields, self.code_rules())
        group = group or USER_GROUP
        with self.edit() as rules:
            self._check_group(rules, group)
            rule = {"id": self._new_id(rules), **clean, "group": group, "created_by": by,
                    "created": _now()}
            self._touch(rule, by)
            pos = (_group_slot(rules, group) if at is None
                   else max(0, min(len(rules), int(at) - 1)))
            rules.insert(pos, rule)
        return rule

    def update(self, rid, fields, by="user"):
        """Change editable fields. A code rule only takes enabled, lang and note."""
        with self.edit() as rules:
            i = self._find(rules, rid)
            given = {k: v for k, v in fields.items() if k in EDITABLE and v is not None}
            if rules[i].get("kind") == CODE and any(
                    k in given and given[k] != rules[i].get(k) for k in ("kind", "pattern", "replacement")):
                raise RuleError("codeFixed", "a code rule has no pattern to edit; switch it on or off, "
                                             "or change its note or language")
            merged = {k: rules[i].get(k) for k in EDITABLE}
            merged["builtin"] = rules[i].get("builtin")
            merged.update(given)
            rules[i].update(validate(merged, self.code_rules()))
            self._touch(rules[i], by)
            return dict(rules[i])

    def set_enabled(self, rid, on, by="user"):
        with self.edit() as rules:
            i = self._find(rules, rid)
            if bool(rules[i].get("enabled", True)) != bool(on):
                if on:
                    validate({**rules[i], "enabled": True}, self.code_rules())
                rules[i]["enabled"] = bool(on)
                self._touch(rules[i], by)
            return dict(rules[i])

    def set_group(self, group, on):
        """Switch a whole group on or off; the rules keep their own switches.
        Returns {group: on} for all groups."""
        with self.edit() as rules:
            self._check_group(rules, group)
            groups = dict(rules.meta.get("groups") or {})
            groups[group] = bool(on)
            rules.meta["groups"] = groups
            return group_state(rules)

    def remove(self, rid):
        with self.edit() as rules:
            return rules.pop(self._find(rules, rid))

    def move(self, rid, to):
        """Move a rule to 1-based position to (clamped); returns the new position."""
        with self.edit() as rules:
            rule = rules.pop(self._find(rules, rid))
            pos = max(0, min(len(rules), int(to) - 1))
            rules.insert(pos, rule)
            return pos + 1

    def review(self, rid):
        """The user looked at a rule from an AI assistant and keeps it."""
        with self.edit() as rules:
            i = self._find(rules, rid)
            rules[i]["reviewed"] = True
            return dict(rules[i])

    @staticmethod
    def _reset(rule, by):
        p = tts_filters.PRESETS_BY_ID[rule["preset_id"]]
        for k in PRESET_FIELDS:
            if k in p:
                rule[k] = p[k]
            else:
                rule.pop(k, None)
        rule["preset_version"] = p["preset_version"]
        RuleStore._touch(rule, by)

    def reset(self, rid, by="user"):
        """Put a preset back to its shipped pattern, replacement, language and
        note; it keeps its switch and position."""
        with self.edit() as rules:
            i = self._find(rules, rid)
            if rules[i].get("preset_id") not in tts_filters.PRESETS_BY_ID:
                raise RuleError("notPreset", f"rule '{rid}' did not come with the app, so it has "
                                             "no original to go back to")
            self._reset(rules[i], by)
            return dict(rules[i])

    def restore(self, group=None, by="user"):
        """Add deleted presets back and reset changed ones, for one group or
        all. Restoring one group also switches that group on. Returns
        {"added": [ids], "reset": [ids]}."""
        with self.edit() as rules:
            if group is not None and group not in GROUPS:
                raise RuleError("group", f"group must be one of {', '.join(GROUPS)}", str(group))
            done = {"added": [], "reset": []}
            for r in rules:
                p = tts_filters.PRESETS_BY_ID.get(r.get("preset_id"))
                if p and group in (None, p["group"]) and preset_changes(r):
                    self._reset(r, by)
                    done["reset"].append(r["id"])
            taken = {r["id"] for r in rules}
            for pid in missing_presets(rules, group):
                rule = preset_rule(pid)
                if pid in taken:
                    rule["id"] = self._new_id(rules)
                rules.insert(_preset_slot(rules, pid), rule)
                done["added"].append(rule["id"])
            if group is not None:
                groups = dict(rules.meta.get("groups") or {})
                groups[group] = True
                rules.meta["groups"] = groups
            return done

    def import_rules(self, items, by="agent", replace_all=False):
        """Add rules from an export; all or nothing. Rules equal to an existing
        one (kind, pattern, replacement, lang, code) are skipped. Group and
        preset fields of the items are kept. Returns (added, skipped)."""
        reg = self.code_rules()
        cleaned = []
        for n, item in enumerate(items, 1):
            try:
                c = validate(item, reg)
            except RuleError as exc:
                raise RuleError(exc.code, f"rule {n}: {exc}", exc.detail) from exc
            group = item.get("group")
            c["group"] = group if group in ALL_GROUPS else USER_GROUP
            if item.get("preset_id") in tts_filters.PRESETS_BY_ID:
                c["preset_id"] = item["preset_id"]
                c["preset_version"] = item.get("preset_version", tts_filters.PRESET_VERSION)
            cleaned.append(c)
        added = skipped = 0
        with self.edit() as rules:
            if replace_all:
                rules.clear()
            seen = {_key(r) for r in rules}
            for c in cleaned:
                if _key(c) in seen:
                    skipped += 1
                    continue
                rule = {"id": self._new_id(rules), **c, "created_by": by, "created": _now()}
                self._touch(rule, by)
                rules.append(rule)
                seen.add(_key(c))
                added += 1
        return added, skipped

    def undo(self):
        """Swap the file with its backup (a second undo redoes). False if
        there is nothing to undo."""
        with self._locked():
            if not self.backup_path.exists():
                return False
            old = read_text(self.backup_path)
            parse(old)                                      # never restore garbage
            cur = read_text(self.path) if self.path.exists() else ""
            _atomic_write(self.path, old)
            _atomic_write(self.backup_path, cur or dump([]))
            return True


def _key(r):
    return (r.get("kind"), r.get("pattern"), r.get("replacement"), r.get("lang", "all"),
            r.get("builtin", ""))


# --------------------------------------------------------------------------- #
#  The pipeline for the daemon (reloads on change)
# --------------------------------------------------------------------------- #
class LiveRules:
    """Compiled rules that follow the file: every apply() checks the file
    signature (one stat call) and reloads when it changed. A broken file keeps
    the last good rules. This is the whole text clean-up of the daemon."""

    def __init__(self, path=None, log=None):
        self.store = RuleStore(path)
        self.log = log or (lambda msg: None)
        self._sig = object()
        self._compiled = []

    def current(self):
        sig = self.store.signature()
        if sig != self._sig:
            self._sig = sig
            try:
                rules = self.store.load()
            except StoreError as exc:
                self.log(f"rules not reloaded: {exc}")
                return self._compiled
            self._compiled = compile_all(rules, self.log, reg=self.store.code_rules(self.log))
            if sig is not None:
                off = [g for g, on in group_state(rules).items() if not on]
                self.log(f"rules loaded: {len(self._compiled)} active of {len(rules)}"
                         + (f" (groups off: {', '.join(off)})" if off else ""))
        return self._compiled

    def apply(self, text, lang=None):
        return apply(text, lang, self.current(), self.log)


Pipeline = LiveRules


# --------------------------------------------------------------------------- #
#  Setting up the list: replace.conf and the presets
# --------------------------------------------------------------------------- #
def _replace_filter_on(folder):
    names = legacy_filters(folder)
    return "replace" in (names or [])


def legacy_filters(folder):
    """Names from the former filters= line in <folder>/voices.conf, or None
    when there is no such line (a new setup)."""
    try:
        import tts_config
        values = tts_config.read(Path(folder) / "voices.conf")
    except Exception:
        return None
    if "filters" not in values:
        return None
    return [n.strip() for n in values["filters"].split(",") if n.strip()]


def legacy_layout(names):
    """(groups, layout) for the former filters= names: groups {group: on},
    layout the order of the list as groups, "code:<name>" for an own filter
    from tts_filters.py, and USER for the rules already in the file.
    None (no filters= line): Markdown and formulas on, Obsidian off.
    'replace' marked where the rules ran; groups switched on after it stay
    after the user's rules, so the text sounds as before."""
    if names is None:
        return {"obsidian": False, "markdown": True, "formulas": True, USER_GROUP: True}, \
            [*GROUPS, USER_GROUP]
    before, after, on = [], [], set()
    side = before
    for n in names:
        if n == "replace":
            side = after
        elif n in tts_filters.LEGACY_FILTERS:
            side.extend(tts_filters.LEGACY_FILTERS[n])
            on.update(tts_filters.LEGACY_FILTERS[n])
        elif f"code:{n}" not in before + after:
            side.append(f"code:{n}")
    late = [g for g in GROUPS if g in after and g not in before]
    layout = ([g for g in GROUPS if g not in late] + [x for x in before if x.startswith("code:")]
              + [USER_GROUP] + late + [x for x in after if x.startswith("code:")])
    groups = {g: g in on for g in GROUPS}
    groups[USER_GROUP] = True
    return groups, layout


def migrate_presets(store=None, log=None):
    """Put the presets into the list once, in front of the rules already
    there, and switch their groups to match the former filters= line (see
    legacy_layout). Own filters from tts_filters.py named in filters= become
    code rules. Idempotent: does nothing once the file records "presets".
    The previous file stays as rules.json.bak. Returns the group switches, or
    None if there was nothing to do or the file is unreadable."""
    store = store or RuleStore()
    log = log or (lambda msg: None)
    try:
        with store.edit() as rules:
            if "presets" in rules.meta:
                return None
            names = legacy_filters(store.path.parent)
            groups, layout = legacy_layout(names)
            own = list(rules)
            taken = {r["id"] for r in own}
            new = []
            for seg in layout:
                if seg == USER_GROUP:
                    new.extend(own)
                elif seg.startswith("code:"):
                    now = _now()
                    new.append({"id": RuleStore._new_id(own + new), "enabled": True,
                                "group": USER_GROUP, "kind": CODE, "builtin": seg[5:],
                                "pattern": "", "replacement": "", "lang": "all",
                                "note": "Own filter from tts_filters.py, formerly in filters=",
                                "created_by": "user", "updated_by": "user", "reviewed": True,
                                "created": now, "updated": now})
                else:
                    new.extend(preset_rule(p["preset_id"]) for p in tts_filters.PRESETS
                               if p["group"] == seg and p["preset_id"] not in taken)
            rules[:] = new
            rules.meta["presets"] = tts_filters.PRESET_VERSION
            rules.meta["groups"] = {**groups, **(rules.meta.get("groups") or {})}
        on = [g for g in GROUPS if groups[g]]
        log(f"presets added to {store.path.name} (filters="
            f"{'(none)' if names is None else ','.join(names)}; groups on: {', '.join(on) or 'none'})")
        return groups
    except (OSError, StoreError) as exc:
        log(f"presets not added: {exc}")
        return None


def parse_replace_conf(text):
    """[(pattern, replacement)] from replace.conf lines 'pattern=>replacement'."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=>" not in s:
            continue
        pat, rep = s.split("=>", 1)
        if pat.strip():
            out.append((pat.strip(), rep.strip()))
    return out


def migrate_replace_conf(store=None, log=None):
    """Import <folder>/replace.conf into rules.json once and rename it to
    replace.conf.migrated. The rules are on only if the 'replace' filter was
    on, so reading sounds the same as before. Patterns that do not compile
    are kept, switched off. Returns the number of imported rules, or None if
    there was nothing to do or the import failed (replace.conf then stays)."""
    store = store or RuleStore()
    conf = store.path.with_name("replace.conf")
    if not conf.exists():
        return None
    log = log or (lambda msg: None)
    try:
        pairs = parse_replace_conf(conf.read_text(encoding="utf-8", errors="replace"))
        on = _replace_filter_on(store.path.parent)
        added = 0
        with store.edit() as rules:
            seen = {_key(r) for r in rules}
            for pat, rep in pairs:
                plain = re.escape(pat) == pat and "\\" not in rep
                rule = {"kind": "literal" if plain else "regex", "pattern": pat, "group": USER_GROUP,
                        "replacement": rep, "lang": "all", "enabled": on,
                        "note": "Imported from replace.conf"}
                try:
                    compile_rule(rule)
                except RuleError as exc:
                    rule["enabled"] = False
                    rule["note"] += f" (does not work as written: {exc})"
                if _key(rule) in seen:
                    continue
                seen.add(_key(rule))
                rule.update(id=RuleStore._new_id(rules), created_by="user", updated_by="user",
                            reviewed=True, created=_now(), updated=_now())
                rules.append(rule)
                added += 1
        target = conf.with_name(conf.name + ".migrated")
        n = 1
        while target.exists():
            target = conf.with_name(f"{conf.name}.migrated.{n}")
            n += 1
        conf.rename(target)
        log(f"replace.conf imported into {store.path.name} ({added} rules), kept as {target.name}")
        return added
    except (OSError, StoreError) as exc:
        log(f"replace.conf not imported: {exc}")
        return None


def migrate(store=None, log=None):
    """Everything that sets up the list once, in this order: replace.conf,
    then the presets in front of the rules. The daemon, the settings window
    and the command line call it at start."""
    store = store or RuleStore()
    migrate_replace_conf(store, log)
    migrate_presets(store, log)


# --------------------------------------------------------------------------- #
#  Instructions for AI coding assistants
# --------------------------------------------------------------------------- #
CHECK_TEXT = "Grüße"          # doctor compares it, to show the shell keeps non-ASCII text


def console_python(exe=None):
    """Interpreter that prints to a console (python.exe next to pythonw.exe)."""
    p = Path(exe or sys.executable)
    if p.name.lower() == "pythonw.exe" and (p.parent / "python.exe").exists():
        return p.parent / "python.exe"
    return p


def installed_wrapper(script=None):
    """The tts-rules shell script install.sh puts next to tts_rules.py, or ''.
    Windows has tts-rules.cmd there, but cmd.exe parses its arguments a second
    time (| and & run commands, ^ and % change), so the instructions call
    Python directly on Windows."""
    script = str(script or Path(__file__).resolve())
    wrapper = re.sub(r"[^/\\]*$", "tts-rules", script, count=1)
    return wrapper if os.path.isfile(wrapper) else ""


def quote_ps(s):
    """Single-quoted PowerShell string. PowerShell also reads the typographic
    single quotes as quotes, so all of them are doubled."""
    return "'" + re.sub("(['\u2018\u2019\u201a\u201b])", r"\1\1", str(s)) + "'"


_SH_PLAIN = re.compile(r"[\w@+:,./-]+", re.ASCII)


def quote_sh(s):
    """Quoted word for bash, zsh, fish and Git Bash. A quote inside becomes
    '\\'', which all four read the same way."""
    s = str(s)
    if _SH_PLAIN.fullmatch(s):
        return s
    return "'" + s.replace("'", "'\\''") + "'"


def run_commands(path=None, windows=None, python=None, script=None, wrapper=None):
    """{shell: command} that runs the command line on this machine; the
    subcommand goes after it. --file is always given, so a shell whose
    environment differs from the app's (PIPER_DIR, APPDATA, HOME) still uses
    the app's rules file. wrapper: path of the tts-rules script, '' for none,
    None to look next to the script."""
    windows = sys.platform.startswith("win") if windows is None else windows
    path = str(path or default_path())
    py = str(python or console_python())
    script = str(script or Path(__file__).resolve())
    if windows:
        def fwd(p):
            return p.replace("\\", "/")
        return {
            "powershell": f"& {quote_ps(py)} {quote_ps(script)} --file {quote_ps(path)}",
            # Without MSYS_NO_PATHCONV, Git Bash turns arguments such as /s into S:/
            "gitbash": (f"MSYS_NO_PATHCONV=1 {quote_sh(fwd(py))} {quote_sh(fwd(script))} "
                        f"--file {quote_sh(fwd(path))}"),
        }
    wrapper = installed_wrapper(script) if wrapper is None else str(wrapper)
    head = quote_sh(wrapper) if wrapper else f"{quote_sh(py)} {quote_sh(script)}"
    return {"posix": f"{head} --file {quote_sh(path)}"}


SHELLS = {
    "powershell": ("In PowerShell (Windows PowerShell 5.1 or PowerShell 7):", quote_ps),
    "gitbash": ("In Git Bash:", quote_sh),
    "posix": ("In bash, zsh or fish:", quote_sh),
}

PROMPT = r"""# Text rules for the read-aloud app on this computer

You are helping the user with Read Aloud (tts-setup), a local text-to-speech app on @SYSTEM@. Before it reads text aloud, the app runs the text through one list of rules, top to bottom, each on the result of the one before. A rule finds a piece of text and replaces it, so the voice says it right. Examples: "SQL" read as "S Q L", "z.B." read as "zum Beispiel". Rules take effect on the next read; nothing needs a restart.

The list is split into groups, in this order:

- obsidian: presets for Obsidian syntax, such as [[Page|Name]] links, ![[embeds]], #tags and %% comments.
- markdown: presets that remove Markdown marks such as **, #, > and table pipes, and read formulas between $ signs.
- formulas: a code rule that reads copied LaTeX and math symbols as German words.
- user: the user's own rules. This is where your rules go.

Presets come with the app. The user may have changed, switched off or deleted some of them; restore brings them back. Each group also has its own switch, which turns all its rules off at once.

## How to run the tool

Change rules only with the tts-rules command line tool. On this computer it runs like this, with one of the commands below in place of <command>:

@HOW@

Rules file, for reference only (never edit it by hand): @PATH@

## Step 1: check that the tool works

Before you change anything, run:

@DOCTOR@

Go on only if the exit code is 0 and the last line is "ok". Otherwise, or if the command is not found, stop: show the user the command you ran and its full output. Do not edit the rules file by hand and do not try other ways to change the rules.

The line "check text" should read Grüße. If it shows other characters (for example GrÃ¼ÃŸe or Gr├╝├ƒe), this shell displays non-ASCII output wrongly, while your input still arrives intact. Then add --json to every command: JSON output writes such characters as \u escapes, which stay exact.

## Quoting and exit codes

Put every pattern and replacement in single quotes, so the shell leaves backslashes and $ alone. @QUOTING@ If a pattern starts with "-", put options such as --kind first and write -- before the pattern.

Add --json to any command for machine-readable output. Exit codes: 0 ok, 1 invalid rule, 2 bad arguments, 3 no rule with that id, 4 rules file busy or unreadable, 5 the shell changed non-ASCII text on the way in.

## Commands

    doctor 'Grüße'                     checks the tool and shows the rules file it uses
    list [--group G] [--json]          all rules in order under their group, with id and whether the group is on
    show <id>
    test '<text>' [--lang <code>]      text before and after, and which rules fired, presets included
                                       (without --lang only rules with lang all run; --group G runs one group)
    add '<pattern>' '<replacement>' [--kind literal|word|regex] [--lang all|<code>] [--note '<why>']
                                       adds to the end of the user group
    edit <id> [--pattern P] [--replacement R] [--kind K] [--lang L] [--note N]
    enable <id> | disable <id>
    enable --group G | disable --group G   switches a whole group: obsidian, markdown, formulas or user
    move <id> --to <position>          1 is the first rule of the whole list
    remove <id>
    reset <id>                         puts a changed preset back to how it came with the app
    restore [--group G | --all]        adds deleted presets back and resets changed ones
    undo                               restores the rules as they were before the last change (one step)
    export [--out <file>] | import <file>
    path                               prints where the rules file is

## What a rule holds

- id: short id the tool assigns, for example r3f9a1c. Presets have readable ids such as md-bold.
- group: obsidian, markdown, formulas or user.
- kind: literal (the exact text, anywhere), word (the exact text, but not inside a longer word; the default), or regex (Python regular expression; the replacement may use \1 or \g<name>). Matching is case-sensitive; in a regex, (?i) ignores case.
- pattern, replacement: what to find and what the voice should read instead. An empty replacement ('') drops the text.
- lang: all, or a language code such as de, en or fr. A rule with a language code only runs when the text is read in that language.
- note: one short sentence on why the rule exists.
- preset_id: set on presets; list --json shows in "changed" which fields the user changed.
- kind builtin: a code rule (dollar-math, latex). It has no pattern; it can be switched, moved or removed, not edited.
- enabled, order, created_by (user, agent or app), reviewed, created, updated: kept by the tool.

## Examples

Each line goes after the command from "How to run the tool".

    add 'SQL' 'S Q L' --kind word --note 'Spell out the acronym'
    add 'z.B.' 'zum Beispiel' --kind literal --lang de --note 'German abbreviation'
    add 'e.g.' 'for example' --kind literal --lang en
    add '(\d+) ?km/h' '\1 Kilometer pro Stunde' --kind regex --lang de --note 'Speed unit'
    test 'Mit SQL fahren wir z.B. 50 km/h.' --lang de

## How to work

1. Run doctor first (step 1), then list, so you do not add a rule that already exists.
2. Run test with a realistic sentence before you change anything, to see what the voice reads now and which presets already act on it.
3. Make the smallest rule that fixes the problem: prefer word or literal over regex, set --lang when the fix belongs to one language, and add a --note.
4. Run test again with the same sentence, and with a sentence where the rule must not fire.
5. If the result is wrong, run undo once, or edit or remove the rule.
6. Add your rules to the user group (the default of add). Do not change, switch off, reset or remove presets or whole groups, and do not change rules you did not add, unless the user asks. If a preset gets in the way, tell the user its id and what it does.
7. Tell the user what you changed: the id, the pattern, the replacement and why.

Rules you add or change are marked as coming from an AI assistant. The user reviews them in the app under Settings, Text clean-up, and keeps or changes them there.
"""

QUOTING = {
    True: ("To write a single quote inside, double it in PowerShell ('it''s') and write '\\'' "
           "in Git Bash ('it'\\''s'). Windows PowerShell 5.1 breaks arguments that contain a "
           "double quote; for such a rule, write a UTF-8 JSON file such as "
           '[{"pattern": "say \\"hi\\"", "replacement": "say hi", "kind": "literal"}] '
           "and run import <file>. Do not use cmd.exe: it changes characters such as | & ^ % "
           "in arguments."),
    False: ("To write a single quote inside, write '\\'' ('it'\\''s'). In fish, \\\\ inside "
            "single quotes is one backslash."),
}


def agent_prompt(path=None, windows=None, python=None, script=None, wrapper=None):
    """English instructions for an AI coding assistant on this machine. The
    arguments default to this process (see run_commands)."""
    windows = sys.platform.startswith("win") if windows is None else windows
    path = str(path or default_path())
    cmds = run_commands(path, windows, python, script, wrapper)
    how = "\n\n".join(f"{SHELLS[k][0]}\n\n    {c} <command>" for k, c in cmds.items())
    doctor = "\n\n".join(f"{SHELLS[k][0]}\n\n    {c} doctor {SHELLS[k][1](CHECK_TEXT)}"
                         for k, c in cmds.items())
    if windows:
        how += "\n\nUse the line for the shell you run commands in."
    system = "Windows" if windows else ("macOS" if sys.platform == "darwin" else "Linux")
    text = PROMPT
    for key, value in (("@SYSTEM@", system), ("@HOW@", how), ("@PATH@", path),
                       ("@DOCTOR@", doctor), ("@QUOTING@", QUOTING[bool(windows)])):
        text = text.replace(key, value)
    return text


def wrapper_text(dest, windows=None, python=None, script=None):
    """Content of the tts-rules wrapper the install scripts write to dest.
    Windows: a .cmd with paths relative to its own folder (%~dp0), since cmd
    reads the file in the console code page and an absolute path with non-ASCII
    letters would break. Elsewhere: a sh script with quoted absolute paths."""
    windows = sys.platform.startswith("win") if windows is None else windows
    py = str(python or console_python())
    script = str(script or Path(__file__).resolve())
    if not windows:
        return f"#!/bin/sh\nexec {quote_sh(py)} {quote_sh(script)} \"$@\"\n"
    base = ntpath.dirname(str(dest))

    def rel(p):
        try:
            r, head = ntpath.relpath(p, base), "%~dp0"
        except ValueError:                       # another drive
            r, head = p, ""
        if not r.isascii():
            raise ValueError(f"{r} has non-ASCII letters, cmd.exe cannot run it from a .cmd file")
        return head + r.replace("%", "%%")
    return f'@"{rel(py)}" "{rel(script)}" %*\r\n'


def write_wrapper(dest, windows=None, python=None, script=None):
    windows = sys.platform.startswith("win") if windows is None else windows
    text = wrapper_text(dest, windows, python, script)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(text.encode("ascii" if windows else "utf-8"))
    if not windows:
        dest.chmod(0o755)
    return dest


def doctor(store, sample=None):
    """Checks for the doctor command. Returns (exit code, report)."""
    rep = {"tool": "tts-rules", "format": VERSION, "python": sys.executable,
           "python_version": platform.python_version(), "script": str(Path(__file__).resolve()),
           "rules_file": str(store.path), "rules": None, "writable": False,
           "check_text": CHECK_TEXT, "problems": []}
    code = EXIT_OK
    if sample is not None and sample != CHECK_TEXT:
        rep["problems"].append(f"the text {CHECK_TEXT} arrived as {sample}: this shell changes "
                               "non-ASCII arguments")
        code = EXIT_ENCODING
    try:
        rep["rules"] = len(store.load())
    except StoreError as exc:
        rep["problems"].append(str(exc))
        code = code or EXIT_STORE
    folder = store.path.parent
    probe = folder
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        fd, tmp = tempfile.mkstemp(prefix=".tts-rules-", suffix=".tmp", dir=str(probe))
        os.close(fd)
        os.unlink(tmp)
        rep["writable"] = True
    except OSError as exc:
        rep["problems"].append(f"cannot write in {probe}: {exc}")
        code = code or EXIT_STORE
    rep["ok"] = code == EXIT_OK
    return code, rep


def _doctor_text(rep):
    n = rep["rules"]
    count = ("unreadable" if n is None else f"{n} rules" if n else
             "no rules yet" if Path(rep["rules_file"]).exists() else
             "not created yet, the first change creates it")
    lines = [f"tool:        tts-rules, rules format {rep['format']}",
             f"python:      {rep['python']} ({rep['python_version']})",
             f"script:      {rep['script']}",
             f"rules file:  {rep['rules_file']} ({count})",
             f"writable:    {'yes' if rep['writable'] else 'no'}",
             f"check text:  {rep['check_text']}"]
    lines += [f"problem:     {p}" for p in rep["problems"]]
    lines.append("ok" if rep["ok"] else "not ok")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  Command line
# --------------------------------------------------------------------------- #
def _q(s):
    """Text in quotes for terminal output; backslashes stay as typed."""
    return '"' + str(s).replace("\n", "\\n") + '"'


def _what(r):
    """'"pattern" -> "replacement"', or 'code <name>' for a code rule."""
    if r.get("kind") == CODE:
        return f"code {r.get('builtin', '')}"
    return f"{_q(r.get('pattern', ''))} -> {_q(r.get('replacement', ''))}"


def _fmt_rule(r, pos, reg=None):
    flag = "on " if r.get("enabled", True) else "off"
    by = r.get("created_by", "user") + ("" if r.get("reviewed", True) else "*")
    line = (f"{pos:>3}  {r['id']:<15} {flag} {r.get('kind', ''):<7} {r.get('lang', 'all'):<3} "
            f"{by:<6} {_what(r)}")
    if r.get("note"):
        line += f"   # {r['note']}"
    if preset_changes(r):
        line += "   [changed preset, reset brings back the original]"
    err = check(r, reg)
    if err:
        line += f"   [broken: {err}]"
    return line


def _fmt_list(rules, groups, only=None, reg=None):
    """Rules in order under a header per group stretch: [markdown: on, 22 rules]."""
    lines, last = [], None
    for i, r in enumerate(rules):
        g = r.get("group", USER_GROUP)
        if only is not None and g != only:
            continue
        if g != last:
            n = sum(1 for x in rules if x.get("group", USER_GROUP) == g)
            lines.append(f"[{g}: {'on' if groups.get(g, True) else 'OFF'}, {n} rule{'s' * (n != 1)}]")
            last = g
        lines.append(_fmt_rule(r, i + 1, reg))
    missing = missing_presets(rules, only if only in GROUPS else None)
    if missing:
        lines.append(f"deleted presets: {', '.join(missing)} (restore brings them back)")
    return "\n".join(lines)


def _build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="machine-readable output")
    common.add_argument("--file", default=argparse.SUPPRESS,
                        help="rules file (default: rules.json in the config folder)")
    writer = argparse.ArgumentParser(add_help=False)
    writer.add_argument("--by", choices=AUTHORS, default="agent",
                        help="who makes the change (default: agent)")

    p = argparse.ArgumentParser(
        prog="tts-rules", parents=[common],
        description="Manage the text rules of the read-aloud app. Rules replace text before it "
                    "is read, top to bottom: first the presets that come with the app (groups "
                    "obsidian, markdown, formulas), then your own rules (group user).",
        epilog="Exit codes: 0 ok, 1 invalid rule, 2 bad arguments, 3 rule not found, "
               "4 rules file busy or unreadable, 5 the shell changed non-ASCII text.")
    p.add_argument("--version", action="version", version=f"tts-rules (rules format {VERSION})")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")

    def cmd(name, help_, *parents, **kw):
        return sub.add_parser(name, help=help_, parents=[common, *parents], **kw)

    s = cmd("list", "list all rules in order, by group")
    s.add_argument("--group", choices=ALL_GROUPS, help="only the rules of one group")
    s = cmd("show", "show one rule")
    s.add_argument("id")
    s = cmd("add", "add a rule (to your own rules unless --group says otherwise)", writer)
    s.add_argument("pattern")
    # Optional, because Windows PowerShell 5.1 drops an empty '' argument
    s.add_argument("replacement", nargs="?", default="", help="what to read instead (empty: drop it)")
    s.add_argument("--kind", choices=KINDS, default="word")
    s.add_argument("--lang", choices=LANGS, default="all")
    s.add_argument("--note", default="")
    s.add_argument("--group", choices=ALL_GROUPS, default=USER_GROUP,
                   help="group to add it to, at its end (default: user)")
    s.add_argument("--at", type=int, help="1-based position in the whole list (default: end of the group)")
    s.add_argument("--disabled", action="store_true", help="add switched off")
    s = cmd("add-code", "advanced: add a code rule from tts_filters.py in the config folder", writer)
    s.add_argument("name", help="a key of the FILTERS dict in tts_filters.py, or latex / dollar-math")
    s.add_argument("--lang", choices=LANGS, default="all")
    s.add_argument("--note", default="")
    s.add_argument("--group", choices=ALL_GROUPS, default=USER_GROUP)
    s.add_argument("--at", type=int, help="1-based position in the whole list (default: end of the group)")
    s = cmd("edit", "change fields of a rule", writer)
    s.add_argument("id")
    s.add_argument("--pattern")
    s.add_argument("--replacement", nargs="?", const="", help="new replacement (empty: drop the text)")
    s.add_argument("--kind", choices=KINDS)
    s.add_argument("--lang", choices=LANGS)
    s.add_argument("--note")
    s = cmd("remove", "remove a rule (restore brings back a deleted preset)", aliases=["rm"])
    s.add_argument("id")
    for name in ("enable", "disable"):
        s = cmd(name, f"{name} a rule, or with --group a whole group", writer)
        s.add_argument("id", nargs="?")
        s.add_argument("--group", choices=ALL_GROUPS, help=f"{name} this group instead of one rule")
    s = cmd("move", "move a rule to another position")
    s.add_argument("id")
    s.add_argument("--to", type=int, required=True, help="1-based position")
    s = cmd("reset", "put a changed preset back to how it came with the app", writer)
    s.add_argument("id")
    s = cmd("restore", "add deleted presets back and reset changed ones", writer)
    g = s.add_mutually_exclusive_group()
    g.add_argument("--group", choices=GROUPS, help="only this group (also switches the group on)")
    g.add_argument("--all", action="store_true", help="all groups (the default)")
    s = cmd("test", "show what the rules do to a text")
    s.add_argument("text", help="the text, or - to read standard input")
    s.add_argument("--lang", choices=tts_langs.SUPPORTED, help="language of the text (default: only 'all' rules)")
    s.add_argument("--group", choices=ALL_GROUPS, help="only the rules of one group")
    s.add_argument("--rules-only", action="store_true", help="only your own rules (same as --group user)")
    s = cmd("export", "write all rules as JSON")
    s.add_argument("--out", help="file to write (default: standard output)")
    s = cmd("import", "add rules from an exported JSON file", writer)
    s.add_argument("source", help="file, or - for standard input")
    s.add_argument("--replace-all", action="store_true", help="replace all existing rules")
    cmd("undo", "undo the last change (one step; a second undo redoes)")
    cmd("path", "print the location of the rules file")
    cmd("prompt", "print instructions for an AI coding assistant")
    s = cmd("doctor", "check that the tool works in this shell and show the rules file it uses")
    s.add_argument("check", nargs="?", help=f"type {CHECK_TEXT} here to check that non-ASCII text arrives unchanged")
    s = sub.add_parser("write-wrapper", parents=[common])         # used by the install scripts
    s.add_argument("dest")
    s.add_argument("--python", help="interpreter (default: this one)")
    return p


def _out(args, data, text):
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, **data}, indent=2))      # ASCII: exact in every console
    elif text:
        print(text)


def main(argv=None):
    # UTF-8 unless PYTHONIOENCODING says otherwise (Windows would pick the ANSI
    # code page for pipes); never fail on a character the stream cannot hold.
    enc = os.environ.get("PYTHONIOENCODING", "").split(":")[0] or "utf-8"
    for stream, kw in ((sys.stdout, {"errors": "backslashreplace"}),
                       (sys.stderr, {"errors": "backslashreplace"}),
                       (sys.stdin, {"errors": "replace"})):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8-sig" if stream is sys.stdin and enc == "utf-8" else enc,
                               **kw)
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.cmd in ("enable", "disable") and (args.id is None) == (args.group is None):
        parser.error(f"{args.cmd} needs a rule id or --group, not both")
    as_json = getattr(args, "json", False)
    # write-wrapper runs during install, before the other modules are copied
    store = None if args.cmd == "write-wrapper" else RuleStore(getattr(args, "file", None))
    if args.cmd not in ("path", "prompt", "doctor", "write-wrapper"):
        migrate(store)
    try:
        return _run(args, store)
    except RuleError as exc:
        code, err, kind = EXIT_INVALID, str(exc), exc.code
    except NotFound as exc:
        code, err, kind = EXIT_NOT_FOUND, str(exc.args[0]), "notFound"
    except (StoreError, OSError) as exc:
        code, err, kind = EXIT_STORE, str(exc), "store"
    if as_json:
        print(json.dumps({"ok": False, "error": err, "code": kind}))
    else:
        print(f"error: {err}", file=sys.stderr)
    return code


def _test(args, store):
    text = sys.stdin.read() if args.text == "-" else args.text
    rules = store.load()
    groups = group_state(rules)
    only = USER_GROUP if args.rules_only else args.group
    if only is not None:
        rules = RuleList([r for r in rules if r.get("group") == only], rules.meta)
    t = trace(text, args.lang, rules, groups, store.code_rules())
    data = {"input": text, "output": t["output"], "lang": args.lang or "", "group": only or "",
            "fired": t["fired"], "skipped": t["skipped"]}
    lines = [f"before: {text}", f"after:  {t['output']}"]
    if t["fired"]:
        lines.append("rules that fired, in order:")
        for f in t["fired"]:
            what = f"code {f['builtin']}" if f["kind"] == CODE else f"{f['kind']} {_what(f)}"
            lines.append(f"  {f['id']:<15} {f['group']:<8} x{f['count']}  {what}")
    else:
        lines.append("no rule fired" + ("" if args.lang else " (without --lang only 'all' rules run)"))
    lines += [f"  skipped broken rule {s['id']}: {s['error']}" for s in t["skipped"]]
    _out(args, data, "\n".join(lines))


def _run(args, store):
    c = args.cmd
    if c == "path":
        _out(args, {"path": str(store.path)}, str(store.path))
    elif c == "prompt":
        print(agent_prompt(store.path))
    elif c == "doctor":
        code, rep = doctor(store, args.check)
        if getattr(args, "json", False):
            print(json.dumps(rep, indent=2))
        else:
            print(_doctor_text(rep))
        return code
    elif c == "write-wrapper":
        try:
            dest = write_wrapper(args.dest, python=args.python)
        except ValueError as exc:
            raise StoreError(str(exc)) from exc
        _out(args, {"wrapper": str(dest)}, f"wrote {dest}")
    elif c == "list":
        rules = store.load()
        groups = group_state(rules)
        shown = [r for r in rules if args.group in (None, r.get("group"))]
        _out(args, {"path": str(store.path), "groups": groups,
                    "missing_presets": missing_presets(rules, args.group if args.group in GROUPS else None),
                    "rules": [{**r, "changed": preset_changes(r)} if r.get("preset_id") else r
                              for r in shown]},
             _fmt_list(rules, groups, args.group, store.code_rules())
             or "No rules yet. Add one with: add '<pattern>' '<replacement>'")
    elif c == "show":
        rules = store.load()
        i = RuleStore._find(rules, args.id)
        _out(args, {"rule": rules[i]}, json.dumps(rules[i], ensure_ascii=False, indent=2))
    elif c == "add":
        r = store.add({"pattern": args.pattern, "replacement": args.replacement, "kind": args.kind,
                       "lang": args.lang, "note": args.note, "enabled": not args.disabled},
                      by=args.by, at=args.at, group=args.group)
        _out(args, {"rule": r}, f"added {r['id']} to {r['group']}: {_what(r)}")
    elif c == "add-code":
        r = store.add({"kind": CODE, "builtin": args.name, "lang": args.lang, "note": args.note},
                      by=args.by, at=args.at, group=args.group)
        _out(args, {"rule": r}, f"added {r['id']} to {r['group']}: {_what(r)}")
    elif c == "edit":
        fields = {k: getattr(args, k) for k in ("pattern", "replacement", "kind", "lang", "note")}
        r = store.update(args.id, fields, by=args.by)
        _out(args, {"rule": r}, f"changed {r['id']}")
    elif c in ("remove", "rm"):
        r = store.remove(args.id)
        hint = "restore brings it back" if r.get("preset_id") else "undo brings it back"
        _out(args, {"rule": r}, f"removed {r['id']} ({hint})")
    elif c in ("enable", "disable"):
        if args.group:
            groups = store.set_group(args.group, c == "enable")
            _out(args, {"groups": groups}, f"{c}d group {args.group}")
        else:
            r = store.set_enabled(args.id, c == "enable", by=args.by)
            _out(args, {"rule": r}, f"{c}d {r['id']}")
    elif c == "move":
        pos = store.move(args.id, args.to)
        _out(args, {"id": args.id, "position": pos}, f"moved {args.id} to position {pos}")
    elif c == "reset":
        r = store.reset(args.id, by=args.by)
        _out(args, {"rule": r}, f"reset {r['id']} to the original")
    elif c == "restore":
        done = store.restore(args.group, by=args.by)
        _out(args, done, f"added back {len(done['added'])} presets, reset {len(done['reset'])}"
                         + (f" in group {args.group} (group switched on)" if args.group else ""))
    elif c == "test":
        _test(args, store)
    elif c == "export":
        text = dump(store.load())
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            _out(args, {"out": args.out}, f"exported to {args.out}")
        else:
            print(text, end="")
    elif c == "import":
        raw = sys.stdin.read() if args.source == "-" else read_text(args.source)
        items = parse(raw)
        added, skipped = store.import_rules(items, by=args.by, replace_all=args.replace_all)
        _out(args, {"added": added, "skipped": skipped},
             f"imported {added} rules, skipped {skipped} duplicates")
    elif c == "undo":
        if not store.undo():
            raise StoreError("nothing to undo")
        _out(args, {"rules": store.load()}, "restored the previous version")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
