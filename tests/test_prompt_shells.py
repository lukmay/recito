"""The command lines of the AI assistant prompt, run in real shells against
fake installs whose paths hold a space, an apostrophe, an umlaut and &.

Marked 'shells' (pytest -m "not shells" leaves them out). Each case skips
where its shell is missing: Linux runs bash, zsh, fish and dash; Windows runs
Windows PowerShell 5.1, PowerShell 7, Git Bash and cmd.exe, plus the Linux
cases inside WSL (distro from TTS_WSL_DISTRO, default Ubuntu-22.04)."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import prompt_shells as ps
import tts_rules

pytestmark = pytest.mark.shells

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin"
WINDOWS = sys.platform.startswith("win")
LINUX_MODES = ("installed", "dev", "piper_dir")


# --- Linux ------------------------------------------------------------------ #
@pytest.mark.skipif(WINDOWS, reason="Linux shells; on Windows see test_wsl")
@pytest.mark.parametrize("mode", LINUX_MODES)
@pytest.mark.parametrize("shell", ps.LINUX_SHELLS)
def test_linux(tmp_path, mode, shell):
    fails = ps.linux_case(tmp_path, mode, shell, BIN, ROOT / "install.sh")
    if fails is None:
        pytest.skip(f"{shell} is not installed")
    assert fails == []


# --- Windows ---------------------------------------------------------------- #
@pytest.fixture(scope="module")
def win_setup(tmp_path_factory):
    """windows_setup per mode, built once for the module."""
    if not WINDOWS:
        pytest.skip("Windows shells")
    cache = {}

    def get(mode):
        if mode not in cache:
            cache[mode] = ps.windows_setup(tmp_path_factory.mktemp(mode), mode, BIN,
                                           ROOT / "install.ps1")
        return cache[mode]
    return get


def _powershell(name):
    exe = shutil.which(name)
    if not exe:
        pytest.skip(f"{name} is not installed")
    return exe


@pytest.mark.parametrize("mode", ["installed", "dev"])
def test_windows_prompt_lines(win_setup, mode):
    prompt, rules, _, _ = win_setup(mode)
    cmds = ps.commands_from_prompt(prompt)
    assert set(cmds) == {"powershell", "gitbash"}
    assert "bash, zsh or fish" not in prompt
    assert f"--file {tts_rules.quote_ps(rules)}" in cmds["powershell"]
    if mode == "installed":
        venv_py = rules.parents[2] / "Local" / "piper-venv" / "Scripts" / "python.exe"
        assert cmds["powershell"].startswith(f"& {tts_rules.quote_ps(venv_py)} ")     # not pythonw
    else:
        assert "OneDrive - Contoso & Co" in cmds["powershell"]


@pytest.mark.parametrize("mode", ["installed", "dev"])
@pytest.mark.parametrize("shell", ["powershell", "pwsh", "gitbash"])
def test_windows(win_setup, tmp_path, mode, shell):
    prompt, rules, env, _ = win_setup(mode)
    cmds = ps.commands_from_prompt(prompt)
    if shell == "gitbash":
        bash = ps.find_git_bash()
        if not bash:
            pytest.skip("Git Bash is not installed")
        prefix, family, runner = cmds["gitbash"], "sh", ps.gitbash_runner(bash, env, tmp_path)
    else:
        exe = _powershell("powershell.exe" if shell == "powershell" else "pwsh.exe")
        prefix, family, runner = cmds["powershell"], "ps", ps.powershell_runner(exe, env)
    ps.reset(rules)
    assert ps.run_scenarios(prefix, family, runner, rules) == []


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh.exe"])
def test_windows_powershell_double_quotes_through_import(win_setup, tmp_path, shell):
    """What the prompt says to do for a rule with a double quote: a JSON file
    written by Set-Content (UTF-8 with BOM in 5.1), then import."""
    prompt, rules, env, _ = win_setup("installed")
    exe = _powershell(shell)
    prefix = ps.commands_from_prompt(prompt)["powershell"]
    f = tts_rules.quote_ps(tmp_path / "Jörg's rules.json")
    line = ("'[{\"pattern\": \"say \\\"hi\\\"\", \"replacement\": \"say hi\", \"kind\": \"literal\"}]' "
            f"| Set-Content -Encoding utf8 -LiteralPath {f}\n{prefix} import {f} --json")
    ps.reset(rules)
    rc, out, err = ps.powershell_runner(exe, env)(line)
    assert rc == 0, (out, err)
    assert json.loads(out)["added"] == 1
    mine = [r for r in json.loads(rules.read_text(encoding="utf-8"))["rules"] if r["group"] == "user"]
    assert mine[0]["pattern"] == 'say "hi"'


def test_windows_cmd_wrapper(win_setup):
    """tts-rules.cmd from the install.ps1 line, run through cmd /c with
    double-quoted arguments; exit codes pass through."""
    _, rules, env, wrapper = win_setup("installed")
    assert wrapper.read_bytes() == b'@"%~dp0..\\piper-venv\\Scripts\\python.exe" "%~dp0tts_rules.py" %*\r\n'
    run = ps.cmd_runner(env)
    head = f'"{wrapper}" --file "{rules}"'
    ps.reset(rules)
    rc, out, err = run(f"{head} doctor Grüße --json")
    assert rc == 0 and json.loads(out)["rules_file"] == str(rules), (out, err)
    rc, out, err = run(f'{head} add "R&D" "R and D" --kind literal --json')
    assert rc == 0, (out, err)
    rc, out, _ = run(f"{head} remove nope --json")
    assert rc == tts_rules.EXIT_NOT_FOUND and json.loads(out)["code"] == "notFound"
    rc, out, err = ps.powershell_runner(_powershell("powershell.exe"), env)(
        f"& {tts_rules.quote_ps(wrapper)} --file {tts_rules.quote_ps(rules)} list --group user --json")
    assert rc == 0 and [r["pattern"] for r in json.loads(out)["rules"]] == ["R&D"], (out, err)


# --- Linux inside WSL (from Windows) ----------------------------------------- #
DISTRO = os.environ.get("TTS_WSL_DISTRO", "Ubuntu-22.04")


def _wsl_path(p):
    p = Path(p).resolve()
    return f"/mnt/{p.drive[0].lower()}{p.as_posix()[2:]}"


@pytest.fixture(scope="module")
def wsl_results():
    """prompt_shells.py run once inside WSL; the script deletes its temp dirs."""
    if not WINDOWS or not shutil.which("wsl.exe"):
        pytest.skip("WSL runs only from Windows")
    listed = subprocess.run(["wsl.exe", "-l", "-q"], capture_output=True, timeout=60)
    names = listed.stdout.decode("utf-16-le", errors="replace").replace("\x00", "").split()
    if DISTRO not in names:
        pytest.skip(f"WSL distro {DISTRO} is not installed")
    p = subprocess.run(["wsl.exe", "-d", DISTRO, "-e", "python3", _wsl_path(Path(ps.__file__)),
                        "--bin", _wsl_path(BIN), "--install-sh", _wsl_path(ROOT / "install.sh")],
                       capture_output=True, timeout=1200)
    assert p.returncode == 0, p.stderr.decode("utf-8", errors="replace")
    return json.loads(p.stdout.decode("utf-8"))


@pytest.mark.parametrize("mode", LINUX_MODES)
@pytest.mark.parametrize("shell", ps.LINUX_SHELLS)
def test_wsl(wsl_results, mode, shell):
    fails = wsl_results[f"{mode}/{shell}"]
    if fails is None:
        pytest.skip(f"{shell} is not installed in {DISTRO}")
    assert fails == []
