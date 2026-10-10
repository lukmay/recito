#!/usr/bin/env bash
# Interactively assigns Piper voices to voice keys.
#
#   tts-voice.sh         interactive: current mapping and available voices, pick by number
#   tts-voice.sh list    list the available voice files only
#
# Voices are *.onnx files in ~/.local/share/piper/. Put new voices there (e.g.
# via piper.download_voices), then assign them here. The daemon is restarted
# afterwards so the voice is loaded warm.
set -uo pipefail

DIR="$HOME/.local/share/piper"
CONF="$DIR/voices.conf"

mapfile -t ONNX < <(cd "$DIR" 2>/dev/null && ls -1 *.onnx 2>/dev/null | sed 's/\.onnx$//')
if [ "${#ONNX[@]}" -eq 0 ]; then
    echo "No *.onnx found in $DIR." >&2
    exit 1
fi

list_voices() {
    local i=1 b
    for b in "${ONNX[@]}"; do printf "  [%2d]  %s\n" "$i" "$b"; i=$((i+1)); done
}

if [ "${1:-}" = "list" ]; then
    echo "Available voices:"; list_voices; exit 0
fi

echo "=== Current mapping ($CONF) ==="
grep -vE '^[[:space:]]*(#|$)' "$CONF" 2>/dev/null | sed 's/^/  /'
echo
echo "=== Available voices ==="
list_voices
echo
read -rp "Which key to change or add? (e.g. de, en): " KEY
KEY="$(printf '%s' "$KEY" | tr -d '[:space:]')"
[ -z "$KEY" ] && { echo "Cancelled."; exit 1; }

read -rp "Number of the voice: " NUM
if ! [[ "$NUM" =~ ^[0-9]+$ ]] || [ "$NUM" -lt 1 ] || [ "$NUM" -gt "${#ONNX[@]}" ]; then
    echo "Invalid number."; exit 1
fi
NEW="${ONNX[$((NUM-1))]}"

if grep -qE "^${KEY}=" "$CONF" 2>/dev/null; then
    sed -i -E "s|^${KEY}=.*|${KEY}=${NEW}|" "$CONF"
else
    printf '%s=%s\n' "$KEY" "$NEW" >> "$CONF"
fi

echo "OK: '${KEY}' -> ${NEW}"
echo "Restarting daemon (loads the voice warm) ..."
systemctl --user restart piper-read.service && echo "Done." || echo "Daemon restart failed."
