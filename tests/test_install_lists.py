"""Every module in bin/ reaches the installed app on both platforms."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = sorted(p.name for p in (ROOT / "bin").glob("*.py"))


def test_install_ps1_copies_every_module():
    text = (ROOT / "install.ps1").read_text(encoding="utf-8")
    missing = [m for m in MODULES if f"'{m}'" not in text]
    assert missing == []


def test_install_sh_copies_every_module_but_windows_ones():
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    missing = [m for m in MODULES
               if not m.startswith("win_") and not re.search(rf"bin/{re.escape(m)}\b", text)]
    assert missing == []
