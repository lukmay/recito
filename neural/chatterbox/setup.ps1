# Chatterbox (Resemble AI) setup. Idempotent, non-interactive.
#   powershell -ExecutionPolicy Bypass -File neural\chatterbox\setup.ps1
# Data root: $env:TTS_NEURAL_DIR, default %LOCALAPPDATA%\tts-neural
# venv:    <root>\chatterbox\venv  (Python 3.12)
# Source:  <root>\chatterbox\src  (git, pinned commit)
# Weights: HF cache (ResembleAI/chatterbox-turbo, ResembleAI/chatterbox [Multilingual V3],
#          optionally Kartoffelbox v0.2 for German comparison)
$ErrorActionPreference = "Continue"   # uv and git write progress to stderr
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }

$Key        = "chatterbox"
$RepoUrl    = "https://github.com/resemble-ai/chatterbox.git"
$RepoCommit = "5de7a54aa4e5e2baadb0182dde554908b48b85c2"   # 2026-07-21, includes Multilingual V3 + Nano
$PerthPin   = "ff1c8ac55a976971245cdd53c18d6131ca00d993"   # resemble-perth (watermark), 2026-09-02
$Root       = Join-Path $(if ($env:TTS_NEURAL_DIR) { $env:TTS_NEURAL_DIR } else { Join-Path $env:LOCALAPPDATA 'tts-neural' }) $Key
$Venv       = Join-Path $Root "venv"
$Src        = Join-Path $Root "src"
$Py         = Join-Path $Venv "Scripts\python.exe"

New-Item -ItemType Directory -Force $Root | Out-Null

# 1) Source at the pinned commit
if (-not (Test-Path (Join-Path $Src ".git"))) {
    git clone -q $RepoUrl $Src
}
git -C $Src fetch -q origin
git -C $Src checkout -q $RepoCommit; Check "git checkout"
Write-Host "chatterbox src @ $(git -C $Src rev-parse --short HEAD)"

# 2) venv. Same torch version as the other neural models, so the 2.3 GB wheel
#    comes from the uv cache.
if (-not (Test-Path $Py)) {
    uv venv --python 3.12 $Venv; Check "uv venv"
}

# 3) CUDA torch (cu128 runs on CUDA 13 drivers), then dependencies.
#    Upstream pins torch 2.6 (allows >=2.9 for newer Pythons); 2.11 is tested.
#    gradio, spacy-pkuseg and pykakasi from pyproject are left out
#    (demo UI, Chinese and Japanese only).
uv pip install --python $Py "torch==2.11.0" "torchaudio==2.11.0" --index-url https://download.pytorch.org/whl/cu128; Check "torch"
uv pip install --python $Py `
    "numpy>=1.24,<2" "librosa==0.11.0" "s3tokenizer==0.3.0" "transformers==5.2.0" `
    "diffusers==0.29.0" "conformer==0.3.2" "safetensors==0.5.3" "pyloudnorm==0.1.1" `
    "omegaconf==2.3.0" "huggingface_hub" "soundfile" `
    "resemble-perth @ git+https://github.com/resemble-ai/Perth.git@$PerthPin"; Check "deps"
uv pip install --python $Py --no-deps $Src; Check "chatterbox"

# 4) Download weights and self-test (loads nothing onto the GPU)
$dl = @'
import hashlib, os, sys
from huggingface_hub import snapshot_download, hf_hub_download
tok = os.getenv("HF_TOKEN") or None
p = snapshot_download("ResembleAI/chatterbox-turbo", token=tok,
                      allow_patterns=["*.safetensors", "*.json", "*.txt", "*.pt", "*.model"])
print("turbo:", p)
p = snapshot_download("ResembleAI/chatterbox", token=tok,
                      allow_patterns=["ve.pt", "t3_mtl23ls_v3.safetensors", "s3gen.pt", "s3gen_v3.safetensors",
                                      "grapheme_mtl_merged_expanded_v1.json", "conds.pt",
                                      "Cangjie5_TC.json"])
print("multilingual v3:", p)
print("en tokenizer:", hf_hub_download("ResembleAI/chatterbox", "tokenizer.json", token=tok))
# Kartoffelbox v0.2 (MIT, SebastianBodza). The original repo is gated; this mirror
# is byte-identical according to the LFS SHA256, checked below.
KB = {"t3_cfg.safetensors": "e21a9916587eaf62", "conds.pt": "6552d70568833628"}
try:
    for fn, sha in KB.items():
        f = hf_hub_download("mister2314/kartoffelbox-de", fn,
                            revision="e49b841fb2ca2685424c57a4015bff7faa00bd7e")
        h = hashlib.sha256()
        with open(f, "rb") as fh:
            for b in iter(lambda: fh.read(1 << 22), b""):
                h.update(b)
        assert h.hexdigest().startswith(sha), f"{fn}: SHA256 mismatch"
        print("kartoffelbox:", f, "ok")
except Exception as exc:
    print("kartoffelbox (optional) not loaded:", exc)
import torch, chatterbox.tts_turbo, chatterbox.mtl_tts  # noqa
print("torch", torch.__version__, "cuda", torch.cuda.is_available())
'@
$tmp = Join-Path $Root "setup_selftest.py"
Set-Content -Path $tmp -Value $dl -Encoding utf8
& $Py -I $tmp
if ($LASTEXITCODE -ne 0) { throw "self-test failed" }
Write-Host "chatterbox: setup ok"

# Voice previews for the setup assistant: one sample per voice, rendered once
# into the preview cache with the app's Python. Skipped (never fatal) when the
# app is not installed or another model server is running.
$AppPy = Join-Path $env:LOCALAPPDATA 'piper-venv\Scripts\python.exe'
$PreviewPy = @((Join-Path $PSScriptRoot '..\..\tts_previews.py'),
               (Join-Path $PSScriptRoot '..\..\bin\tts_previews.py')) |
             Where-Object { Test-Path $_ } | Select-Object -First 1
if ((Test-Path $AppPy) -and $PreviewPy) {
    try { & $AppPy $PreviewPy build --engine chatterbox } catch { Write-Host "previews skipped: $_" }
    $global:LASTEXITCODE = 0
}
