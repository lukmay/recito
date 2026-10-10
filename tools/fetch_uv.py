#!/usr/bin/env python3
"""Downloads the pinned uv release for Windows and puts uv.exe into a folder.

    python tools/fetch_uv.py [outdir] [--version X.Y.Z]

The default version is the one in installer/UV_VERSION, the default folder is
installer/. The zip is checked against the .sha256 file that astral-sh publishes
next to it. The Windows installer (installer/recito.iss) bundles the result.
"""
import argparse
import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://github.com/astral-sh/uv/releases/download"
ASSET = "uv-x86_64-pc-windows-msvc.zip"


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("outdir", nargs="?", type=Path, default=ROOT / "installer")
    ap.add_argument("--version", help="uv version (default: installer/UV_VERSION)")
    a = ap.parse_args()
    version = a.version or (ROOT / "installer" / "UV_VERSION").read_text().strip()

    url = f"{BASE}/{version}/{ASSET}"
    data = get(url)
    want = get(url + ".sha256").decode().split()[0].lower()
    have = hashlib.sha256(data).hexdigest()
    if have != want:
        sys.exit(f"sha256 mismatch for {url}: got {have}, expected {want}")

    a.outdir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        (a.outdir / "uv.exe").write_bytes(z.read("uv.exe"))
    print(f"uv {version} -> {a.outdir / 'uv.exe'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
