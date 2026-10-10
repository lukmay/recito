#!/usr/bin/env bash
# Creates the KDE launchers (.desktop) and registers the global shortcuts.
# The shortcuts are the "hyper" combos that keyd produces from AltGr and the
# mouse side buttons (see keyd/default.conf). Do not run as root.
#
# Log out and back in once afterwards so Plasma loads the shortcuts.
set -euo pipefail

APPS="$HOME/.local/share/applications"
TTS="$HOME/.local/share/recito"
mkdir -p "$APPS"

# Migration from the name before Recito: remove the old launchers and their
# KDE shortcut bindings, the new ones are bound below.
rm -f "$APPS"/tts-vorlesen-*.desktop
for b in de de-fast de-ocr en en-fast en-ocr auto auto-ocr pause reader; do
    kwriteconfig6 --file kglobalshortcutsrc --group services \
        --group "tts-vorlesen-${b}.desktop" --key _launch --delete 2>/dev/null || true
done

# launcher <file base> <name> <exec args...>
launcher() {
    local base="$1" name="$2"; shift 2
    cat > "$APPS/recito-${base}.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$name
Exec=$TTS/$*
Icon=recito
Terminal=false
NoDisplay=true
StartupNotify=false
Categories=Utility;
EOF
}

# The file base names (de, en, ...) do not match the actions: both use
# automatic language detection. AltGr+P and the lower mouse button read aloud;
# AltGr+O and the upper mouse button run OCR. The names stay fixed because the
# KDE shortcut bindings refer to them.
launcher de       "Recito read aloud (auto language)"         "tts-speak.sh auto"
launcher de-fast  "Recito read aloud, fast (auto language)"   "tts-speak.sh auto fast"
launcher de-ocr   "Recito OCR (German, forced)"               "tts-speak.sh de ocr"
launcher en       "Recito OCR (auto language)"                "tts-speak.sh auto ocr"
launcher en-fast  "Recito OCR, fast (auto language)"          "tts-speak.sh auto ocr fast"
launcher en-ocr   "Recito OCR (English, forced)"              "tts-speak.sh en ocr"
launcher auto     "Recito read aloud (auto language, AltGr+I)" "tts-speak.sh auto"
launcher auto-ocr "Recito OCR (auto language, AltGr+I)"       "tts-speak.sh auto ocr"
launcher pause    "Recito pause / resume"                     "tts-pause.sh"
launcher reader   "Recito listen in ElevenReader"             "tts-reader.sh"

# bind <launcher base> <combo>
bind() {
    kwriteconfig6 --file kglobalshortcutsrc --group services \
        --group "recito-${1}.desktop" --key _launch "$2"
}

bind de       "Meta+Ctrl+P"
bind de-fast  "Meta+Ctrl+Shift+P"
bind de-ocr   "Meta+Ctrl+Alt+P"
bind en       "Meta+Ctrl+O"
bind en-fast  "Meta+Ctrl+Shift+O"
bind en-ocr   "Meta+Ctrl+Alt+O"
bind auto     "Meta+Ctrl+I"
bind auto-ocr "Meta+Ctrl+Alt+I"
bind pause    "Meta+Ctrl+L"
bind reader   "Ctrl+Alt+E"

echo "KDE launchers and shortcuts set. Please log out and back in once."
