# Chatterbox (key `chatterbox`)

Chatterbox by Resemble AI is MIT-licensed. The model server loads two sub-models into one process. English runs on Chatterbox-Turbo (350 M parameters), which understands paralinguistic tags such as `[laugh]` and decodes in a single step. German runs on Chatterbox-Multilingual V3 (500 M parameters), which covers 23 languages but has no tags.

## Measurements

Measured on an RTX 4080-class GPU against the running server; WER is the Whisper back-transcription of the shared `bench.py` samples.

| Language | First audio (warm) | Real-time factor | WER |
|---|---|---|---|
| German (Multilingual V3) | 290–310 ms | 0.38–0.46 | 5.1 % |
| English (Turbo) | 260 ms | 0.33 | 0 % |

The server streams. It generates speech tokens one at a time and decodes them in overlapping windows; the first audio comes after 12 tokens (`CB_FIRST_CFG`, `CB_FIRST_TURBO`). Each decode step runs as a recorded CUDA graph with a static KV cache (`DecodeGraph` in `server.py`), which lowers the time per token from 22 to 6.2 ms in German and from 11 to 4.2 ms in English. A sentence too long for the 1600 positions of the static cache runs without the graph.

Both sub-models load at startup (`CB_LAZY=0` in `models.json`), so the first German sentence does not wait for a load. Startup takes about 23 s. The server uses 5.0 GB according to `nvidia-smi`, with peaks of 5.2 GB; the launcher caps it at 5200 MB.

The German WER comes from two samples: Whisper hears "Kohlbeck" instead of "Kullback", and it writes "Echtzeit-Sprachverarbeitungssoftware" with a hyphen.

## Voices

The default German voice is `ref:de_female_1`, the default English voice `builtin`, the voice that ships with Turbo. Every file in `<data folder>/voices/` is available as `ref:<id>`. The server clones the voice from the WAV and caches the conditioning, so it is computed once per voice.

## Tags and parameters

Tags work in English only, through Turbo. They go in square brackets into the text:

```
[laugh] [chuckle] [sigh] [gasp] [cough] [clear throat] [sniff] [groan] [shush]
[crying] [whispering] [happy] [angry] [sarcastic] [surprised] [fear] [dramatic]
```

The `instruct` field (`chatterbox_instruct` in `voices.conf`) takes parameters as `name=value`:

```
exaggeration=0.8 cfg=0.3          more expressive
exaggeration=0.3 cfg=0.5          calmer
model=kartoffel                   German community fine-tune instead of Multilingual V3
model=mtl3                        English through Multilingual instead of Turbo
```

Free text such as "excited" or "calm" (also German "aufgeregt", "ruhig") sets the same values as the first two lines.

## Setup and test

```
powershell -ExecutionPolicy Bypass -File neural\chatterbox\setup.ps1
python neural\gpulock.py python neural\bench.py chatterbox
python neural\gpulock.py python neural\asr_check.py chatterbox
```

Environment variables change the behavior; the list is at the top of `server.py` (for example `CB_DE_MODEL=kartoffel`, or `CB_LAZY=1` to load the second sub-model on first use).

## Open points

The gallery sample `de_kartoffel` compares Multilingual V3 with the `kartoffel` fine-tune; which one sounds better in German has not been decided by listening.
