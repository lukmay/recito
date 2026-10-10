#!/usr/bin/env bash
# Switches the TTS engine, e.g. between Edge TTS (online, very natural) and
# Piper (local, offline), and restarts the daemon. Piper is always the automatic
# fallback when Edge is unreachable.
#
#   tts-engine.sh            show current engine and switch interactively
#   tts-engine.sh edge       prefer Edge TTS (Piper as fallback)
#   tts-engine.sh piper      local Piper only
#   tts-engine.sh elevenlabs ElevenLabs (API key in ~/.local/share/piper/elevenlabs.key)
#   tts-engine.sh kokoro2    local Kokoro (model server, neural/kokoro2/setup.sh)
#   tts-engine.sh mssp       Microsoft Speech Platform voices via Wine (see linux-mssp/)
#   tts-engine.sh status     show only
set -uo pipefail

CONF="$HOME/.local/share/piper/voices.conf"
[ -f "$CONF" ] || { echo "voices.conf not found: $CONF" >&2; exit 1; }

current() { sed -nE 's/^[[:space:]]*engine=([a-zA-Z0-9]+).*/\1/p' "$CONF" | tail -1; }

set_engine() {
    local val="$1"
    if grep -qE '^[[:space:]]*engine=' "$CONF"; then
        sed -i -E "s|^[[:space:]]*engine=.*|engine=${val}|" "$CONF"
    else
        printf 'engine=%s\n' "$val" >> "$CONF"
    fi
    echo "Engine -> ${val}. Restarting daemon ..."
    systemctl --user restart piper-read.service \
        && echo "Done." || echo "Daemon restart failed."
}

CUR="$(current)"; CUR="${CUR:-piper}"

if [ "${1:-}" = "elevenlabs" ] && [ -z "${ELEVENLABS_API_KEY:-}" ] && [ ! -s "$(dirname "$CONF")/elevenlabs.key" ]; then
    echo "No ElevenLabs key. Quickest setup is in the tray (Settings ->" >&2
    echo "ElevenLabs -> get API key). Without a key Piper keeps reading." >&2
fi

case "${1:-}" in
    edge|piper|elevenlabs|kokoro2|mssp) set_engine "$1" ;;
    kokoro)     set_engine kokoro2 ;;   # former kokoro-onnx engine
    status)     echo "Current engine: $CUR" ;;
    "")
        echo "Current engine: $CUR"
        if [ "$CUR" = "edge" ]; then OTHER=piper; else OTHER=edge; fi
        read -rp "Switch to '${OTHER}'? [y/N] " a
        [[ "$a" =~ ^[yYjJ]$ ]] && set_engine "$OTHER" || echo "Unchanged."
        ;;
    *) echo "Usage: $0 [edge|piper|elevenlabs|kokoro2|mssp|status]" >&2; exit 1 ;;
esac
