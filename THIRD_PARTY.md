# Third-party components

Recito contains code and one bundled font. Models, voices and most libraries are downloaded by the install and setup scripts and are not part of the repository; their licenses still apply to anyone who installs and uses them. Licenses can change between releases. The entries below describe the pinned versions; check the linked source before redistributing anything.

## Bundled in this repository

| Component | License | File |
|---|---|---|
| Material Icons Round font (Google) | Apache-2.0 | `bin/fonts/MaterialIconsRound-Regular.otf`, license in `bin/fonts/LICENSE-material-icons.txt` |

## Models and voices downloaded by the scripts

| Component | Used by | License | Source |
|---|---|---|---|
| Piper TTS engine (`piper-tts`, piper1-gpl) | daemon | GPL-3.0 | [OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl) |
| Piper voices (`de_DE-thorsten-low`, `en_US-amy-low`, others) | daemon | per voice, see each voice's `MODEL_CARD` | [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices) |
| Kokoro-82M v1.0 | `kokoro2` | Apache-2.0 | [hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) |
| German Kokoro fine-tunes (Thorsten-Voice, kikiri Martin and Victoria, Kerstin) | `kokoro2` | Apache-2.0; Thorsten and Kerstin training data CC0 | model cards of `Thorsten-Voice/Kokoro`, `kikiri-tts/*`, `cryptomilk/kokoro-german-kerstin` |
| kokoro and misaki forks with German G2P | `kokoro2` | Apache-2.0 | [semidark/kokoro](https://github.com/semidark/kokoro), [semidark/misaki](https://github.com/semidark/misaki) |
| Chatterbox, Chatterbox-Turbo, Multilingual V3 | `chatterbox` | MIT | [resemble-ai/chatterbox](https://github.com/resemble-ai/chatterbox) |
| Kartoffelbox (optional German fine-tune) | `chatterbox` | MIT | `SebastianBodza/Kartoffelbox`, mirrored as `mister2314/kartoffelbox-de` |
| Perth watermarker | `chatterbox` | see repository | [resemble-ai/Perth](https://github.com/resemble-ai/Perth) |
| Qwen3-TTS (1.7B CustomVoice, 0.6B Base, 1.7B VoiceDesign) | `qwen3` | Apache-2.0 | [QwenLM/Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) |
| Qwen3-TTS streaming fork | `qwen3` | see repository | [dffdeeq/Qwen3-TTS-streaming](https://github.com/dffdeeq/Qwen3-TTS-streaming) |
| Fish Audio S2 Pro weights | `fish` | Fish Audio Research License: research and non-commercial use only, commercial use needs a license from Fish Audio | [fishaudio/s2-pro](https://huggingface.co/fishaudio/s2-pro) (`LICENSE.md` is downloaded with the weights) |
| fish-speech code | `fish` | see repository | [fishaudio/fish-speech](https://github.com/fishaudio/fish-speech) |
| Step-Audio-EditX and Step-Audio-Tokenizer | `stepaudio` | Apache-2.0 | [stepfun-ai/Step-Audio-EditX](https://github.com/stepfun-ai/Step-Audio-EditX) |
| Whisper large-v3-turbo via faster-whisper | `asr_check.py`, voice transcripts | MIT | [openai/whisper](https://github.com/openai/whisper), [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) |
| Hi-Fi TTS dataset (voices `cast_hifi_*`) | reference voices | CC BY 4.0; attribution to the LibriVox readers and the dataset authors required | [OpenSLR 109](https://www.openslr.org/109/) |

The four shared reference voices `de_female_1`, `de_male_1`, `en_female_1` and `en_male_1` are generated with Qwen3-TTS VoiceDesign from text descriptions and do not imitate a real person.

## Libraries

The Python packages installed by `install.sh` and `install.ps1` include PySide6 (LGPL-3.0), piper-tts (GPL-3.0), edge-tts, lingua-language-detector, onnxruntime, sounddevice, soundcard, pyperclip, pywin32 and winsdk. The Linux setup additionally uses keyd (built from source), tesseract and ffmpeg, and `neural/kokoro2/setup.sh` installs espeak-ng. Each keeps its own license.

## Online services

Edge-TTS uses Microsoft's online speech service through the `edge-tts` package, which is not an official Microsoft API. ElevenLabs is a paid service under the ElevenLabs terms; users bring their own API key. The ElevenReader window (`bin/tts_reader.py`) uses an undocumented ElevenReader endpoint with the user's own login.
