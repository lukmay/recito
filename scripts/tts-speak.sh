#!/usr/bin/env bash
# Reads the PRIMARY selection (mouse selection) or an OCR'd screen region aloud
# through the warm Piper daemon (piper-read).
#
# Usage:    tts-speak.sh <voice> [ocr] [fast]
#   e.g.    tts-speak.sh auto            (auto language, read primary selection)
#           tts-speak.sh auto fast       (faster)
#           tts-speak.sh auto ocr        (drag a region -> OCR -> read aloud)
#           tts-speak.sh auto ocr fast   (OCR, faster)
#           tts-speak.sh de | en ...     (forced language instead of auto)
#   <voice>: a language code or auto (auto = the daemon detects the language
#            among read_langs from voices.conf and picks the voice)
set -uo pipefail

SAY="$HOME/.local/bin/piper-read-say"
KEY="${1:-}"

if [[ "$KEY" == "--list-voices" ]]; then
    sed -nE 's/^([^#=[:space:]]+)=.*/\1/p' "$HOME/.local/share/piper/voices.conf" 2>/dev/null
    exit 0
fi
if [[ -z "$KEY" ]]; then
    echo "Usage: $0 <voice> [ocr] [fast] | --list-voices" >&2
    exit 1
fi
shift || true

OCR=0; FAST=0
for a in "$@"; do
    case "$a" in
        ocr)  OCR=1 ;;
        fast) FAST=1 ;;
    esac
done
FASTARGS=()
[[ "$FAST" == 1 ]] && FASTARGS=(fast)

if [[ "$OCR" == 1 ]]; then
    # Drag the region with slurp right away (release = done), take a full
    # screenshot, crop it to the region, recognize with Tesseract, read aloud.
    # Tesseract language packs: the forced language, or for auto every
    # reading language (read_langs) whose pack is installed.
    tess_code() {
        case "$1" in
            de) echo deu ;; en) echo eng ;; fr) echo fra ;; es) echo spa ;; it) echo ita ;;
            nl) echo nld ;; pt) echo por ;; pl) echo pol ;; sv) echo swe ;; da) echo dan ;;
            cs) echo ces ;; ru) echo rus ;; tr) echo tur ;;
        esac
    }
    if [[ "$KEY" == auto ]]; then
        LANGS="$(sed -nE 's/^read_langs=(.*)/\1/p' "$HOME/.local/share/piper/voices.conf" 2>/dev/null | tail -n1)"
        HAVE="$(tesseract --list-langs 2>/dev/null)"
        TLANG=""
        for l in ${LANGS//,/ }; do
            c="$(tess_code "$l")"
            [[ -n "$c" ]] && grep -qx "$c" <<<"$HAVE" && TLANG="${TLANG:+$TLANG+}$c"
        done
        TLANG="${TLANG:-deu+eng}"
    else
        TLANG="$(tess_code "$KEY")"
        TLANG="${TLANG:-eng}"
    fi
    GEO="$(slurp -f '%x %y %w %h' 2>/dev/null || true)"
    [[ -z "$GEO" ]] && exit 0                       # selection cancelled
    read -r X Y W H <<<"$GEO"
    FULL="/tmp/tts-ocr-full-$$.png"
    CROP="/tmp/tts-ocr-$$.png"
    rm -f "$FULL" "$CROP"
    spectacle -b -f -n -o "$FULL" >/dev/null 2>&1 || true
    for _ in $(seq 1 30); do [[ -s "$FULL" ]] && break; sleep 0.1; done
    [[ -s "$FULL" ]] || { rm -f "$FULL"; exit 0; }
    magick "$FULL" -crop "${W}x${H}+${X}+${Y}" +repage "$CROP" 2>/dev/null \
        || convert "$FULL" -crop "${W}x${H}+${X}+${Y}" +repage "$CROP" 2>/dev/null || true
    rm -f "$FULL"
    [[ -s "$CROP" ]] || exit 0
    TEXT="$(tesseract "$CROP" stdout -l "$TLANG" 2>/dev/null | tr -s '[:space:]' ' ' || true)"
    rm -f "$CROP"
    if [[ -z "${TEXT// /}" ]]; then
        notify-send "Recito OCR" "No text recognized." 2>/dev/null || true
        exit 0
    fi
    printf '%s' "$TEXT" | "$SAY" "$KEY" "${FASTARGS[@]}" --stdin
else
    # Plain read-aloud: piper-read-say fetches the PRIMARY selection itself.
    "$SAY" "$KEY" "${FASTARGS[@]}"
fi
