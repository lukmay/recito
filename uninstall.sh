#!/usr/bin/env bash
# Removes Recito (formerly tts-vorlesen), under the new and the old names.
# Keeps the voices (~/.local/share/piper/*.onnx) and the venv by default; --purge
# removes them as well, together with the Python that uv fetched.
#
#   uninstall.sh [--purge]
#
# Also reachable as  recito --uninstall  (AppImage) and as
# ~/.local/share/recito/uninstall.sh  after an install.
set -uo pipefail

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1
HERE="$(cd "$(dirname "$0")" && pwd)"
TTS="$HOME/.local/share/recito"
MANIFEST="$TTS/installed-files"

echo "==> Stopping tray, daemon and removing service"
pkill -f "$HOME/.local/bin/tts-tray.py" 2>/dev/null || true
systemctl --user stop tts-tray 2>/dev/null || true
systemctl --user disable --now piper-read.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/piper-read.service"
systemctl --user daemon-reload 2>/dev/null || true

echo "==> Removing scripts, client and launchers"
rm -f "$HOME/.local/bin/piper-read-daemon.py" "$HOME/.local/bin/piper-read-say"
rm -f "$HOME/.local/bin/tts-rules" "$HOME/.local/bin/tts_rules.py"
rm -f "$HOME/.local/bin/tts-tray.py" "$HOME/.local/bin/recito-version.txt"
rm -f "$HOME/.local/bin"/tts_*.py "$HOME/.local/bin/tts_settings.qml"
rm -rf "$HOME/.local/bin/i18n" "$HOME/.local/bin/ui" "$HOME/.local/bin/fonts" \
       "$HOME/.local/bin/neural" "$HOME/.local/bin/icons" "$HOME/.local/bin/__pycache__"
rm -rf "$HOME/.local/share/tts-vorlesen"   # old name
rm -f "$HOME/.local/share/applications/recito"*.desktop \
      "$HOME/.local/share/applications/tts-vorlesen-"*.desktop \
      "$HOME/.local/share/applications/tts-tray.desktop" \
      "$HOME/.config/autostart/recito.desktop" "$HOME/.config/autostart/tts-tray.desktop"

echo "==> Removing files placed outside the app folders (ffmpeg, wl-clipboard, uv)"
if [ -f "$MANIFEST" ]; then
    while IFS= read -r f; do
        # uv stays unless this was a purge: other tools may use it
        case "$f" in */uv) [ "$PURGE" = 1 ] || continue ;; esac
        [ -n "$f" ] && rm -f "$f"
    done < "$MANIFEST"
fi

echo "==> Removing app and tray icons"
if [ -f "$HERE/tools/install-icons.sh" ]; then
    bash "$HERE/tools/install-icons.sh" --remove
fi

echo "==> Removing shortcuts"
for b in de de-fast de-ocr en en-fast en-ocr auto auto-ocr pause reader; do
    for prefix in recito tts-vorlesen; do          # new and old name
        kwriteconfig6 --file kglobalshortcutsrc --group services \
            --group "${prefix}-${b}.desktop" --key _launch --delete 2>/dev/null || true
    done
done
[ -f "$HERE/gnome/install-gnome.sh" ] && bash "$HERE/gnome/install-gnome.sh" --remove 2>/dev/null
[ -f "$TTS/gnome/install-gnome.sh" ] && bash "$TTS/gnome/install-gnome.sh" --remove 2>/dev/null

if systemctl is-enabled keyd >/dev/null 2>&1; then
    if [ -t 0 ]; then
        echo "==> Disabling keyd (sudo)"
        sudo systemctl disable --now keyd 2>/dev/null || true
        echo "    (keyd binary and /etc/keyd stay; remove them manually if needed.)"
    else
        echo "==> keyd is still enabled. To turn it off:  sudo systemctl disable --now keyd"
    fi
fi

if [ "$PURGE" = 1 ]; then
    echo "==> --purge: removing voices, venv, Python and logs"
    rm -rf "$HOME/.local/share/piper" "$HOME/.local/share/piper-venv" \
        "${XDG_CACHE_HOME:-$HOME/.cache}/tts-previews"
    # This script may live in the folder it deletes; bash has read it already.
    rm -rf "$TTS"
else
    echo "==> Voices and venv kept (~/.local/share/piper, piper-venv). --purge removes them."
    rm -f "$TTS"/tts-*.sh "$TTS/installed-version" "$MANIFEST"
fi
echo "Done."
