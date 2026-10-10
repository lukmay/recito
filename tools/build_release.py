#!/usr/bin/env python3
"""Builds the release assets of Recito from a git commit.

    python tools/build_release.py <version> <outdir> [--ref REF] [--notes FILE]

Writes into <outdir>:
    Recito-<version>-windows.zip      app files and "Install Recito.cmd"
    Recito-<version>-linux.tar.gz     app files and install.sh
    SHA256SUMS.txt

The files come from `git archive REF` (default: tag v<version> if it exists,
else HEAD), so untracked or uncommitted files never end up in a release.
Dev-only content (tests, CI, icon sources, screenshots) is left out. With
--notes, the "Install" section for the top of the release notes is written to
that file, followed by the CHANGELOG.md section of the version when there is one.
"""
import argparse
import hashlib
import io
import time
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Left out of both archives (prefix match on whole path parts).
COMMON_EXCLUDE = (
    ".github", ".gitignore", ".editorconfig", "pyproject.toml", "CONTRIBUTING.md",
    "tests", "docs", "video", "assets/brand", "tools",
)
# Only one platform needs these.
WINDOWS_ONLY = ("install.ps1", "WINDOWS.md", "bin/win_")
LINUX_ONLY = ("install.sh", "uninstall.sh", "scripts", "kde", "keyd", "systemd", "linux-mssp")
# Re-added although their folder is excluded (install.sh calls it).
KEEP = {"linux": ("tools/install-icons.sh",), "windows": ()}

INSTALL_CMD = (
    "@echo off\r\n"
    "title Install Recito\r\n"
    'cd /d "%~dp0"\r\n'
    'powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"\r\n'
    "echo.\r\n"
    "pause\r\n"
)


def under(path: str, prefixes) -> bool:
    for p in prefixes:
        if p.endswith("_"):          # file name prefix such as bin/win_
            if path.startswith(p):
                return True
        elif path == p or path.startswith(p + "/"):
            return True
    return False


def wanted(path: str, platform: str) -> bool:
    if path in KEEP[platform]:
        return True
    if under(path, COMMON_EXCLUDE):
        return False
    other = LINUX_ONLY if platform == "windows" else WINDOWS_ONLY
    return not under(path, other)


def git(*args: str) -> bytes:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True, capture_output=True).stdout


def resolve_ref(version: str, ref: str | None) -> str:
    if ref:
        return ref
    tag = f"v{version}"
    check = ["git", "-C", str(ROOT), "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}"]
    ok = subprocess.run(check, capture_output=True).returncode == 0
    return tag if ok else "HEAD"


def read_archive(ref: str):
    """Members of `git archive ref` as (name, mode, mtime, data) for regular files."""
    raw = git("archive", "--format=tar", ref)
    out = []
    with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
        for m in tf:
            if m.isfile():
                out.append((m.name, m.mode, m.mtime, tf.extractfile(m).read()))
    return out


def write_zip(path: Path, root: str, files, extra) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, _mode, mtime, data in sorted(files + extra):
            t = max(mtime, 315532800)        # zip cannot hold dates before 1980
            zi = zipfile.ZipInfo(f"{root}/{name}", date_time=time.gmtime(t)[:6])
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, data)


def write_tar(path: Path, root: str, files, extra) -> None:
    with tarfile.open(path, "w:gz") as tf:
        for name, mode, mtime, data in sorted(files + extra):
            ti = tarfile.TarInfo(f"{root}/{name}")
            ti.size, ti.mode, ti.mtime = len(data), mode, mtime
            ti.uname = ti.gname = "root"
            tf.addfile(ti, io.BytesIO(data))


def changelog_section(version: str, text: str) -> str:
    """The body of '## [version]' (or '## version') up to the next '## '."""
    m = re.search(rf"^##\s+\[?{re.escape(version)}\]?[^\n]*\n(.*?)(?=^##\s|\Z)", text, re.S | re.M)
    return m.group(1).strip() if m else ""


def notes_header(version: str, changelog: str) -> str:
    tag = f"Recito-{version}"
    lines = [
        "## Install",
        "",
        f"**Windows 11:** download `{tag}-windows.zip`, extract it, and double-click "
        "`Install Recito.cmd`. It needs Python 3.12 or 3.13 (64-bit); the installer says so if not.",
        "",
        f"**Fedora KDE Plasma 6:** download `{tag}-linux.tar.gz`, then run "
        f"`tar xzf {tag}-linux.tar.gz && cd {tag}-linux && ./install.sh`.",
        "",
        "To update, install the newer release over the old one. Your settings and voices stay. "
        "`SHA256SUMS.txt` lets you check the downloads.",
    ]
    body = changelog_section(version, changelog)
    if body:
        lines += ["", "## What is new", "", body]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("version")
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--ref", help="git ref to archive (default: tag v<version> if present, else HEAD)")
    ap.add_argument("--notes", type=Path, help="write the release notes header to this file")
    a = ap.parse_args()

    if not re.fullmatch(r"\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?", a.version):
        sys.exit(f"not a version: {a.version!r}")
    ref = resolve_ref(a.version, a.ref)
    members = read_archive(ref)
    names = {m[0] for m in members}
    version_bytes = (a.version + "\n").encode()
    extra_all = []
    if "VERSION" in names:
        have = next(m[3] for m in members if m[0] == "VERSION").decode().strip()
        if have != a.version:
            sys.exit(f"VERSION at {ref} says {have}, not {a.version}")
    else:
        # Commits from before the VERSION file: add it, so the installers can copy it.
        print(f"note: {ref} has no VERSION file, adding one", file=sys.stderr)
        extra_all.append(("VERSION", 0o644, members[0][2], version_bytes))

    a.outdir.mkdir(parents=True, exist_ok=True)
    stem = f"Recito-{a.version}"
    win_root, lin_root = f"{stem}-windows", f"{stem}-linux"

    win = [m for m in members if wanted(m[0], "windows")]
    lin = [m for m in members if wanted(m[0], "linux")]
    mtime = members[0][2]
    write_zip(a.outdir / f"{win_root}.zip", win_root, win,
              extra_all + [("Install Recito.cmd", 0o644, mtime, INSTALL_CMD.encode("ascii"))])
    write_tar(a.outdir / f"{lin_root}.tar.gz", lin_root, lin, extra_all)

    sums = []
    for f in (a.outdir / f"{win_root}.zip", a.outdir / f"{lin_root}.tar.gz"):
        sums.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}")
    (a.outdir / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8", newline="\n")

    if a.notes:
        cl = next((m[3].decode("utf-8") for m in members if m[0] == "CHANGELOG.md"), "")
        if not cl and (ROOT / "CHANGELOG.md").is_file():
            cl = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        a.notes.write_text(notes_header(a.version, cl), encoding="utf-8", newline="\n")

    print(f"built {stem} from {ref} into {a.outdir}")
    for line in sums:
        print(" ", line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
