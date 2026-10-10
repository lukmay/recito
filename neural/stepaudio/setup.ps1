# Step-Audio-EditX setup: venv, dependencies, repo and weights. Idempotent.
#   powershell -ExecutionPolicy Bypass -File neural\stepaudio\setup.ps1
# Everything goes to <root>\stepaudio\ (venv, src, models, voicecache).
# Data root: $env:TTS_NEURAL_DIR, default %LOCALAPPDATA%\tts-neural
# No vLLM: the LLM runs through neural\stepaudio\step_llm.py (native on Windows).
$ErrorActionPreference = "Stop"

$Key    = "stepaudio"
$Root   = Join-Path $(if ($env:TTS_NEURAL_DIR) { $env:TTS_NEURAL_DIR } else { Join-Path $env:LOCALAPPDATA 'tts-neural' }) $Key
$Venv   = Join-Path $Root "venv"
$Py     = Join-Path $Venv "Scripts\python.exe"
$Src    = Join-Path $Root "src"
$Models = Join-Path $Root "models"

# Pinned versions
$RepoUrl    = "https://github.com/stepfun-ai/Step-Audio-EditX.git"
$RepoCommit = "a652e87052c109e26f616d60971376ff47a829d4"   # 2026-04-09
$EditXRev   = "5fe2f8a05c2353301ad47d3c1747b262115da138"   # HF stepfun-ai/Step-Audio-EditX, 2026-02-14 (model release 20260129)
$TokRev     = "af7e5a3ec06175a7facae9d4100073d6e4dbb36c"   # HF stepfun-ai/Step-Audio-Tokenizer

New-Item -ItemType Directory -Force $Root, $Models | Out-Null

# 1) Source (CosyVoice vocoder, FunASR tokenizer, prompts)
if (-not (Test-Path (Join-Path $Src ".git"))) {
    git clone $RepoUrl $Src
}
git -C $Src fetch --quiet origin
git -C $Src checkout --quiet $RepoCommit
Write-Host "Repo: $(git -C $Src rev-parse --short HEAD)"

# 2) venv (Python 3.12, torch cu128)
if (-not (Test-Path $Py)) {
    uv venv --python 3.12 $Venv
}
uv pip install --python $Py torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu128
uv pip install --python $Py `
    "transformers==4.57.3" "onnxruntime-gpu==1.23.2" "openai-whisper==20250625" `
    "huggingface_hub[hf_xet]>=0.36" hyperpyyaml librosa soundfile sox einops conformer `
    omegaconf "numpy<2.4" setuptools sentencepiece safetensors protobuf six torch-complex `
    kaldiio editdistance hydra-core modelscope num2words scipy tqdm requests humanfriendly `
    pyyaml jieba

# 3) Weights (~9 GB)
$Hf = Join-Path $Venv "Scripts\hf.exe"
& $Hf download stepfun-ai/Step-Audio-Tokenizer --revision $TokRev `
    --local-dir (Join-Path $Models "Step-Audio-Tokenizer") --exclude "*example*"
& $Hf download stepfun-ai/Step-Audio-EditX --revision $EditXRev `
    --local-dir (Join-Path $Models "Step-Audio-EditX") --exclude "assets/*"

# 4) Self-test (imports, CUDA, weights present)
$test = @'
import sys, types, os
sys.modules["vllm"] = types.SimpleNamespace(LLM=object, SamplingParams=object)
root = os.environ["STEPAUDIO_ROOT"]
sys.path.insert(0, os.path.join(root, "src"))
import torch, onnxruntime, whisper, transformers
from stepvocoder.cosyvoice2.cli.cosyvoice import CosyVoice
from tokenizer import StepAudioTokenizer
m = os.path.join(root, "models")
for f in ["Step-Audio-EditX/model-00001.safetensors", "Step-Audio-EditX/CosyVoice-300M-25Hz/flow.pt",
          "Step-Audio-Tokenizer/speech_tokenizer_v1.onnx"]:
    assert os.path.exists(os.path.join(m, f)), f
print("torch", torch.__version__, "cuda", torch.cuda.is_available(),
      "ort", onnxruntime.get_available_providers()[:2], "transformers", transformers.__version__)
'@
$env:STEPAUDIO_ROOT = $Root
& $Py -c $test
if ($LASTEXITCODE -ne 0) { throw "self-test failed" }
Write-Host "stepaudio: setup ok"

# Voice previews for the setup assistant: one sample per voice, rendered once
# into the preview cache with the app's Python. Skipped (never fatal) when the
# app is not installed or another model server is running.
$AppPy = Join-Path $env:LOCALAPPDATA 'piper-venv\Scripts\python.exe'
$PreviewPy = @((Join-Path $PSScriptRoot '..\..\tts_previews.py'),
               (Join-Path $PSScriptRoot '..\..\bin\tts_previews.py')) |
             Where-Object { Test-Path $_ } | Select-Object -First 1
if ((Test-Path $AppPy) -and $PreviewPy) {
    try { & $AppPy $PreviewPy build --engine stepaudio } catch { Write-Host "previews skipped: $_" }
    $global:LASTEXITCODE = 0
}
