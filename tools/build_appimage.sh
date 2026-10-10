#!/usr/bin/env bash
# Builds Recito-<version>-x86_64.AppImage from the linux archive that
# tools/build_release.py wrote, and adds it to SHA256SUMS.txt.
#
#   tools/build_appimage.sh <version> <distdir>
#
# The AppImage is a bootstrapper: the app files, a pinned uv and a pinned
# wl-clipboard build. It installs Recito into the user's home on first run
# (see appimage/AppRun). Needs on the build machine: curl, tar, sha256sum,
# file, gcc, libc6-dev, meson, ninja, pkg-config, libwayland-dev, wayland-protocols,
# libwayland-bin (Ubuntu 22.04 or newer). Set SKIP_WL_CLIPBOARD=1 to build
# without wl-clipboard (the sessions of X11 users do not need it).
set -euo pipefail

VERSION="${1:?usage: build_appimage.sh <version> <distdir>}"
DIST="$(cd "${2:?usage: build_appimage.sh <version> <distdir>}" && pwd)"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Pinned tools, checked against these hashes.
UV_VERSION=0.13.0
UV_SHA256=1468ebd5a5541121837c5a2817b9972ba6090fa6caa3d142620850a47fb75154
APPIMAGETOOL_VERSION=1.9.1
APPIMAGETOOL_SHA256=ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0
WLCLIP_VERSION=2.3.0
WLCLIP_SHA256=b4dc560973f0cd74e02f817ffa2fd44ba645a4f1ea94b7b9614dacc9f895f402

TARBALL="$DIST/Recito-$VERSION-linux.tar.gz"
OUT="$DIST/Recito-$VERSION-x86_64.AppImage"
[ -f "$TARBALL" ] || { echo "missing $TARBALL (run tools/build_release.py first)" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
APPDIR="$WORK/AppDir"
PAY="$APPDIR/usr/share/recito"
mkdir -p "$PAY" "$APPDIR/usr/bin"

fetch() {  # <url> <sha256> <file>
    curl -fsSL --retry 3 -o "$3" "$1"
    echo "$2  $3" | sha256sum -c --quiet -
}

echo "==> App files from $(basename "$TARBALL")"
tar xzf "$TARBALL" -C "$PAY" --strip-components=1
# The tarball has no tools/ except install-icons.sh; the payload needs exactly that.
[ -x "$PAY/install.sh" ] && [ -f "$PAY/tools/install-icons.sh" ] || { echo "unexpected archive layout" >&2; exit 1; }

echo "==> uv $UV_VERSION"
fetch "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-unknown-linux-gnu.tar.gz" \
    "$UV_SHA256" "$WORK/uv.tgz"
tar xzf "$WORK/uv.tgz" -C "$WORK" --strip-components=1 uv-x86_64-unknown-linux-gnu/uv
install -m 0755 "$WORK/uv" "$APPDIR/usr/bin/uv"

if [ "${SKIP_WL_CLIPBOARD:-0}" != 1 ]; then
    echo "==> wl-clipboard $WLCLIP_VERSION"
    fetch "https://github.com/bugaevc/wl-clipboard/archive/refs/tags/v$WLCLIP_VERSION.tar.gz" \
        "$WLCLIP_SHA256" "$WORK/wl.tgz"
    mkdir "$WORK/wl" && tar xzf "$WORK/wl.tgz" -C "$WORK/wl" --strip-components=1
    meson setup "$WORK/wl/build" "$WORK/wl" -Dzshcompletiondir=no -Dfishcompletiondir=no         --buildtype=release --strip
    ninja -C "$WORK/wl/build"
    mkdir -p "$PAY/vendor"
    for t in wl-copy wl-paste; do
        install -m 0755 "$WORK/wl/build/src/$t" "$PAY/vendor/$t"
    done
fi

echo "==> AppDir"
install -m 0755 "$ROOT/appimage/AppRun" "$APPDIR/AppRun"
install -m 0644 "$ROOT/appimage/recito.desktop" "$APPDIR/recito.desktop"
install -m 0644 "$PAY/assets/icons/hicolor/256x256/apps/recito.png" "$APPDIR/recito.png"
ln -s recito.png "$APPDIR/.DirIcon"
# AppRun is read from the image; the payload needs no execute bit tricks.
chmod 0755 "$PAY"/install.sh "$PAY"/uninstall.sh

echo "==> appimagetool $APPIMAGETOOL_VERSION"
fetch "https://github.com/AppImage/appimagetool/releases/download/$APPIMAGETOOL_VERSION/appimagetool-x86_64.AppImage" \
    "$APPIMAGETOOL_SHA256" "$WORK/appimagetool"
chmod +x "$WORK/appimagetool"
rm -f "$OUT"
ARCH=x86_64 "$WORK/appimagetool" --appimage-extract-and-run --no-appstream "$APPDIR" "$OUT"
chmod +x "$OUT"

# List it in SHA256SUMS.txt (replacing an older line of the same file).
if [ -f "$DIST/SHA256SUMS.txt" ]; then
    grep -v "  $(basename "$OUT")\$" "$DIST/SHA256SUMS.txt" > "$WORK/sums" || true
else
    : > "$WORK/sums"
fi
(cd "$DIST" && sha256sum "$(basename "$OUT")") >> "$WORK/sums"
cp "$WORK/sums" "$DIST/SHA256SUMS.txt"
ls -l "$OUT"
