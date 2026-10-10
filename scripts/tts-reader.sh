#!/usr/bin/env bash
# Sends the selected text (PRIMARY selection) to ElevenReader as a new document
# and plays it in the tray app's ElevenReader window. The tray fetches the
# selection itself and is started by this call if it is not running.
exec "$HOME/.local/share/piper-venv/bin/python" "$HOME/.local/bin/tts-tray.py" --reader
