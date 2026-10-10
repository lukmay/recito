#!/usr/bin/env bash
# Installs the app and tray icons into the user's hicolor icon theme
# (~/.local/share/icons/hicolor), or removes them with --remove. Called by
# install.sh and uninstall.sh; the source is assets/icons/hicolor.
#
#   tools/install-icons.sh [--remove]
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO/assets/icons/hicolor"
DST="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"

if [ "${1:-}" = "--remove" ]; then
    find "$DST" -type f \( -name 'tts-vorlesen.*' -o -name 'tts-vorlesen-tray*' \) \
        -delete 2>/dev/null || true
else
    if [ ! -d "$SRC" ]; then
        echo "    (no icons in $SRC, run tools/build_icons.py)" >&2
        exit 0
    fi
    # Same layout as the source: <size>/apps/tts-vorlesen.png,
    # scalable/apps/tts-vorlesen.svg, scalable/status/tts-vorlesen-tray*.svg
    (cd "$SRC" && find . -type f) | while read -r rel; do
        install -D -m 0644 "$SRC/$rel" "$DST/$rel"
    done
fi

# Refresh the icon caches where the tools exist. A user hicolor folder has no
# index.theme, so gtk-update-icon-cache needs --ignore-theme-index.
touch "$DST" 2>/dev/null || true
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache --force --quiet --ignore-theme-index "$DST" 2>/dev/null || true
fi
for k in kbuildsycoca6 kbuildsycoca5; do
    if command -v "$k" >/dev/null 2>&1; then
        "$k" --noincremental >/dev/null 2>&1 || true
        break
    fi
done
