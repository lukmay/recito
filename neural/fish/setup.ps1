# Fish Audio S2 Pro setup: venv, source, weights, self-test. Idempotent, non-interactive.
#   powershell -ExecutionPolicy Bypass -File neural\fish\setup.ps1
# Data root: $env:TTS_NEURAL_DIR, default %LOCALAPPDATA%\tts-neural
$ErrorActionPreference = "Stop"
$Key     = "fish"
$Root    = Join-Path $(if ($env:TTS_NEURAL_DIR) { $env:TTS_NEURAL_DIR } else { Join-Path $env:LOCALAPPDATA 'tts-neural' }) $Key
$Src     = Join-Path $Root "src"
$Venv    = Join-Path $Root "venv"
$Py      = Join-Path $Venv "Scripts\python.exe"
$Ckpt    = Join-Path $Root "checkpoints\s2-pro"
$Commit  = "214da3cd841bda85da2496b96cd3c4d7edb1337e"   # fish-speech main, 2026-09-17
$HfRev   = "1de9996b6be38b745688de084d87a5633f714e4e"   # fishaudio/s2-pro
New-Item -ItemType Directory -Force $Root | Out-Null

# 1) Source (fish-speech, pinned)
if (-not (Test-Path (Join-Path $Src ".git"))) {
    git clone https://github.com/fishaudio/fish-speech.git $Src
}
git -C $Src fetch --quiet origin
git -C $Src checkout --quiet $Commit
if ($LASTEXITCODE -ne 0) { throw "git checkout $Commit failed" }

# 2) venv (Python 3.12), torch 2.11.0 cu128, fish-speech dependencies.
#    fish-speech pins torch 2.8.0; an override selects 2.11.0 to match the other
#    models (shared uv cache). tensorboard<2.20 because 2.20+ needs protobuf>=6,
#    while fish-speech needs protobuf<6.
if (-not (Test-Path $Py)) { uv venv --python 3.12 $Venv }
$Ovr = Join-Path $Root "overrides.txt"
"torch==2.11.0`ntorchaudio==2.11.0`nprotobuf>=3.20.0,<6.0.0`ntensorboard<2.20" | Set-Content -Encoding ascii $Ovr
uv pip install --python $Py "torch==2.11.0" "torchaudio==2.11.0" --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) { throw "torch installation failed" }
uv pip install --python $Py --overrides $Ovr -e $Src "huggingface_hub[hf_xet]" soundfile "tensorboard<2.20"
if ($LASTEXITCODE -ne 0) { throw "fish-speech installation failed" }

# 3) Weights (~11 GB, pinned revision)
$dl = @"
from huggingface_hub import snapshot_download
snapshot_download('fishaudio/s2-pro', revision='$HfRev', local_dir=r'$Ckpt',
    allow_patterns=['*.json','*.safetensors','codec.pth','*.jinja','LICENSE.md','README.md'])
print('weights ok')
"@
& $Py -c $dl
if ($LASTEXITCODE -ne 0) { throw "download failed" }

# 4) Self-test (import and CUDA, no model load)
$t = @"
import sys, torch
sys.path.insert(0, r'$Src')
from fish_speech.models.text2semantic.llama import DualARTransformer
from fish_speech.models.text2semantic.inference import load_codec_model
from fish_speech.tokenizer import FishTokenizer
tok = FishTokenizer.from_pretrained(r'$Ckpt')
print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), 'semantic', tok.semantic_begin_id, tok.semantic_end_id)
assert torch.cuda.is_available()
"@
& $Py -c $t
if ($LASTEXITCODE -ne 0) { throw "self-test failed" }
Write-Host "fish: setup done. Test: python neural\gpulock.py python neural\bench.py fish"
