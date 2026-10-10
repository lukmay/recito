# Changelog

Each release has a section here. The section of the version in `VERSION` becomes part of the release notes on GitHub.

## 1.1.0

Installing Recito no longer needs Python, a terminal or administrator rights.

- Windows: `Recito-Setup.exe` installs everything per user, including Python and ffmpeg, starts Recito and adds it to the Start menu and to Windows "Apps" for uninstalling.
- Linux: one `Recito-x86_64.AppImage` sets itself up on first start (Python, speech engines, voices, launcher, autostart and shortcuts on KDE and GNOME) and updates an older install. A `curl ... | bash` one-liner does the same from a terminal.
- Missing optional system packages show up as one copy-paste command instead of an error.

## 1.0.0

Recito reads the text you select, or a region of the screen, aloud on Windows 11 and Fedora KDE Plasma 6.

- Global hotkeys read the selection or an OCR'd screen region, at normal or fast speed, with pause and stop. The first sentence plays while the rest is still being synthesized.
- Voices from Piper (offline), Edge TTS, ElevenLabs, Windows and Microsoft Speech voices, and optional local neural models such as Kokoro, Chatterbox and Fish Audio. Chatterbox and Fish Audio can read in a voice you record.
- 13 reading languages with automatic language detection per text or per sentence.
- Rules clean up what is read: Markdown, Obsidian marks, LaTeX and formulas. Every rule can be edited, and the `tts-rules` command line manages them.
- A setup assistant recommends an engine for your hardware and walks through languages, voice, speed and shortcuts on the first start.
- A tray app with a settings window in English, German, French, Spanish or Italian, light and dark themes, and autostart.
