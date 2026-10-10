# kokoro2: Kokoro with German voices

This model server runs Kokoro-82M with PyTorch, on CUDA if available and otherwise on the CPU, and streams sentence by sentence. The app shows it as "Kokoro"; the engine key in `voices.conf` is `kokoro2`. Older versions also had an `engine=kokoro` on kokoro-onnx; when this server is installed, the daemon reads that setting as `kokoro2` and takes over its `kokoro_de` and `kokoro_en` voices, and otherwise it uses Piper. English uses the official model; German offers five voices from four community fine-tunes. The protocol is the shared one from `../server_base.py`.

## Models

English runs on `hexgrad/Kokoro-82M` v1.0 (January 2025), the latest official release. English voices: `af_heart` (default), `af_bella`, `af_nicole`, `af_aoede`, `af_kore`, `af_sarah`, `am_michael`, `am_fenrir`, `am_puck` (US) and `bf_emma`, `bf_isabella`, `bm_george`, `bm_fable` (British, through the UK pipeline).

According to their model cards, all German models come from the training recipe `semidark/kikiri-tts`: a German base model, then one fine-tune per speaker. Each German voice is therefore a separate model with 82 M parameters.

| Voice | Source | Training data | Remarks |
|---|---|---|---|
| `thorsten` (default) | `Thorsten-Voice/Kokoro`, epoch 5 | 12,283 real recordings | lowest WER in the voice comparison |
| `thorsten_ep10` | same repository, epoch 10 | as above | slightly faster; less natural than epoch 5 according to the model card |
| `martin` | `kikiri-tts/kikiri-german-martin` | 627 synthetic sentences | same weights as `kokoro-martin.onnx` of the former kokoro-onnx engine |
| `victoria` | `kikiri-tts/kikiri-german-victoria` | 455 synthetic sentences | female |
| `kerstin` | `cryptomilk/kokoro-german-kerstin` | 2.3 h of real recordings | female, converted from the training checkpoint during setup |

An unknown voice, or a voice of the other language, falls back to the default of the requested language. All models are Apache-2.0; the training data of Thorsten and Kerstin is CC0.

## German pronunciation

Phonemization uses the forks `semidark/misaki` and `semidark/kokoro`. They add German text normalization that spells out numbers, dates, times, percentages and euro amounts. The server adds two fixes. It replaces the short ü (`ʏ`) with `y`, because `ʏ` is missing from the Kokoro vocabulary. It also hyphenates compounds that end in an English word ("Echtzeitsprachverarbeitungssoftware" becomes "...verarbeitungs-Software"); without the hyphen espeak pronounces the whole word in English.

Pronunciation markup works in both languages: `[word](/IPA/)` replaces the pronunciation of a word, for example `[Kokoro](/kˈOkəɹO/)`. Kokoro has no emotion tags and no style instructions. `speed` is native (0.5 to 2.0).

## Measurements

Measured on an RTX 4080-class GPU with `bench.py` and Whisper large-v3-turbo.

| Quantity | Value |
|---|---|
| First audio, warm | 80 to 160 ms, median about 115 ms |
| First audio after a switch to an unloaded German voice | 0.8 to 1.2 s |
| Real-time factor | 0.02 to 0.04 |
| Start until ready | 11 to 13 s |
| VRAM per loaded model | 318 MB (fp32) plus about 100 MB peak during inference |
| VRAM after start (English plus `thorsten`) | 654 MB allocated, 728 MB reserved |

The server keeps at most `KOKORO2_MAX_DE` German models loaded and unloads the least recently used one first. `models.json` sets this to 1, so a voice switch never holds two German models. `KOKORO2_PRELOAD=all` loads all five at startup. The models run in fp32; bf16 autocast was slower.

Word error rate over the shared samples from `samples_common.json` without the two number samples: 2 to 3 % in German and 0 % in English. The same samples through the former kokoro-onnx engine (with `martin`) reached 23 % in German, for two reasons. kokoro-onnx kept espeak's language switch markers in the phoneme string, so "Server" was spoken with audible markers. It also dropped unknown phonemes, so the short ü disappeared ("Brücke" lost its vowel). This gap is why the app removed that engine.

## Setup and test

```powershell
powershell -ExecutionPolicy Bypass -File neural\kokoro2\setup.ps1
python neural\gpulock.py python neural\bench.py kokoro2
python neural\gpulock.py python neural\asr_check.py kokoro2
```

On Linux: `bash neural/kokoro2/setup.sh`. The script creates the venv `<data folder>/kokoro2/venv` (Python 3.12, torch 2.11.0), clones the forks at pinned commits into `kokoro2/src`, downloads the weights at pinned Hugging Face revisions and converts Kerstin to `kokoro2/models/kerstin.pth`. The Kerstin checkpoint is a one-time 1.9 GB download.

## Notes for clients

The server returns 24 kHz, 16-bit mono. It splits text at sentence boundaries, and at commas above 220 characters, sends each sentence as soon as it is ready and inserts 120 ms of silence between sentences of one request. Whole paragraphs are fine as a request.

## Known issues

espeak pronounces some English technical words in German text the German way ("Learning"); markup fixes individual words. At speed 1.3 long proper names such as "Kullback-Leibler" get slurred. `kerstin` had the highest WER in the voice comparison (12 % on one sentence); the cause is unknown.
