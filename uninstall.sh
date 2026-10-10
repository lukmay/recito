#!/usr/bin/env bash
# Removes Recito (formerly tts-vorlesen), under the new and the old names.
# Keeps the voices (~/.local/share/piper/*.onnx) and the venv by default; --purge removes them as well.
set -uo pipefail

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

echo "==> Stopping daemon and removing service"
systemctl --user disable --now piper-read.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/piper-read.service"
systemctl --user daemon-reload 2>/dev/null || true

echo "==> Removing scripts, client and launchers"
rm -f "$HOME/.local/bin/piper-read-daemon.py" "$HOME/.local/bin/piper-read-say"
rm -f "$HOME/.local/bin/tts-rules" "$HOME/.local/bin/tts_rules.py"
rm -rf "$HOME/.local/share/recito" "$HOME/.local/share/tts-vorlesen"   # new and old name
rm -f "$HOME/.local/share/applications/recito"*.desktop \
      "$HOME/.local/share/applications/tts-vorlesen-"*.desktop \
      "$HOME/.local/share/applications/tts-tray.desktop" \
      "$HOME/.config/autostart/recito.desktop" "$HOME/.config/autostart/tts-tray.desktop"
rm -f "$HOME/.local/bin/tts_icons.py"
rm -rf "$HOME/.local/bin/icons"

echo "==> Removing app and tray icons"
bash "$(cd "$(dirname "$0")" && pwd)/tools/install-icons.sh" --remove

echo "==> Removing KDE shortcuts"
for b in de de-fast de-ocr en en-fast en-ocr auto auto-ocr pause reader; do
    for prefix in recito tts-vorlesen; do          # new and old name
        kwriteconfig6 --file kglobalshortcutsrc --group services \
            --group "${prefix}-${b}.desktop" --key _launch --delete 2>/dev/null || true
    done
done

echo "==> Disabling keyd (sudo)"
sudo systemctl disable --now keyd 2>/dev/null || true
echo "    (keyd binary and /etc/keyd stay; remove them manually if needed.)"

if [ "$PURGE" = 1 ]; then
    echo "==> --purge: removing voices and venv"
    rm -rf "$HOME/.local/share/piper" "$HOME/.local/share/piper-venv" \
        "${XDG_CACHE_HOME:-$HOME/.cache}/tts-previews"
else
    echo "==> Voices and venv kept (~/.local/share/piper, piper-venv). --purge removes them."
fi
echo "Done."
