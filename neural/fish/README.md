# Fish Audio S2 Pro (key `fish`)

Local model server for Fish Audio S2 Pro, speaking the shared protocol from `../server_base.py`. The model clones a voice from a reference recording with transcript. Inline tags in square brackets control whispering, laughing, pauses and mood in the middle of a sentence.

Fish is above the default VRAM budget (`vram_mb` 9800 in `models.json`, budget 5500 MB), so the launcher refuses to start it. To use it, close other GPU programs and raise `neural_vram_max_mb` in `voices.conf` to 10000.

## License

Fish Audio S2 Pro is under the Fish Audio Research License: research and non-commercial use are free, commercial use needs a separate license from Fish Audio. Check the license before shipping or using it commercially; see [THIRD_PARTY.md](../../THIRD_PARTY.md).

## Model

S2 Pro (March 2026, technical report arXiv 2603.08823) has two autoregressive parts. A large transformer based on Qwen3-4B (36 layers) generates one semantic token per frame; a small transformer (4 layers) adds nine more codebook entries for acoustic detail. Together that is about 4.4 B parameters. A frame is one audio step of the model, 21.5 frames per second. The codec turns tokens into 44.1 kHz audio.

Pinned versions: fish-speech commit `214da3cd` in `<data folder>/fish/src`, weights `fishaudio/s2-pro` revision `1de9996b` in `<data folder>/fish/checkpoints/s2-pro`. The weights take 11 GB on disk. fish-speech pins torch 2.8.0; the setup installs torch 2.11.0 like the other model servers.

## What the server does differently from fish-speech

The fish-speech inference scripts require at least 24 GB of VRAM and only return audio after each text block. `server.py` uses the same modules and weights with its own generation loop.

- Context is limited to 4096 tokens instead of 32768, which saves almost 6 GB for mask and KV cache.
- The text embedding table (155,776 rows, 0.8 GB) stays in system RAM, because only the prefill needs it. The GPU holds only the 4097 rows the model may output: the 4096 semantic tokens and the end token `<|im_end|>`.
- The codec skips three unused 1 GB masks.
- One generation step (large transformer, ten small-transformer steps, sampling) runs as a CUDA graph, 42 ms instead of 79 ms per frame at full context. There are two graphs, for contexts up to 1024 and up to 4096 tokens.
- Sampling matches the fish-speech API server: temperature 0.8, top-p 0.8, top-k 30, plus its protection against token repetition.

Audio streams. The first block leaves after 8 frames (0.37 s of audio), then blocks of 16, 32 and at most 48 frames. The codec is causal, so each block is decoded with 48 frames of left context and only the new part is sent. Texts over 220 characters are split at sentence boundaries and generated one after another.

## Measurements

Measured on an RTX 4080-class GPU with `bench.py`, second run of each sample, reference voices `de_female_1` and `en_female_1`.

| Quantity | Value |
|---|---|
| VRAM | 9.2 GB after loading, peak 9.7 GB |
| First audio | 380 to 430 ms, independent of sentence length |
| First audio, first sentence after start | 0.5 to 3 s |
| Real-time factor | 0.74 to 0.79 |
| Load time | 150 to 380 s (measured while other models were loading) |
| WER, shared samples without numbers | German 1.8 %, English 0 % |

The limit is memory bandwidth: each frame reads about 15 GB of weights. With another process using the GPU the real-time factor rises above 1, and reading aloud stutters. The codes of the reference voices are cached in `<data folder>/fish/refcache/`, so later starts only load them.

German is a second-tier language according to the model card. Whisper transcribes the German test sentences almost word for word, including long compounds, umlauts, mixed German and English, dates, times and euro amounts.

## Voices

The model has no built-in voices; without a reference it picks a random voice per sentence. The server therefore clones from reference recordings (`<id>.wav` plus `<id>.txt` with the transcript), first from the shared `<data folder>/voices/`, then from `<data folder>/fish/voices/`. Voice ids are `ref:<id>`. New files appear after a server restart.

Defaults are `ref:de_female_1` for German and `ref:en_female_1` for English, with the male voices as fallback. Unknown voices fall back to the default of the language; `random` forces the random voice.

## Tags and instructions

Tags go in square brackets into the text and apply from that position on. The server passes them to the model unchanged. The fish-speech README lists these:

```
[pause] [short pause] [emphasis] [laughing] [laughing tone] [chuckle] [chuckling]
[inhale] [exhale] [sigh] [tsk] [clearing throat] [panting] [whisper] [low voice]
[low volume] [volume up] [volume down] [loud] [shouting] [screaming] [excited]
[excited tone] [delight] [angry] [sad] [surprised] [shocked] [moaning] [singing]
[echo] [interrupting] [audience laughter] [with strong accent]
```

Free descriptions such as `[whisper in small voice]` or `[professional broadcast tone]` work too. The tags are English and also work in German text. The `instruct` field becomes a tag in front of the text: `"instruct": "professional broadcast tone"` gives `[professional broadcast tone] <text>`.

`speed` is ignored: S2 Pro has no speed control. The daemon time-stretches the audio to the requested pace instead (see "Speed and loudness" in `neural/README.md`).

## Setup and test

```
powershell -ExecutionPolicy Bypass -File neural\fish\setup.ps1
python neural\gpulock.py python neural\bench.py fish
python neural\gpulock.py python neural\asr_check.py fish
```

`setup.ps1` clones fish-speech, creates the venv (Python 3.12), downloads the weights and checks import and CUDA. Environment variables for experiments: `FISH_MAX_LEN` (context length, default 4096), `FISH_NO_GRAPHS=1` (no CUDA graphs, about half the speed), `FISH_CKPT` (another checkpoint folder).

## Known issues

When the GPU is already busy with other programs, loading can take many minutes and the graphics driver can stop responding, most likely because Windows pages GPU memory into system RAM. This is why the launcher blocks Fish by default.

Each text block is generated without the previous one as context. The voice stays the same through the reference, but intonation can jump at block boundaries. The length per block is limited (2.6 frames per spoken character plus a margin per tag); if the model loops, the server stops there and logs a warning.

Output is 44,100 Hz mono s16le. Single sentences or paragraphs of up to about 220 characters work best.
