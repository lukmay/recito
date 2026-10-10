"""Runs the command lines of the AI assistant prompt (tts_rules.agent_prompt)
in real shells, against fake installs in temporary folders.

Stdlib only: the pytest module test_prompt_shells imports it, and on Windows
the WSL test runs it inside Linux as a script:

    python3 prompt_shells.py --bin <repo>/bin --install-sh <repo>/install.sh

which prints {"mode/shell": [failures]} as JSON (an empty list is a pass,
null means the shell is not installed).
"""
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CHECK = "Grüße"
HEADERS = {
    "powershell": "In PowerShell (Windows PowerShell 5.1 or PowerShell 7):",
    "gitbash": "In Git Bash:",
    "posix": "In bash, zsh or fish:",
}
MODULES = ("tts_rules.py", "tts_platform.py", "tts_config.py", "tts_filters.py", "tts_langs.py")
# A user name with a space, an apostrophe, an umlaut and shell metacharacters
NASTY = "Jörg O'Neil & Co"

# What list --json must show after the add commands, in order
RULES = [
    ("SQL", "S Q L"),
    (r"(\d+) ?km/h", r"\1 Kilometer pro Stunde"),
    ("it's", "it is"),
    ("foo|bar", "baz"),
    ("R&D", "R and D"),
    ("$HOME", "home"),
    ("^Hallo", "Hi"),
    ("Grüße", "Gruesse"),
    ("TODO: ", ""),
    ("/s", "slash s"),
    ("-x", "minus x"),
]
OUTPUT = "Hi S Q L 50 Kilometer pro Stunde, baz, R and D, it is, home, Gruesse, x slash s minus x"


def commands_from_prompt(text):
    """{shell: command prefix} as the prompt shows it under "How to run"."""
    out = {}
    for key, head in HEADERS.items():
        m = re.search(re.escape(head) + r"\n\n    (.+) <command>\n", text)
        if m:
            out[key] = m.group(1)
    return out


def scenarios(family):
    """[(name, subcommand as typed in the shell, exit code)]. family: 'ps' or 'sh'."""
    q = "''" if family == "ps" else "'\\''"          # a quote inside single quotes
    text = f"'Hallo SQL 50 km/h, foo, R&D, it{q}s, $HOME, Grüße, TODO: x /s -x'"
    return [
        ("doctor", f"doctor '{CHECK}' --json", 0),
        ("doctor-text", f"doctor '{CHECK}'", 0),
        ("add-word", "add 'SQL' 'S Q L' --kind word --note 'Spell out the acronym'", 0),
        ("add-regex", r"add '(\d+) ?km/h' '\1 Kilometer pro Stunde' --kind regex --lang de", 0),
        ("add-apostrophe", f"add 'it{q}s' 'it is' --kind literal", 0),
        ("add-pipe", "add 'foo|bar' 'baz' --kind regex", 0),
        ("add-ampersand", "add 'R&D' 'R and D' --kind literal", 0),
        ("add-dollar", "add '$HOME' 'home' --kind literal", 0),
        ("add-caret", "add '^Hallo' 'Hi' --kind regex", 0),
        ("add-umlaut", "add 'Grüße' 'Gruesse' --kind literal --lang de", 0),
        ("add-empty", "add 'TODO: ' '' --kind literal", 0),
        ("add-slash", "add '/s' 'slash s' --kind literal", 0),
        ("add-dash", "add --kind literal -- '-x' 'minus x'", 0),
        ("list", "list --group user --json", 0),
        ("test", f"test {text} --lang de --rules-only --json", 0),
        ("not-found", "remove nope --json", 3),
        ("undo", "undo --json", 0),
        ("list-after-undo", "list --group user --json", 0),
    ]


def _json(out):
    try:
        return json.loads(out)
    except ValueError:
        return None


def run_scenarios(prefix, family, runner, rules_file):
    """Run every scenario as its own shell call; returns the failures."""
    fails = []
    for name, sub, want in scenarios(family):
        rc, out, err = runner(f"{prefix} {sub}")
        where = f"{name}: rc={rc} out={out[-600:]!r} err={err[-600:]!r}"
        if rc != want:
            fails.append(where)
            continue
        data = _json(out)
        if name == "doctor":
            if not data or not data.get("ok") or data.get("rules_file") != str(rules_file):
                fails.append(f"{where} (want rules_file {rules_file})")
        elif name == "doctor-text":
            if out.strip().splitlines()[-1:] != ["ok"]:
                fails.append(where)
        elif name == "list":
            got = [(r["pattern"], r["replacement"]) for r in (data or {}).get("rules", [])]
            if got != RULES or any(r["created_by"] != "agent" for r in data["rules"]):
                fails.append(f"{where} (got {got!r})")
        elif name == "test":
            if not data or data.get("output") != OUTPUT:
                fails.append(where)
        elif name == "not-found":
            if not data or data.get("code") != "notFound":
                fails.append(where)
        elif name == "list-after-undo":
            if not data or len(data.get("rules", [])) != len(RULES) - 1:
                fails.append(where)
    try:
        saved = [r for r in json.loads(Path(rules_file).read_text(encoding="utf-8"))["rules"]
                 if r.get("group") == "user"]                # the presets come first
        if [r["pattern"] for r in saved] != [p for p, _ in RULES[:-1]]:
            fails.append(f"rules file holds {[r['pattern'] for r in saved]!r}")
    except (OSError, ValueError, KeyError) as exc:
        fails.append(f"rules file {rules_file}: {exc}")
    return fails


def reset(rules_file):
    for suffix in ("", ".bak", ".lock"):
        p = Path(str(rules_file) + suffix)
        if p.exists():
            p.unlink()


def _decode(b):
    return (b or b"").decode("utf-8", errors="replace")


# --- Runners: one shell call per command line ------------------------------ #
def posix_runner(shell, env):
    def run(line):
        p = subprocess.run([shell, "-c", line], env=env, capture_output=True, timeout=60)
        return p.returncode, _decode(p.stdout), _decode(p.stderr)
    return run


def powershell_runner(exe, env):
    """powershell -Command with the exact text (as -EncodedCommand, so no
    second layer of quoting); exit $LASTEXITCODE passes the tool's exit code."""
    def run(line):
        code = base64.b64encode(f"{line}\nexit $LASTEXITCODE".encode("utf-16-le")).decode()
        p = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", code],
                           env=env, capture_output=True, timeout=120)
        return p.returncode, _decode(p.stdout), _decode(p.stderr)
    return run


def gitbash_runner(bash, env, workdir):
    """Git Bash reads the line from a script file: its own command line
    parsing would otherwise take the single quotes apart first."""
    script = Path(workdir) / "line.sh"

    def run(line):
        script.write_bytes((line + "\n").encode("utf-8"))
        p = subprocess.run([bash, "--noprofile", "--norc", str(script)], env=env,
                           capture_output=True, timeout=60)
        return p.returncode, _decode(p.stdout), _decode(p.stderr)
    return run


def cmd_runner(env):
    def run(line):
        p = subprocess.run(f'cmd.exe /d /s /c "{line}"', env=env, capture_output=True, timeout=60)
        return p.returncode, _decode(p.stdout), _decode(p.stderr)
    return run


def find_git_bash():
    """bash.exe of Git for Windows (not System32\\bash.exe, which is WSL)."""
    git = shutil.which("git")
    candidates = [Path(git).resolve().parent.parent / "bin" / "bash.exe"] if git else []
    candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe")
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


# --- Fake installs ---------------------------------------------------------- #
def make_venv(dest):
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(dest)], check=True,
                   capture_output=True, timeout=300)
    return Path(dest)


def copy_modules(bin_src, dest):
    dest.mkdir(parents=True, exist_ok=True)
    for name in MODULES:
        shutil.copy2(Path(bin_src) / name, dest / name)
    return dest


def install_line(script, marker="write-wrapper"):
    """The line of an install script that writes the tts-rules wrapper,
    continuation lines joined."""
    lines = Path(script).read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if marker in line and not line.lstrip().startswith("#"):
            out = [line]
            while out[-1].rstrip().endswith("\\"):
                i += 1
                out.append(lines[i])
            return "\n".join(out)
    raise LookupError(f"no {marker} line in {script}")


PROMPT_CODE = ("import sys; sys.path.insert(0, sys.argv[1]); import tts_rules; "
               "open(sys.argv[2], 'w', encoding='utf-8').write(tts_rules.agent_prompt())")


def render_prompt(python, bin_dir, env, out_file):
    """The prompt as the settings window builds it: tts_rules imported from
    bin_dir, run by python (pythonw.exe on Windows, like the window)."""
    subprocess.run([str(python), "-c", PROMPT_CODE, str(bin_dir), str(out_file)], env=env,
                   check=True, capture_output=True, timeout=120)
    return Path(out_file).read_text(encoding="utf-8")


def base_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in ("PIPER_DIR", "PYTHONIOENCODING")}
    env.update({k: str(v) for k, v in extra.items()})
    return env


# --- Linux ------------------------------------------------------------------ #
LINUX_SHELLS = ("bash", "zsh", "fish", "dash")


def linux_setup(root, mode, bin_src, install_sh):
    """A fake install in root. mode: 'installed' (install.sh layout, wrapper
    written by the install.sh line), 'dev' (repo checkout) or 'piper_dir'
    (installed, app started with PIPER_DIR). Returns (prompt, rules file, shell env)."""
    home = root / f"home {NASTY}"
    venv = home / ".local/share/piper-venv"
    xdg = {"XDG_CONFIG_HOME": root / "xdg config", "XDG_DATA_HOME": root / "xdg data",
           "XDG_CACHE_HOME": root / "xdg cache"}
    app_env = base_env(HOME=home, LANG="C.UTF-8", **xdg)
    rules = home / ".local/share/piper/rules.json"
    if mode == "dev":
        bin_dir = copy_modules(bin_src, home / "src $dir" / "tts setup" / "bin")
        python = sys.executable
    else:
        bin_dir = copy_modules(bin_src, home / ".local/bin")
        make_venv(venv)
        line = install_line(install_sh)
        subprocess.run(["bash", "-c", f'VENV="$1"; BIN="$2"; {line}', "install", str(venv), str(bin_dir)],
                       check=True, capture_output=True, timeout=60)
        python = venv / "bin/python"
        if mode == "piper_dir":
            custom = root / f"custom piper {NASTY}"
            app_env["PIPER_DIR"] = str(custom)
            rules = custom / "rules.json"
    prompt = render_prompt(python, bin_dir, app_env, root / f"prompt-{mode}.md")
    # The assistant's shell: same HOME, but a PIPER_DIR that points elsewhere,
    # so only the --file in the prompt leads to the right file.
    shell_env = base_env(HOME=home, LANG="C.UTF-8", PIPER_DIR=root / "decoy", **xdg)
    return prompt, rules, shell_env


def linux_case(root, mode, shell, bin_src, install_sh):
    """Failures for one mode and shell; None if the shell is missing."""
    exe = shutil.which(shell)
    if not exe:
        return None
    root.mkdir(parents=True, exist_ok=True)
    prompt, rules, env = linux_setup(root, mode, bin_src, install_sh)
    cmds = commands_from_prompt(prompt)
    if set(cmds) != {"posix"}:
        return [f"prompt shows shells {sorted(cmds)}, want posix only"]
    fails = []
    wrapper = _sh_quote(str(root / f"home {NASTY}" / ".local/bin/tts-rules"))
    if (mode != "dev") != cmds["posix"].startswith(wrapper + " "):
        fails.append(f"installed wrapper used only when installed: {cmds['posix']}")
    if "PowerShell" in prompt or "Git Bash" in prompt:
        fails.append("Linux prompt mentions Windows shells")
    reset(rules)
    return fails + run_scenarios(cmds["posix"], "sh", posix_runner(exe, env), rules)


def _sh_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def linux_matrix(bin_src, install_sh, shells=LINUX_SHELLS):
    results = {}
    for mode in ("installed", "dev", "piper_dir"):
        for shell in shells:
            root = Path(tempfile.mkdtemp(prefix="tts prompt "))
            try:
                results[f"{mode}/{shell}"] = linux_case(root, mode, shell, bin_src, install_sh)
            except Exception as exc:                  # report, do not stop the matrix
                results[f"{mode}/{shell}"] = [f"setup failed: {exc!r}"]
            finally:
                shutil.rmtree(root, ignore_errors=True)
    return results


# --- Windows ---------------------------------------------------------------- #
def windows_setup(root, mode, bin_src, install_ps1):
    """A fake install under root/<NASTY>. mode: 'installed' (install.ps1
    layout, wrapper written by the install.ps1 line) or 'dev' (repo checkout
    in a OneDrive folder). Returns (prompt, rules file, shell env, wrapper)."""
    user = root / NASTY
    local, roaming = user / "AppData/Local", user / "AppData/Roaming"
    app_env = base_env(APPDATA=roaming, LOCALAPPDATA=local, USERPROFILE=user)
    wrapper = None
    if mode == "dev":
        bin_dir = copy_modules(bin_src, user / "OneDrive - Contoso & Co" / "Dev $x (tts)" / "bin")
        exe = Path(sys.executable)
        pythonw = exe.with_name("pythonw.exe")
        python = pythonw if pythonw.exists() else exe
    else:
        bin_dir = copy_modules(bin_src, local / "piper-bin")
        venv = make_venv(local / "piper-venv")
        py = venv / "Scripts/python.exe"
        line = install_line(install_ps1)
        script = (f"$Py = '{_ps(py)}'; $BinDir = '{_ps(bin_dir)}'\n{line}\n"
                  "exit $LASTEXITCODE")
        code = base64.b64encode(script.encode("utf-16-le")).decode()
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", code],
                       env=app_env, check=True, capture_output=True, timeout=120)
        python = venv / "Scripts/pythonw.exe"          # the settings window runs under pythonw
        wrapper = bin_dir / "tts-rules.cmd"
    prompt = render_prompt(python, bin_dir, app_env, root / f"prompt-{mode}.md")
    decoy = root / "decoy"
    shell_env = base_env(APPDATA=decoy, LOCALAPPDATA=decoy, PIPER_DIR=decoy)
    return prompt, roaming / "piper" / "rules.json", shell_env, wrapper


def _ps(p):
    return str(p).replace("'", "''")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bin", required=True)
    ap.add_argument("--install-sh", required=True)
    args = ap.parse_args(argv)
    results = linux_matrix(args.bin, args.install_sh)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
