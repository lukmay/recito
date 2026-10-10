#!/usr/bin/env python3
"""Builds the release assets of Recito from a git commit.

    python tools/build_release.py <version> <outdir> [--ref REF] [--notes FILE]

Writes into <outdir>:
    Recito-<version>-linux.tar.gz     app files and install.sh
    SHA256SUMS.txt                    the release workflow adds the Windows
                                      setup and the Linux AppImage to it

The Windows release is Recito-Setup-<version>.exe, built with Inno Setup from
installer/recito.iss; there is no Windows zip. The Linux AppImage is built from
the tar.gz by tools/build_appimage.sh.

The files come from `git archive REF` (default: tag v<version> if it exists,
else HEAD), so untracked or uncommitted files never end up in a release.
Dev-only content (tests, CI, icon sources, screenshots) is left out. With
--notes, the "Install" section for the top of the release notes is written to
that file, followed by the CHANGELOG.md section of the version when there is one.
"""
import argparse
import hashlib
import io
import re
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Left out of the archive (prefix match on whole path parts).
COMMON_EXCLUDE = (
    ".github", ".gitignore", ".editorconfig", "pyproject.toml", "CONTRIBUTING.md",
    "tests", "docs", "video", "assets/brand", "tools", "installer", "appimage",
)
# Windows files, not in the Linux archive.
WINDOWS_ONLY = ("install.ps1", "WINDOWS.md", "bin/win_")
# Re-added although their folder is excluded (install.sh calls it).
KEEP = ("tools/install-icons.sh",)


def under(path: str, prefixes) -> bool:
    for p in prefixes:
        if p.endswith("_"):          # file name prefix such as bin/win_
            if path.startswith(p):
                return True
        elif path == p or path.startswith(p + "/"):
            return True
    return False


def wanted(path: str) -> bool:
    """Whether a file of the repository goes into the Linux archive."""
    if path in KEEP:
        return True
    if under(path, COMMON_EXCLUDE):
        return False
    return not under(path, WINDOWS_ONLY)


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
        f"**Windows 11:** download `Recito-Setup-{version}.exe` and run it. Nothing else is needed, "
        "not even Python. The file is not code-signed, so Windows may show SmartScreen: "
        "click More info, then Run anyway.",
        "",
        f"**Linux (Fedora KDE Plasma 6, GNOME, Ubuntu):** download `Recito-{version}-x86_64.AppImage`, "
        "make it executable (right-click, Properties, Allow executing, or `chmod +x`) and run it. "
        "It needs no root, no Python and no terminal. Prefer the terminal? "
        "`curl -fsSL https://raw.githubusercontent.com/lukmay/recito/master/install.sh | bash`. "
        f"`{tag}-linux.tar.gz` holds the same installer for offline use.",
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
    lin_root = f"{stem}-linux"

    lin = [m for m in members if wanted(m[0])]
    write_tar(a.outdir / f"{lin_root}.tar.gz", lin_root, lin, extra_all)

    sums = []
    for f in (a.outdir / f"{lin_root}.tar.gz",):
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
