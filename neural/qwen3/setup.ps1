# Qwen3-TTS setup: venv, dependencies, weights. Idempotent, non-interactive.
#   powershell -ExecutionPolicy Bypass -File neural\qwen3\setup.ps1 [-NoClone] [-Small] [-RefVoices]
# -NoClone   : skip Qwen3-TTS-12Hz-0.6B-Base (only useful with QWEN3_CLONE=0)
# -Small     : also download 0.6B-CustomVoice (for QWEN3_MODEL=0.6B)
# -RefVoices : VoiceDesign model + Whisper for make_ref_voices.py
# Data root: $env:TTS_NEURAL_DIR, default %LOCALAPPDATA%\tts-neural
param([switch]$NoClone, [switch]$Small, [switch]$RefVoices)
$ErrorActionPreference = "Stop"

$Key  = "qwen3"
$Root = Join-Path $(if ($env:TTS_NEURAL_DIR) { $env:TTS_NEURAL_DIR } else { Join-Path $env:LOCALAPPDATA 'tts-neural' }) $Key
$Venv = Join-Path $Root "venv"
$Py   = Join-Path $Venv "Scripts\python.exe"
$Src  = Join-Path $Root "src"
# Streaming fork of the official package (same models, adds stream_generate_pcm)
$ForkUrl    = "https://github.com/dffdeeq/Qwen3-TTS-streaming"
$ForkCommit = "13ef823ffe76aec4ac7e35d3fec28ecfc8d7427a"

New-Item -ItemType Directory -Force $Root | Out-Null

if (-not (Test-Path $Py)) {
    Write-Host "Creating venv (Python 3.12) ..."
    uv venv --python 3.12 $Venv
    if ($LASTEXITCODE) { throw "uv venv failed" }
}

Write-Host "PyTorch 2.11.0 (cu128) ..."
uv pip install --python $Py torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE) { throw "torch installation failed" }

if (-not (Test-Path (Join-Path $Src ".git"))) {
    Write-Host "Cloning fork ..."
    git clone -q $ForkUrl $Src
    if ($LASTEXITCODE) { throw "git clone failed" }
}
git -C $Src fetch -q origin 2>$null
git -C $Src checkout -q $ForkCommit
if ($LASTEXITCODE) { throw "could not check out commit $ForkCommit" }

Write-Host "Dependencies ..."
# As in the package pyproject, minus gradio (demo UI only)
uv pip install --python $Py `
    transformers==4.57.3 accelerate==1.12.0 librosa==0.11.0 soundfile==0.13.1 `
    sox==1.5.0 onnxruntime==1.23.2 einops==0.8.1 "huggingface_hub[hf_xet]==0.36.0" numpy==2.2.6
if ($LASTEXITCODE) { throw "pip install failed" }
uv pip install --python $Py --no-deps $Src
if ($LASTEXITCODE) { throw "qwen_tts installation failed" }

Write-Host "Weights (HF cache) ..."
$repos = @("Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")
if ($Small)     { $repos += "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice" }
if (-not $NoClone) { $repos += "Qwen/Qwen3-TTS-12Hz-0.6B-Base" }
if ($RefVoices) {
    # only for make_ref_voices.py (regenerating reference voices)
    uv pip install --python $Py faster-whisper==1.2.1
    if ($LASTEXITCODE) { throw "faster-whisper installation failed" }
    $repos += "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"; $repos += "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
}
foreach ($r in $repos) {
    & $Py -c "from huggingface_hub import snapshot_download as s; print(s('$r'))"
    if ($LASTEXITCODE) { throw "download of $r failed" }
}

Write-Host "Self-test ..."
& $Py -c "import torch, qwen_tts, transformers; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), 'transformers', transformers.__version__); from qwen_tts import Qwen3TTSModel; print('qwen_tts ok')"
if ($LASTEXITCODE) { throw "import self-test failed" }
Write-Host "done: $Py"

# Voice previews for the setup assistant: one sample per voice, rendered once
# into the preview cache with the app's Python. Skipped (never fatal) when the
# app is not installed or another model server is running.
$AppPy = Join-Path $env:LOCALAPPDATA 'piper-venv\Scripts\python.exe'
$PreviewPy = @((Join-Path $PSScriptRoot '..\..\tts_previews.py'),
               (Join-Path $PSScriptRoot '..\..\bin\tts_previews.py')) |
             Where-Object { Test-Path $_ } | Select-Object -First 1
if ((Test-Path $AppPy) -and $PreviewPy) {
    try { & $AppPy $PreviewPy build --engine qwen3 } catch { Write-Host "previews skipped: $_" }
    $global:LASTEXITCODE = 0
}
