# Credits for the Recito hero video

The hero video at the top of the README is rendered with HyperFrames. This file lists every third-party asset in it.

## Music

| Use | Track | Author | Source | License |
|---|---|---|---|---|
| Music bed (121 BPM section, from 0:12 of the file) | "Motivating Mornings" (Mixkit track 33) | Ahjay Stelino | https://mixkit.co/free-stock-music/ | Mixkit Stock Music Free License, https://mixkit.co/license/#musicFree |

## Sound effects (all Mixkit, Mixkit Sound Effects Free License, https://mixkit.co/license/#sfxFree)

| Use | Effect | Mixkit ID |
|---|---|---|
| Scene cuts | "Fast whoosh transition" | 1490 |
| Selection | "Select click" | 1109 |
| Ctrl and Alt keys | "Hard single key press in a laptop" | 2542 |
| P key | "Single key type" | 2533 |
| Mascot pop | "Bubble pop up alert notification" | 2357 |
| Wi-Fi switch off | "On or off light switch tap" | 2585 |
| Language switch | "Dry pop up notification alert" | 2356 |
| End card chime | "Magic marimba" | 2820 |

Pixabay would have been the first choice, but its download pages sit behind a bot check that cannot be scripted. Mixkit's licenses allow use in published videos without attribution. The raw files are not part of the repository.

## Voices

| Voice | Engine | Notes |
|---|---|---|
| "Sarah" (ElevenLabs premade voice), model `eleven_multilingual_v2` | ElevenLabs text-to-speech API with word timestamps | The core line, the formula line and the DE/EN/FR language lines. Used only inside the video. Needs an API key, which is not part of the repository |
| en-US-AriaNeural, de-DE-KatjaNeural, fr-FR-DeniseNeural | Microsoft Edge TTS via the `edge-tts` package | Optional offline fallback (`voices.py edge`), not used in the current cut |
| Cloned voice `ref:cast_hifi_92` | Chatterbox, local server | Reference sample from the Hi-Fi TTS dataset (EN), CC BY 4.0, credited on screen in the cloned-voice scene. Hi-Fi TTS: Bakhturina et al., NVIDIA, https://www.openslr.org/109/ |

## Fonts

- Nunito (headlines), SIL Open Font License 1.1
- Inter (body), SIL Open Font License 1.1
- Material Icons Round (icon glyphs), Apache License 2.0

## Tooling

HyperFrames by HeyGen, Apache License 2.0, https://github.com/heygen-com/hyperframes. GSAP 3 (standard license, free for this use), https://gsap.com.
