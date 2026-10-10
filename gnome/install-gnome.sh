#!/usr/bin/env bash
# Binds the Recito shortcuts in GNOME (custom keyboard shortcuts), the same
# Super+Ctrl combos that kde/install-kde.sh binds in Plasma. OCR is not bound:
# it needs slurp, which GNOME's compositor does not support.
#
#   install-gnome.sh [--remove]
set -euo pipefail

TTS="$HOME/.local/share/recito"
SCHEMA=org.gnome.settings-daemon.plugins.media-keys
PATHS=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings
command -v gsettings >/dev/null 2>&1 || { echo "gsettings not found"; exit 1; }

# Our slot names; everything else in the list is the user's and stays.
SLOTS=(recito-read recito-read-fast recito-pause)
current="$(gsettings get "$SCHEMA" custom-keybindings)"

# list <keep|drop>: the current list without our entries, as gsettings syntax
others() {
    python3 - "$current" <<'PY'
import ast, sys
raw = sys.argv[1]
if raw.startswith("@as"):
    raw = raw[3:].strip()
items = [p for p in ast.literal_eval(raw) if "/recito-" not in p]
print(repr(items))
PY
}

if [ "${1:-}" = "--remove" ]; then
    gsettings set "$SCHEMA" custom-keybindings "$(others)"
    for s in "${SLOTS[@]}"; do
        gsettings reset-recursively "$SCHEMA.custom-keybinding:$PATHS/$s/" 2>/dev/null || true
    done
    exit 0
fi

bind() {  # <slot> <name> <command> <binding>
    local dir="$PATHS/$1/"
    gsettings set "$SCHEMA.custom-keybinding:$dir" name "$2"
    gsettings set "$SCHEMA.custom-keybinding:$dir" command "$3"
    gsettings set "$SCHEMA.custom-keybinding:$dir" binding "$4"
}
bind recito-read      "Recito read aloud"        "$TTS/tts-speak.sh auto"      "<Super><Control>p"
bind recito-read-fast "Recito read aloud, fast"  "$TTS/tts-speak.sh auto fast" "<Super><Control><Shift>p"
bind recito-pause     "Recito pause / resume"    "$TTS/tts-pause.sh"           "<Super><Control>l"

merged="$(python3 - "$(others)" <<'PY'
import ast, sys
items = ast.literal_eval(sys.argv[1])
base = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings"
for s in ("recito-read", "recito-read-fast", "recito-pause"):
    items.append(f"{base}/{s}/")
print(repr(items))
PY
)"
gsettings set "$SCHEMA" custom-keybindings "$merged"
echo "GNOME shortcuts set: Super+Ctrl+P (read), +Shift (fast), Super+Ctrl+L (pause)."
