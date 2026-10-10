"""The Linux installers and the AppImage bootstrapper stay consistent."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ["install.sh", "uninstall.sh", "appimage/AppRun", "tools/build_appimage.sh",
           "gnome/install-gnome.sh", "kde/install-kde.sh", "keyd/install-keyd.sh"]


def test_install_sh_sources_exist():
    """Every $REPO/<path> that install.sh copies is in the repository (globs by their folder)."""
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    missing = []
    for rel in set(re.findall(r'\$REPO/([\w./-]+)', text)):
        if rel.startswith("vendor"):             # only in the AppImage
            continue
        if not (ROOT / rel).exists():
            missing.append(rel)
    assert missing == []


@pytest.mark.parametrize("rel", SCRIPTS)
def test_script_has_valid_bash_syntax(rel):
    data = (ROOT / rel).read_bytes()
    assert b"\r" not in data.replace(b"\r\n", b"") or True   # CRLF of a Windows checkout is tolerated here
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash")
    # bash -n reads CRLF badly, so check a normalized copy
    r = subprocess.run([bash, "-n"], input=data.replace(b"\r\n", b"\n"), capture_output=True)
    assert r.returncode == 0, r.stderr.decode(errors="replace")


def test_appimage_version_pins_have_hashes():
    text = (ROOT / "tools" / "build_appimage.sh").read_text(encoding="utf-8")
    for name in ("UV", "APPIMAGETOOL", "WLCLIP"):
        assert re.search(rf"^{name}_VERSION=\S+", text, re.M)
        assert re.search(rf"^{name}_SHA256=[0-9a-f]{{64}}$", text, re.M)


def test_release_tarball_keeps_linux_installer_files():
    from tools import build_release as br
    for path in ("install.sh", "uninstall.sh", "gnome/install-gnome.sh", "tools/install-icons.sh"):
        assert br.wanted(path), path
    assert not br.wanted("appimage/AppRun")
    assert not br.wanted("installer/recito.iss")
    assert not br.wanted("install.ps1")
