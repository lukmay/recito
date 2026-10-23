#!/usr/bin/env bash
# Installs Recito, the local read-aloud tool, for the current user on Linux.
# Needs no root, no distro Python and no pip: it uses uv (installed into
# ~/.local/bin when missing) to fetch Python 3.12 and the packages.
#
#   curl -fsSL https://raw.githubusercontent.com/lukmay/recito/master/install.sh | bash
#   ./install.sh [options]          from a release archive or a clone
#
# Options:
#   --system-deps   also install the optional system packages with sudo
#                   (dnf or apt): OCR, ALSA player, clipboard tools
#   --keyd          also build and enable keyd for the AltGr hotkeys (sudo)
#   --no-start      do not start the tray at the end
#   --gui           show progress and messages in a window (kdialog or
#                   zenity) or as notifications; used by the AppImage
#
# Environment: RECITO_REF (branch or tag the one-liner downloads, default
# master), RECITO_ARCHIVE (URL of a .tar.gz to use instead), UV (path to a uv
# binary to use).
#
# Idempotent. Run it again to update. Installed files:
#   ~/.local/share/recito       scripts, uv's Python, install log and manifest
#   ~/.local/share/piper-venv   the Python environment (historical name)
#   ~/.local/share/piper        voices and voices.conf
#   ~/.local/bin                app, daemon, client, tray, ffmpeg and wl-clipboard if needed
set -euo pipefail

# ---------------------------------------------------------------- options
SYSTEM_DEPS=0; WITH_KEYD=0; NO_START=0; GUI=0
for a in "$@"; do
    case "$a" in
        --system-deps) SYSTEM_DEPS=1 ;;
        --keyd) WITH_KEYD=1 ;;
        --no-start) NO_START=1 ;;
        --gui) GUI=1 ;;
        -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"; exit 0 ;;
        *) echo "unknown option: $a" >&2; exit 2 ;;
    esac
done

# ------------------------------------------------- one-liner: fetch the code
# Piped into bash there is no script file: download the archive of the
# branch and run the install.sh in it.
if [ -z "${BASH_SOURCE[0]:-}" ] || [ ! -f "$(dirname "${BASH_SOURCE[0]}")/bin/tts-tray.py" ]; then
    REF="${RECITO_REF:-master}"
    SRC="$(mktemp -d)"
    trap 'rm -rf "$SRC"' EXIT
    echo "==> Downloading Recito ($REF)"
    curl -fsSL "${RECITO_ARCHIVE:-https://github.com/lukmay/recito/archive/${REF}.tar.gz}" | tar xz -C "$SRC" --strip-components=1
    bash "$SRC/install.sh" "$@"
    exit $?
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.local/share/piper-venv"
PIPER_DIR="$HOME/.local/share/piper"
BIN="$HOME/.local/bin"
TTS="$HOME/.local/share/recito"
OLD_TTS="$HOME/.local/share/tts-vorlesen"   # name before the rename to Recito
VERSION="$(tr -d '[:space:]' < "$REPO/VERSION" 2>/dev/null || echo 0)"
LOG="$TTS/install.log"
MANIFEST="$TTS/installed-files"             # files outside $TTS that we placed
PYVERSION=3.12
export PATH="$BIN:$PATH"                     # tools we place there count as present
PACKAGES=(piper-tts numpy lingua-language-detector edge-tts PySide6 soundcard scipy imageio-ffmpeg)
TOTAL=6

mkdir -p "$TTS" "$BIN"
exec > >(tee -a "$LOG") 2>&1
echo "--- Recito $VERSION install, $(date -Is)"

# uv keeps its Python and cache under ~/.local/share/recito, so uninstalling
# removes them; the cache is dropped at the end.
export UV_PYTHON_INSTALL_DIR="$TTS/python"
export UV_CACHE_DIR="$TTS/uv-cache"
export UV_PYTHON_PREFERENCE=only-managed
export UV_LINK_MODE=copy

# --------------------------------------------------------------- GUI helpers
UI_KIND=none; UI_REF=""; UI_PID=""; UI_FIFO=""; QD=""
HAVE_DISPLAY=0
if [ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]; then HAVE_DISPLAY=1; fi

find_qdbus() {
    local q
    for q in qdbus6 qdbus-qt6 qdbus qdbus-qt5; do
        if command -v "$q" >/dev/null 2>&1; then QD="$q"; return 0; fi
    done
    return 1
}

ui_open() {
    if [ "$GUI" != 1 ] || [ "$HAVE_DISPLAY" != 1 ]; then return 0; fi
    if command -v zenity >/dev/null 2>&1; then
        UI_FIFO="$(mktemp -u)"; mkfifo "$UI_FIFO"
        exec 9<>"$UI_FIFO"        # read-write open does not block
        zenity --progress --title="Recito" --text="Setting up Recito ..." \
            --percentage=0 --no-cancel --auto-close <&9 >/dev/null 2>&1 &
        UI_PID=$!; UI_KIND=zenity
    elif command -v kdialog >/dev/null 2>&1 && find_qdbus; then
        if UI_REF="$(kdialog --title Recito --progressbar "Setting up Recito ..." "$TOTAL" 2>/dev/null)"; then
            UI_KIND=kdialog
        else
            UI_KIND=notify
        fi
    elif command -v notify-send >/dev/null 2>&1; then
        UI_KIND=notify
    fi
}

ui_step() {   # <n> <text>
    local n="$1" text="$2"
    echo "==> $n/$TOTAL  $text"
    case "$UI_KIND" in
        zenity) { echo "# $text"; echo $(( (n - 1) * 100 / TOTAL )); } >&9 2>/dev/null || true ;;
        kdialog)
            # shellcheck disable=SC2086
            "$QD" $UI_REF Set org.kde.kdialog.ProgressDialog value "$((n - 1))" >/dev/null 2>&1 || true
            # shellcheck disable=SC2086
            "$QD" $UI_REF setLabelText "$text" >/dev/null 2>&1 || true ;;
        notify) notify-send -a Recito -i recito "Setting up Recito ($n/$TOTAL)" "$text" 2>/dev/null || true ;;
    esac
}

ui_close() {
    case "$UI_KIND" in
        zenity) echo 100 >&9 2>/dev/null || true
                exec 9>&-
                wait "$UI_PID" 2>/dev/null || true
                rm -f "$UI_FIFO" ;;
        kdialog)
            # shellcheck disable=SC2086
            "$QD" $UI_REF close >/dev/null 2>&1 || true ;;
    esac
    UI_KIND=none
}

# ui_message <title> <text> [copyable command]
ui_message() {
    local title="$1" text="$2" cmd="${3:-}"
    if [ "$GUI" != 1 ] || [ "$HAVE_DISPLAY" != 1 ]; then return 0; fi
    if command -v kdialog >/dev/null 2>&1; then
        if [ -n "$cmd" ]; then
            kdialog --title "$title" --textinputbox "$text" "$cmd" >/dev/null 2>&1 || true
        else
            kdialog --title "$title" --msgbox "$text" >/dev/null 2>&1 || true
        fi
    elif command -v zenity >/dev/null 2>&1; then
        if [ -n "$cmd" ]; then
            printf '%s\n\n%s\n' "$text" "$cmd" | zenity --text-info --title="$title" \
                --width=640 --height=320 >/dev/null 2>&1 || true
        else
            zenity --info --title="$title" --no-markup --text="$text" >/dev/null 2>&1 || true
        fi
    elif command -v notify-send >/dev/null 2>&1; then
        notify-send -a Recito -i recito "$title" "$text${cmd:+ $cmd}" 2>/dev/null || true
    fi
}

on_error() {
    local code=$?
    trap - ERR
    ui_close
    echo "Recito setup failed (exit $code). Log: $LOG" >&2
    ui_message "Recito setup failed" "Something went wrong. The log is at: $LOG"
    exit "$code"
}
trap on_error ERR

ui_open

# ----------------------------------------------------------- 1/6  Python (uv)
ui_step 1 "Preparing Python $PYVERSION"
if [ -z "${UV:-}" ]; then
    if command -v uv >/dev/null 2>&1; then UV="$(command -v uv)"
    elif [ -x "$BIN/uv" ]; then UV="$BIN/uv"
    else
        echo "    uv not found, installing it to $BIN"
        curl -LsSf https://astral.sh/uv/install.sh \
            | env UV_UNMANAGED_INSTALL="$BIN" UV_NO_MODIFY_PATH=1 sh
        UV="$BIN/uv"
        echo "$BIN/uv" >> "$MANIFEST"
    fi
fi
if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    rm -rf "$VENV"
    "$UV" venv --python "$PYVERSION" --python-preference only-managed "$VENV"
fi

# ------------------------------------------------------ 2/6  packages (PyPI)
ui_step 2 "Installing the speech engines and the tray (about 1 GB, a few minutes)"
"$UV" pip install --quiet --python "$VENV/bin/python" --upgrade "${PACKAGES[@]}"

# ------------------------------------------------------ 3/6  files and tools
ui_step 3 "Installing the app files"
mkdir -p "$PIPER_DIR"
cp -n "$REPO/config/voices.conf" "$PIPER_DIR/voices.conf" || true
# Keep an existing config (cp -n) but append missing engine keys, so older
# installs pick up new settings.
for k in engine edge_de edge_en pause_sentence speed_normal speed_fast \
         lang_detect elevenlabs_model elevenlabs_cache; do
    if ! grep -qE "^[[:space:]]*${k}=" "$PIPER_DIR/voices.conf" 2>/dev/null; then
        grep -E "^${k}=" "$REPO/config/voices.conf" >> "$PIPER_DIR/voices.conf" \
            && echo "    + voices.conf: added ${k}"
    fi
done

install -m 0755 "$REPO/bin/piper-read-daemon.py" "$REPO/bin/piper-read-say" \
    "$REPO/bin/tts-tray.py" "$BIN/"
install -m 0644 "$REPO/bin/tts_filters.py" "$BIN/"    # code rules and preset rules
install -m 0644 "$REPO/bin/tts_rules.py" "$BIN/"      # the user's text rules
install -m 0644 "$REPO/bin/tts_platform.py" "$BIN/"   # OS abstraction (audio, IPC)
install -m 0644 "$REPO/bin/tts_config.py" "$BIN/"     # voices.conf parsing
install -m 0644 "$REPO/bin/tts_audio.py" "$BIN/"      # loudness and time stretch
install -m 0644 "$REPO/bin/tts_timing.py" "$BIN/"     # which word is being heard
install -m 0644 "$REPO/bin/tts_i18n.py" "$BIN/"       # UI translations
install -m 0644 "$REPO/bin/tts_langs.py" "$BIN/"      # reading languages, Piper voice download
# tts-rules: command line for the text rules (for power users and AI assistants).
# tts_rules.py writes it, the same code the tests check; it needs the modules above.
"$VENV/bin/python" "$BIN/tts_rules.py" write-wrapper "$BIN/tts-rules" >/dev/null \
    || echo "    Warning: could not write $BIN/tts-rules"
install -D -m 0644 -t "$BIN/i18n" "$REPO"/bin/i18n/*
install -D -m 0644 -t "$BIN/ui" "$REPO"/bin/ui/*
install -m 0644 "$REPO/bin/tts_theme.py" "$REPO/bin/tts_elevenlabs.py" "$REPO/bin/tts_settings.py" "$REPO/bin/tts_settings.qml" \
    "$REPO/bin/tts_reader.py" "$REPO/bin/tts_follow.py" "$BIN/"   # settings, ElevenReader and follow windows
install -D -m 0644 -t "$BIN/fonts" "$REPO"/bin/fonts/*   # icon font (Material Icons, Apache 2.0)
install -m 0644 "$REPO/bin/tts_icons.py" "$BIN/"      # app and tray icon lookup
install -m 0644 "$REPO/VERSION" "$BIN/recito-version.txt"   # shown in the settings window and tray tooltip
rm -rf "$BIN/icons"
cp -r "$REPO/assets/icons" "$BIN/icons"               # generated by tools/build_icons.py
# The same icons in the user's hicolor theme. The tray asks for
# recito-tray[-<state>]-symbolic, which Plasma and GNOME recolor.
bash "$REPO/tools/install-icons.sh"
# Local neural models (server scripts) and custom voices. Each model venv is
# created by neural/<model>/setup.sh, which is optional.
install -m 0644 "$REPO/bin/tts_neural.py" "$REPO/bin/tts_voices.py" "$BIN/"
mkdir -p "$BIN/neural"
cp -r "$REPO/neural/." "$BIN/neural/"
find "$BIN/neural" -name __pycache__ -type d -prune -exec rm -rf {} +
install -m 0644 "$REPO/bin/tts_previews.py" "$BIN/"

# Hotkey wrappers, uninstaller and the icon script it needs.
install -m 0755 "$REPO"/scripts/*.sh "$TTS/"
install -m 0755 "$REPO/uninstall.sh" "$TTS/uninstall.sh"
install -D -m 0755 "$REPO/tools/install-icons.sh" "$TTS/tools/install-icons.sh"
install -D -m 0755 "$REPO/gnome/install-gnome.sh" "$TTS/gnome/install-gnome.sh"

# ffmpeg decodes the Edge TTS MP3. Without a system one, use the static build
# that the imageio-ffmpeg package ships; a link of ours is refreshed on updates.
if [ -L "$BIN/ffmpeg" ] || ! command -v ffmpeg >/dev/null 2>&1; then
    FF="$("$VENV/bin/python" -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())' 2>/dev/null || true)"
    if [ -n "$FF" ] && [ -x "$FF" ]; then
        ln -sf "$FF" "$BIN/ffmpeg"
        grep -qxF "$BIN/ffmpeg" "$MANIFEST" 2>/dev/null || echo "$BIN/ffmpeg" >> "$MANIFEST"
        echo "    ffmpeg: using the static build from imageio-ffmpeg"
    else
        echo "    Warning: no ffmpeg, Edge voices will not play"
    fi
fi
# wl-clipboard (reads the mouse selection on Wayland): the AppImage brings a
# build in vendor/, used only when the system has none of its own.
if [ -d "$REPO/vendor" ]; then
    for t in wl-paste wl-copy; do
        if [ -f "$REPO/vendor/$t" ] && { ! command -v "$t" >/dev/null 2>&1 || grep -qxF "$BIN/$t" "$MANIFEST" 2>/dev/null; }; then
            install -m 0755 "$REPO/vendor/$t" "$BIN/$t"
            grep -qxF "$BIN/$t" "$MANIFEST" 2>/dev/null || echo "$BIN/$t" >> "$MANIFEST"
        fi
    done
fi

# ----------------------------------------------------------- 4/6  voices
ui_step 4 "Downloading the voices"
# Download the Piper voices listed in voices.conf (<lang>=<onnx base name>
# lines only, e.g. de=de_DE-thorsten-low or fr=fr_FR-siwis-medium).
while IFS='=' read -r key base; do
    key="$(echo "$key" | tr -d '[:space:]')"
    base="$(echo "$base" | tr -d '[:space:]')"
    [[ "$key" =~ ^[a-z][a-z]$ && "$base" =~ ^[a-z][a-z]_[A-Z][A-Z]-.+-.+$ ]] || continue
    if [ ! -f "$PIPER_DIR/$base.onnx" ]; then
        echo "    downloading voice $base ..."
        "$VENV/bin/python" -m piper.download_voices "$base" --download-dir "$PIPER_DIR" \
            || echo "    (download failed, place the voice manually: $PIPER_DIR/$base.onnx)"
    fi
done < "$PIPER_DIR/voices.conf"

# ------------------------------------------- 5/6  launchers, service, shortcuts
ui_step 5 "Setting up launcher, autostart and shortcuts"
APPS="$HOME/.local/share/applications"
AUTOSTART="$HOME/.config/autostart"
mkdir -p "$APPS" "$AUTOSTART"
# Migration from the name before Recito: drop the old launcher, autostart entry
# and hotkey wrappers, otherwise the tray would start twice at login.
rm -f "$APPS/tts-tray.desktop" "$AUTOSTART/tts-tray.desktop"
rm -f "$APPS"/tts-vorlesen-*.desktop
rm -rf "$OLD_TTS"
bash "$REPO/tools/install-icons.sh" --remove-legacy
cat > "$APPS/recito.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Recito TTS
GenericName=Text to speech
Keywords=TTS;text to speech;read aloud;speech;voice;screen reader;Vorlesen;
Comment=Configure read-aloud voices, speed, pauses and hotkeys
Exec=$VENV/bin/python $BIN/tts-tray.py
Icon=recito
Terminal=false
X-KDE-StartupNotify=false
Categories=Utility;AudioVideo;
EOF
cp -f "$APPS/recito.desktop" "$AUTOSTART/recito.desktop"

HAVE_SYSTEMD=0
if systemctl --user show-environment >/dev/null 2>&1; then HAVE_SYSTEMD=1; fi

mkdir -p "$HOME/.config/systemd/user"
install -m 0644 "$REPO/systemd/piper-read.service" "$HOME/.config/systemd/user/"
if [ "$HAVE_SYSTEMD" = 1 ]; then
    systemctl --user daemon-reload
    systemctl --user enable piper-read.service
    # restart instead of 'enable --now', so a running daemon loads the new code
    systemctl --user restart piper-read.service
fi

# Shortcuts: KDE through kglobalshortcutsrc, GNOME through gsettings. Both bind
# Meta+Ctrl+P and friends, which keyd can produce from AltGr (optional).
case "${XDG_CURRENT_DESKTOP:-}" in
    *GNOME*|*Unity*|*Budgie*|*Cinnamon*)
        bash "$REPO/gnome/install-gnome.sh" || echo "    (GNOME shortcuts not set)" ;;
    *)
        if command -v kwriteconfig6 >/dev/null 2>&1; then
            bash "$REPO/kde/install-kde.sh"
        else
            echo "    No KDE or GNOME shortcut tool found. Bind a key to: $TTS/tts-speak.sh auto"
        fi ;;
esac

# --------------------------------- optional system packages and keyd (sudo)
# What the system should provide, as package names for dnf and apt. The core
# path (select text, hotkey, read with Piper or Edge) works without them,
# except the selection reader (wl-paste or xclip): the AppImage brings wl-paste.
DNF=(); APT=(); WHY=()
need() {  # <command> <dnf package(s)> <apt package(s)> <why>
    if ! command -v "$1" >/dev/null 2>&1; then
        # shellcheck disable=SC2206
        DNF+=($2)
        # shellcheck disable=SC2206
        APT+=($3)
        WHY+=("$4")
    fi
}
SESSION="${XDG_SESSION_TYPE:-}"
need aplay alsa-utils alsa-utils "audio output"
if [ "$SESSION" = x11 ]; then
    need xclip xclip xclip "reading the selection on X11"
else
    need wl-paste wl-clipboard wl-clipboard "reading the selection on Wayland"
fi
case "${XDG_CURRENT_DESKTOP:-}" in
    *KDE*)
        need tesseract "tesseract tesseract-langpack-deu tesseract-langpack-eng" \
            "tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng" "reading screen regions (OCR)"
        need slurp slurp slurp "OCR region selection"
        need magick ImageMagick imagemagick "OCR image cropping"
        need spectacle spectacle kde-spectacle "OCR screenshots" ;;
esac
if [ "$SESSION" = x11 ] && ! ldconfig -p 2>/dev/null | grep -q 'libxcb-cursor\.so\.0'; then
    DNF+=(xcb-util-cursor); APT+=(libxcb-cursor0); WHY+=("the tray window on X11")
fi

if [ "$SYSTEM_DEPS" = 1 ]; then
    # The earlier install.sh installed all of these; keep that for --system-deps.
    DNF+=(slurp ImageMagick spectacle tesseract tesseract-langpack-deu tesseract-langpack-eng wl-clipboard alsa-utils)
    APT+=(slurp imagemagick tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng wl-clipboard alsa-utils)
fi

PKG_CMD=""
if command -v dnf >/dev/null 2>&1; then
    if [ "${#DNF[@]}" -gt 0 ]; then
        PKG_CMD="sudo dnf install -y $(printf '%s\n' "${DNF[@]}" | sort -u | tr '\n' ' ')"
    fi
elif command -v apt-get >/dev/null 2>&1; then
    if [ "${#APT[@]}" -gt 0 ]; then
        PKG_CMD="sudo apt install -y $(printf '%s\n' "${APT[@]}" | sort -u | tr '\n' ' ')"
    fi
fi
PKG_CMD="${PKG_CMD% }"
WHY_TEXT="$(IFS=,; echo "${WHY[*]:-}" | sed 's/,/, /g')"

if [ "$SYSTEM_DEPS" = 1 ] && [ -n "$PKG_CMD" ]; then
    echo "==> System packages (sudo)"
    eval "$PKG_CMD" && PKG_CMD="" || echo "    (system packages failed, Recito works without most of them)"
fi
if [ "$WITH_KEYD" = 1 ]; then
    echo "==> keyd (sudo; builds from source, check the device ID in keyd/default.conf)"
    sudo bash "$REPO/keyd/install-keyd.sh" || echo "    (keyd failed, the Meta+Ctrl shortcuts still work)"
fi

# ------------------------------------------------------------- 6/6  start
ui_step 6 "Starting Recito"
# A running tray still holds the old code in memory: stop it to apply an update.
pkill -f "$BIN/tts-tray.py" 2>/dev/null && sleep 1 || true
if [ "$HAVE_SYSTEMD" = 1 ]; then systemctl --user stop tts-tray 2>/dev/null || true; fi
echo "$VERSION" > "$TTS/installed-version"
rm -rf "$UV_CACHE_DIR"
# Voice previews, so "Listen" in the setup assistant plays at once: rendered in
# the background into ~/.cache/tts-previews, skipped when offline.
PREVIEW_LOG="${XDG_CACHE_HOME:-$HOME/.cache}/tts-previews/build.log"
nohup "$VENV/bin/python" "$BIN/tts_previews.py" build --engine piper --engine edge \
    --log "$PREVIEW_LOG" >/dev/null 2>&1 &
if [ "$NO_START" = 0 ] && [ "$HAVE_DISPLAY" = 1 ]; then
    if [ "$HAVE_SYSTEMD" = 1 ] && command -v systemd-run >/dev/null 2>&1; then
        systemd-run --user --collect --unit=tts-tray \
            --setenv=PATH="$BIN:$PATH" \
            "$VENV/bin/python" "$BIN/tts-tray.py" >/dev/null 2>&1 || true
    else
        PATH="$BIN:$PATH" setsid nohup "$VENV/bin/python" "$BIN/tts-tray.py" >/dev/null 2>&1 &
    fi
fi
if [ "$HAVE_SYSTEMD" = 0 ]; then
    echo "    No systemd user session: start the daemon with: $VENV/bin/python $BIN/piper-read-daemon.py"
fi
ui_close

# ------------------------------------------------------------------- done
case ":$PATH:" in *":$BIN:"*) ;; *) echo "    Note: $BIN is not in your PATH (the tools in it work anyway)." ;; esac

if [ -n "$PKG_CMD" ]; then
    echo
    echo "Optional, needs root ($WHY_TEXT):"
    echo "  $PKG_CMD"
    ui_message "Recito is ready" "Recito is installed and ready to use. Some optional parts need system packages ($WHY_TEXT). To get them, run this in a terminal:" "$PKG_CMD"
else
    ui_message "Recito is ready" "Recito is installed. Select text and press Meta+Ctrl+P to hear it."
fi

cat <<'DONE'

============================================================
 Done. Recito sits in the system tray; a setup assistant opens
 on the first start. Log out and back in once so the global
 shortcuts load, then:
   Meta+Ctrl+P          read the selection aloud (auto language)
   Meta+Ctrl+Shift+P    same, fast
   Meta+Ctrl+O          OCR a screen region (KDE, needs tesseract)
   Meta+Ctrl+L          pause / resume
 AltGr combos and mouse side buttons need keyd:  ./install.sh --keyd
 Check:  systemctl --user status piper-read.service
 Remove: ~/.local/share/recito/uninstall.sh --purge
============================================================
DONE
