# Kokoro (German fork) setup. Idempotent, non-interactive.
#   powershell -ExecutionPolicy Bypass -File neural\kokoro2\setup.ps1
# Data root: $env:TTS_NEURAL_DIR, default %LOCALAPPDATA%\tts-neural
# venv:     <root>\kokoro2\venv  (Python 3.12)
# Sources:  <root>\kokoro2\src\{kokoro,misaki}  (semidark forks with German
#           language 'd' and German text normalization, pinned commits)
# Weights:  HF cache (pinned revisions). The Kerstin voice is converted once from
#           the stage 2 training checkpoint into Kokoro format
#           -> <root>\kokoro2\models\
$ErrorActionPreference = "Continue"   # uv and git write progress to stderr
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }

$Key          = "kokoro2"
$KokoroUrl    = "https://github.com/semidark/kokoro.git"
$KokoroCommit = "b96fef95e6a7"     # 2026-04-17, Kokoro 0.9.4 + German (DEG2P)
$MisakiUrl    = "https://github.com/semidark/misaki.git"
$MisakiCommit = "9cda92683091"     # 2026-06-21, misaki 0.9.4 + misaki.de (normalization, overrides)
$Root         = Join-Path $(if ($env:TTS_NEURAL_DIR) { $env:TTS_NEURAL_DIR } else { Join-Path $env:LOCALAPPDATA 'tts-neural' }) $Key
$Venv         = Join-Path $Root "venv"
$Src          = Join-Path $Root "src"
$Py           = Join-Path $Venv "Scripts\python.exe"
$Here         = Split-Path -Parent $MyInvocation.MyCommand.Path

New-Item -ItemType Directory -Force $Src | Out-Null

# 1) Sources at pinned commits
foreach ($r in @(@("kokoro", $KokoroUrl, $KokoroCommit), @("misaki", $MisakiUrl, $MisakiCommit))) {
    $dir = Join-Path $Src $r[0]
    if (-not (Test-Path (Join-Path $dir ".git"))) { git clone -q $r[1] $dir; Check "git clone $($r[0])" }
    git -C $dir fetch -q origin
    git -C $dir checkout -q $r[2]; Check "git checkout $($r[0])"
    Write-Host "$($r[0]) @ $(git -C $dir rev-parse --short HEAD)"
}

# 2) venv
if (-not (Test-Path $Py)) { uv venv --python 3.12 $Venv; Check "uv venv" }

# 3) CUDA torch (cu128 runs on CUDA 13 drivers), then dependencies
uv pip install --python $Py "torch==2.11.0" --index-url https://download.pytorch.org/whl/cu128; Check "torch"
uv pip install --python $Py `
    "numpy<2.3" "soundfile==0.13.1" "huggingface_hub>=0.30,<1.0" "loguru" "transformers==4.56.2" `
    "phonemizer-fork==3.3.2" "espeakng-loader==0.2.4" "num2words==0.5.14" "spacy==3.8.7" `
    "addict" "regex" "click" `
    "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl" `
    "onnx==1.19.0" "onnxruntime==1.22.1"; Check "deps"
# Forks without dependency resolution, otherwise misaki[en] pulls the CPU torch from PyPI
uv pip install --python $Py --no-deps (Join-Path $Src "misaki") (Join-Path $Src "kokoro"); Check "kokoro/misaki"

# 4) Download weights, convert Kerstin, import self-test (nothing on the GPU)
& $Py -I (Join-Path $Here "fetch_models.py")
if ($LASTEXITCODE -ne 0) { throw "weights or self-test failed" }
Write-Host "kokoro2: setup ok"

# Voice previews for the setup assistant: one sample per voice, rendered once
# into the preview cache with the app's Python. Skipped (never fatal) when the
# app is not installed or another model server is running.
$AppPy = Join-Path $env:LOCALAPPDATA 'piper-venv\Scripts\python.exe'
$PreviewPy = @((Join-Path $PSScriptRoot '..\..\tts_previews.py'),
               (Join-Path $PSScriptRoot '..\..\bin\tts_previews.py')) |
             Where-Object { Test-Path $_ } | Select-Object -First 1
if ((Test-Path $AppPy) -and $PreviewPy) {
    try { & $AppPy $PreviewPy build --engine kokoro2 } catch { Write-Host "previews skipped: $_" }
    $global:LASTEXITCODE = 0
}
