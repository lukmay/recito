#!/usr/bin/env bash
# Kokoro (German fork) setup on Linux (Fedora). Idempotent, non-interactive.
#   bash neural/kokoro2/setup.sh
# Counterpart of setup.ps1 with the same versions and commits.
# Data root: $TTS_NEURAL_DIR, default ~/.local/share/tts-neural
# venv:     <root>/kokoro2/venv  (Python 3.12, via uv)
# Sources:  <root>/kokoro2/src/{kokoro,misaki}
# PyTorch:  CUDA build when an NVIDIA card is present (nvidia-smi found), CPU build
#           otherwise (force with TTS_NEURAL_CPU=1). Kokoro is small enough to
#           read aloud on the CPU.
set -euo pipefail

KEY=kokoro2
KOKORO_URL=https://github.com/semidark/kokoro.git
KOKORO_COMMIT=b96fef95e6a7     # 2026-04-17, Kokoro 0.9.4 + German (DEG2P)
MISAKI_URL=https://github.com/semidark/misaki.git
MISAKI_COMMIT=9cda92683091     # 2026-06-21, misaki 0.9.4 + misaki.de
ROOT="${TTS_NEURAL_DIR:-$HOME/.local/share/tts-neural}/$KEY"
VENV="$ROOT/venv"
SRC="$ROOT/src"
PY="$VENV/bin/python"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# espeak-ng for phonemization, git for the forks
if command -v dnf >/dev/null && ! command -v espeak-ng >/dev/null; then
    sudo dnf install -y espeak-ng git
fi
if ! command -v uv >/dev/null; then
    python3 -m pip install --user --quiet uv
    export PATH="$HOME/.local/bin:$PATH"
fi

mkdir -p "$SRC"

# 1) Sources at pinned commits
for spec in "kokoro $KOKORO_URL $KOKORO_COMMIT" "misaki $MISAKI_URL $MISAKI_COMMIT"; do
    set -- $spec
    dir="$SRC/$1"
    [ -d "$dir/.git" ] || git clone -q "$2" "$dir"
    git -C "$dir" fetch -q origin
    git -C "$dir" checkout -q "$3"
    echo "$1 @ $(git -C "$dir" rev-parse --short HEAD)"
done

# 2) venv
[ -x "$PY" ] || uv venv --python 3.12 "$VENV"

# 3) PyTorch matching the hardware, then dependencies
if [ "${TTS_NEURAL_CPU:-0}" != 1 ] && command -v nvidia-smi >/dev/null; then
    TORCH_INDEX=https://download.pytorch.org/whl/cu128
else
    TORCH_INDEX=https://download.pytorch.org/whl/cpu
fi
uv pip install --python "$PY" "torch==2.11.0" --index-url "$TORCH_INDEX"
uv pip install --python "$PY" \
    "numpy<2.3" "soundfile==0.13.1" "huggingface_hub>=0.30,<1.0" "loguru" "transformers==4.56.2" \
    "phonemizer-fork==3.3.2" "espeakng-loader==0.2.4" "num2words==0.5.14" "spacy==3.8.7" \
    "addict" "regex" "click" \
    "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl" \
    "onnx==1.19.0" "onnxruntime==1.22.1"
# Forks without dependency resolution, otherwise misaki[en] pulls a different torch
uv pip install --python "$PY" --no-deps "$SRC/misaki" "$SRC/kokoro"

# 4) Download weights, convert Kerstin, import self-test
"$PY" -I "$HERE/fetch_models.py"
echo "kokoro2: setup ok ($(basename "$TORCH_INDEX"))"

# Voice previews for the setup assistant: one sample per voice, rendered once
# into the preview cache with the app's Python. Skipped (never fatal) when the
# app is not installed or another model server is running.
APP_PY="$HOME/.local/share/piper-venv/bin/python"
for PREVIEW_PY in "$HERE/../../tts_previews.py" "$HERE/../../bin/tts_previews.py"; do
    if [ -x "$APP_PY" ] && [ -f "$PREVIEW_PY" ]; then
        "$APP_PY" "$PREVIEW_PY" build --engine kokoro2 || echo "previews skipped"
        break
    fi
done
