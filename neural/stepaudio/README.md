# Step-Audio-EditX (key `stepaudio`)

Step-Audio-EditX by StepFun is Apache-2.0 licensed and has 3.5 B parameters. The model clones a voice from reference audio plus transcript and can then edit the result in further passes: emotion, speaking style, speed and paralinguistic sounds such as laughing or sighing. It is trained mainly on Chinese and English; German is not officially supported.

Pinned versions: weights `stepfun-ai/Step-Audio-EditX` at revision `5fe2f8a`, torch 2.11.0 with CUDA 12.8.

## Status: blocked by default

In a benchmark on an RTX 4080-class GPU the server computed 16 to 26 times slower than real time while keeping the GPU fully busy, which made the desktop unusable. The launcher therefore blocks it through the VRAM budget (`vram_mb` 8600 in `models.json`, budget 5500 MB). The cause of the slow decoding is not known; the model plus its caches left almost no free memory on a 16 GB card.

| Quantity | Value |
|---|---|
| Load time | 309 s |
| VRAM after loading | 7.4 GB, peak 8.1 GB |
| First audio | 1.1 s |
| Real-time factor | 16 to 26 |

## How the server computes

The official repository needs vLLM, which is not available on Windows. `step_llm.py` runs the language model itself, with a static KV cache and one CUDA graph per decode step. The server caches the voice prefix per voice, so each sentence only computes the target text. CosyVoice flow and the HiFT vocoder then produce 24 kHz mono audio in streaming mode.

## Voices

Available are the shared reference voices from `<data folder>/voices/` (`de_female_1`, `de_male_1`, `en_female_1`, `en_male_1`) and the example voice from the repository (`en_repo_male`). Defaults are `de_female_1` and `en_female_1`.

## Tags and edits

Paralinguistic tags go in square brackets into the text, for example `[Laughter]`, `[Sigh]`, `[Breathing]`, `[Uhm]`, `[Surprise-oh]` or `[cough]`. The full list is `TAGS` in `server.py`.

The `instruct` field (`stepaudio_instruct` in `voices.conf`) starts extra edit passes. Each pass runs the language model over the whole sentence again and does not stream, so it adds noticeable latency. Commas separate several edits; `x2` repeats one:

```
emotion=happy                 happy, angry, sad, excited, fear, surprised, ...
style=whisper x2              whisper, news, story, gentle, serious, radio, ...
speed=faster                  faster, slower, more faster, more slower
para                          add tags from the text in a separate pass
```

The lists are `EMOTIONS` and `STYLES` in `server.py`. The `speed` value from the daemon needs no extra pass; the server interpolates the mel spectrogram in the vocoder.

## Setup

```powershell
powershell -ExecutionPolicy Bypass -File neural\stepaudio\setup.ps1
```

The script installs everything under `<data folder>/stepaudio/` (venv, source, models, voice cache), about 9 GB of weights.
