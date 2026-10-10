# Local neural TTS models

This folder connects five local speech models to the daemon: Kokoro (`kokoro2`), Qwen3-TTS, Chatterbox, Fish Audio S2 Pro and Step-Audio-EditX. Each model runs as its own model server in its own Python venv, because their dependencies (torch, transformers) conflict. All model servers speak the same HTTP protocol, defined in `server_base.py`, so the daemon treats them alike.

## Comparison

Measured on an RTX 4080-class GPU (16 GB). "First audio" is the median time to the first audio after the model server has loaded. "VRAM" is the increase in used GPU memory reported by `nvidia-smi` during synthesis, including the CUDA context. The real-time factor is compute time divided by audio duration; below 1 the model is faster than it speaks. The word error rate (WER) comes from `asr_check.py`, which transcribes each sample back with Whisper; it measures intelligibility, not how natural a voice sounds.

| Engine | First audio | Real-time factor | VRAM | WER German / English | Expressive control | Default budget |
|---|---|---|---|---|---|---|
| `kokoro2` | 0.11 s | 0.02–0.04 | 1.1 GB | 1.8–2.7 % / 0 % | pronunciation markup | allowed |
| `chatterbox` | 0.30 s (DE), 0.26 s (EN) | 0.33–0.46 | 5.2 GB | 5.1 % / 0 % | tags in English, parameters | allowed |
| `qwen3` | 0.20 s | 0.41–0.42 | 5.2 GB | 1.7 % / 0 % | style instruction in words | allowed |
| `stepaudio` | 1.1 s | 16–26 | 8.1 GB | not measured | tags and edit passes | blocked |
| `fish` | 0.40 s | 0.74–0.79 | 9.2 GB | 1.8 % / 0 % | tags in German and English | blocked |

`qwen3` runs without its extra cloning model here (`QWEN3_CLONE=0` in `models.json`); with it, the server needs 6.3 GB.

Step-Audio-EditX computes about 20 times slower than real time and keeps the GPU fully busy while doing so. Fish loads for minutes and needs 9.2 GB. Both stay above the default VRAM budget, so the launcher refuses to start them unless you raise the budget.

`kokoro2` suits everyday reading best: first audio after about 0.1 s, little VRAM, and it also runs on a CPU. `qwen3` follows style instructions such as "calm, like an audiobook narrator". `chatterbox` sounds the most natural in listening tests and understands tags like `[laugh]` in English, but needs almost the whole default budget.

## Installing a model

Each model has a setup script that creates its venv, downloads pinned weights and runs an import self-test:

```powershell
powershell -ExecutionPolicy Bypass -File neural\qwen3\setup.ps1
```

On Linux, `neural/kokoro2/setup.sh` does the same for `kokoro2`; without an NVIDIA GPU it installs the CPU build of PyTorch. The other models have Windows setup scripts only.

venvs, weights, logs, samples and reference voices live in the data folder: `%LOCALAPPDATA%\tts-neural\` on Windows, `~/.local/share/tts-neural/` on Linux. The environment variable `TTS_NEURAL_DIR` moves it.

The settings window shows every installed model that fits the budget as its own engine card. Small chips on each card name what the model can do, for example cloning voices or understanding tags.

## Configuration

```
engine=kokoro2
kokoro2_de=thorsten
kokoro2_en=af_heart
qwen3_instruct=ruhig und deutlich, wie ein Hörbuchsprecher
neural_idle_min=30
neural_vram_max_mb=5500
neural_vram_reserve_mb=3000
```

`<key>_de` and `<key>_en` choose the voice per language; empty means the model default. `<key>_instruct` passes a style instruction or parameters; its syntax depends on the model and is described in the model's README.

## How the daemon uses the model servers

With `engine=<key>` the daemon starts the model server in the background when it starts itself. A model server that receives no request for `neural_idle_min` minutes (default 30) exits and frees its VRAM. For models above 2 GB that are not the active engine, for example after a voice preview, the limit is at most 10 minutes. The next sentence starts the server again, which takes 10 to 35 seconds, and the settings window says so before a preview. If a model server cannot start or returns an error, Piper reads the sentence.

## Speed and loudness

The daemon sends `speed` only to models with `native_speed` in `models.json`. Kokoro and Step-Audio-EditX have it and render 0.5 to 2.0 themselves. Chatterbox, Qwen3-TTS and Fish S2 Pro have no speed control, so the daemon sends speed 1.0 and time-stretches their audio to the requested pace. The stretch keeps the pitch (WSOLA, `bin/tts_audio.py`) and runs on the streamed chunks with no wait for the whole sentence.

Every model's output then goes through the daemon's loudness normalization, the same as the other engines; see "Speed and loudness" in the main [README](../README.md#speed-and-loudness). The server itself needs no loudness handling. Chatterbox Turbo, which reads English by default, scales the reference voice to -27 LUFS before cloning, so its raw output is about 10 LU quieter than the multilingual model that reads German. The normalization evens this out.

## GPU guard

The rest of the desktop has priority over speech synthesis. `launcher.py` starts a model server only under four conditions:

1. Its measured need (`vram_mb` in `models.json`) fits the budget `neural_vram_max_mb` (default 5500 MB).
2. At least `neural_vram_reserve_mb` (default 3000 MB) stay free after loading.
3. No other model server runs; the launcher stops a running one first.
4. `nvidia-smi` answers. If it does not, only models below 1.5 GB start.

Without an NVIDIA GPU the launcher starts only models marked `cpu_ok` in `models.json` (currently `kokoro2`); the settings window does not offer the others. `TTS_NEURAL_CPU=1` forces this mode on machines with an NVIDIA GPU.

The model server gets `vram_mb` as a hard cap for the PyTorch allocator. A model that needs more fails with an out-of-memory error, and Piper reads the sentence. Without this cap Windows pages GPU memory into system RAM when the card is full, and the whole desktop stalls. Model servers also run at below-normal CPU priority with at most four torch threads. A model server that is not ready after 240 seconds is killed.

When the engine changes, the restarted daemon first stops every model server except the active one. A model server whose daemon is gone (crash, tray app closed) exits on its own after the same idle time.

## Protocol

Every model server listens on 127.0.0.1 only:

| Request | Response |
|---|---|
| `GET /health` | `{"ok": true, "model": ..., "loaded": bool}` |
| `GET /voices` | voices with id, language and description, default voice per language, supported tags, sample rate, whether instructions work |
| `POST /synth` with JSON `text`, `voice`, `lang`, `speed`, `instruct` | raw 16-bit mono PCM, streamed, sample rate in the `X-Sample-Rate` header; errors before the first byte as status 500 with JSON `{"error": ...}` |
| `POST /shutdown` | the process exits |

The port comes from `models.json`. If Windows has reserved that port, the model server takes a free one and writes it to `<data folder>/<key>/port`, where the launcher reads it.

## Tools

```
python neural/launcher.py status          budget, free VRAM, need and permission per model
python neural/launcher.py start qwen3     start a model server by hand
python neural/launcher.py stop qwen3
python neural/gpulock.py python neural/bench.py <key>       samples and timing
python neural/gpulock.py python neural/asr_check.py <key>   Whisper WER
python neural/gallery.py                  rebuild the sample gallery
```

`bench.py` synthesizes the shared test texts (`samples_common.json`) and the model's own examples (`<key>/samples.json`) and writes WAV files and a report to `<data folder>/samples/<key>/`. `gallery.py` builds `samples/gallery.html`, a page with a player, the text, the voice and the timings for every sample. `gpulock.py` makes sure only one of these tools uses the GPU at a time. `asr_check.py` runs in its own venv `<data folder>/_asr/venv` with faster-whisper; the same venv writes the transcripts of new reference voices.

## Reference voices

Models that clone voices (`clone` in `models.json`) speak with any voice from a short reference recording. The settings window creates these from an audio file, the microphone or the PC's audio output, trims silence, limits them to 15 seconds and warns about recordings that are too short, too quiet, clipped or noisy. A voice consists of `<id>.wav`, `<id>.json` (name, language, source) and `<id>.txt` (transcript) in `<data folder>/voices/`. The logic lives in `bin/tts_voices.py`.

The four shared voices `de_female_1`, `de_male_1`, `en_female_1` and `en_male_1` were generated by Qwen3-TTS VoiceDesign from text descriptions (`qwen3/make_ref_voices.py`) and do not belong to a real person. Voices named `cast_hifi_*` come from the Hi-Fi TTS dataset (CC BY 4.0, see [THIRD_PARTY.md](../THIRD_PARTY.md)).

## Adding a model

See [CONTRIBUTING.md](../CONTRIBUTING.md#adding-a-neural-model).
