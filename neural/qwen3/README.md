# Qwen3-TTS (key `qwen3`)

Local model server for Qwen3-TTS (Alibaba Qwen, Apache-2.0). It speaks the shared protocol from `../server_base.py`, streams audio and runs in its own venv `<data folder>/qwen3/venv`.

## Models

The server loads up to two checkpoints of the Qwen3-TTS family (released January 2026).

`Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` provides nine preset speakers and understands style instructions in natural language. Only the 1.7B variant follows instructions; the smaller 0.6B CustomVoice ignores them.

`Qwen/Qwen3-TTS-12Hz-0.6B-Base` clones the shared reference voices from `<data folder>/voices/`. None of the nine preset speakers is a native German speaker, so cloning gives German a native voice. `models.json` turns cloning off (`QWEN3_CLONE=0`) to stay within the default VRAM budget.

The official Python package has no streaming code. Setup installs the fork [dffdeeq/Qwen3-TTS-streaming](https://github.com/dffdeeq/Qwen3-TTS-streaming) at commit `13ef823`. `server.py` uses only its model classes and runs its own decode loop.

## Measurements

Measured on an RTX 4080-class GPU with `bench.py` and `asr_check.py`, with 1.7B CustomVoice plus 0.6B Base.

| Quantity | Value |
|---|---|
| VRAM | 6.3 GB allocated, 6.7 GB reserved (4.3 GB without the cloning model) |
| Load time | 12 to 18 s including warmup and CUDA graph capture |
| First audio, warm | 195 to 230 ms, median about 208 ms |
| First audio, first request | up to 485 ms |
| Real-time factor | 0.34 to 0.43 |
| WER, shared samples without numbers | German 1.7 %, English 0 % |

The real-time factor holds for sentences of 60 to 230 characters.

## Voices

Defaults: `ref:de_male_1` for German if cloning is on, otherwise `Dylan`; `Aiden` for English.

Preset speakers: `Aiden` and `Ryan` (English), `Serena`, `Vivian`, `Uncle_Fu`, `Dylan`, `Eric` (Chinese), `Ono_Anna` (Japanese) and `Sohee` (Korean). All speak German and English, with their native language as a possible accent. In a Whisper test over three German sentences, `Dylan`, `Sohee` and `ref:de_female_1` reached 1.2 % WER, `Uncle_Fu` 2.4 %, `Aiden` 3.6 %, `Ryan` 6.3 % and `Serena` 10.7 %. Whisper does not measure accent.

Cloned voices are called `ref:<id>`. With cloning on, every WAV file with a transcript in the voices folder becomes a voice at startup. An unknown voice falls back to the default of the language.

## Style instructions and speed

There are no inline tags. Style instructions go as free text, German or English, into the `instruct` field of the request:

```json
{"text": "...", "lang": "de", "voice": "Dylan", "instruct": "Sprich langsam und ruhig, wie ein Hörbuchsprecher."}
```

Instructions work only with preset speakers; for `ref:` voices the server ignores the field. If a request carries an instruction without a known voice, German uses `Dylan` instead of the clone.

The model has no speed control, so the server ignores `speed`. The daemon sends speed 1.0 and time-stretches the audio to the requested pace (see "Speed and loudness" in `neural/README.md`). A style instruction such as "Sprich schneller" still works on top for preset speakers, but its tempo is not exact.

## How it streams fast enough

For each frame (80 ms of audio) the talker predicts the first of 16 codebooks, then a small code predictor fills in the other 15 in 15 sequential steps. Run eagerly, that costs about 145 ms per frame, almost all of it Python and kernel launch overhead, which makes the model slower than real time (factor 1.8).

`server.py` therefore records a whole frame step as one CUDA graph: the 15 code predictor steps plus the talker step with a static KV cache of 3072 positions. That brings the real-time factor to about 0.4 without `torch.compile`. The first audio chunk leaves after 4 frames (320 ms of audio), later chunks every 10 frames. The codec decoder gets 72 frames of left context, so chunk boundaries stay inaudible.

## Configuration

Environment variables, or `<data folder>/qwen3/config.json` with lower-case keys without prefix (for example `{"clone": 0}`):

| Variable | Default | Effect |
|---|---|---|
| `QWEN3_MODEL` | `1.7B` | `0.6B` loads the small CustomVoice model, without instructions (not measured) |
| `QWEN3_CLONE` | `1` | `0` skips the cloning model: 2 GB less VRAM, German then uses `Dylan` |
| `QWEN3_FIRST` | `4` | frames until the first chunk |
| `QWEN3_EMIT` | `10` | frames per further chunk |
| `QWEN3_GRAPH` | `1` | `0` disables the CUDA graphs (debugging) |

## Setup and test

```powershell
powershell -ExecutionPolicy Bypass -File neural\qwen3\setup.ps1
python neural\launcher.py start qwen3
python neural\gpulock.py python neural\bench.py qwen3
python neural\gpulock.py python neural\asr_check.py qwen3
python neural\launcher.py stop qwen3
```

`setup.ps1` creates the venv with Python 3.12 and installs PyTorch 2.11.0 (CUDA 12.8), transformers 4.57.3 and the fork. It downloads the weights into the Hugging Face cache (about 6 GB) and checks the imports. `-RefVoices` also downloads the VoiceDesign model and Whisper, which `make_ref_voices.py` needs to regenerate the shared reference voices. `samples.json` contains the same sentences with and without instructions (audiobook, excited, whispering, sad, angry) and the cloned voices.

## Notes for clients

The server returns 24 kHz mono s16le and streams; the first audio comes after about 0.2 s. A request should be a sentence or a short paragraph. Beyond about 1500 characters the length limit (60 plus 2 frames per character, at most 3072 cache positions) cuts the end. Requests are processed one after another.

## Known issues

Output is sampled, so every synthesis differs slightly, including tempo and pauses. A length limit guards against endless babbling. Preset speakers often mispronounce foreign names such as "Kullback". At startup the package logs "SoX could not be found" and "flash-attn is not installed"; both are harmless (SoX is only needed for fine-tuning, SDPA replaces flash-attn).
