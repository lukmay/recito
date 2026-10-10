<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/brand/logo-lockup-dark.svg">
    <img src="assets/brand/logo-lockup.svg" alt="Recito" height="72">
  </picture>
</p>

<h3 align="center">Select text. Press a key. Listen.</h3>

<p align="center">
Local and private or online, in your own voice, in 13 languages.<br>
For Windows 11 and Fedora KDE Plasma 6.
</p>

https://github.com/user-attachments/assets/2f2886c4-ca6d-4cb7-ad32-8ed2e68c6dd7

Recito reads the text you select in any app. Press a hotkey and the first sentence plays while the rest is still being synthesized. Draw a rectangle on the screen and Recito reads the text it recognizes there (OCR).

## Why Recito

**It stays on your computer when you want it to.** Piper, Kokoro and the neural models run locally and need no internet. Edge-TTS and ElevenLabs are online choices, and Piper reads any sentence they fail on.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/onboarding-engine-dark.webp">
    <img src="docs/images/onboarding-engine-light.webp" alt="Setup step that recommends an engine for the detected hardware" width="720">
  </picture>
</p>

**It reads in your own voice.** Record 10 to 15 seconds, or import a file, and the cloning engines Chatterbox and Fish Audio speak with that voice.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/new-voice-dark.webp">
    <img src="docs/images/new-voice-light.webp" alt="Dialog for creating a voice from a recording" width="720">
  </picture>
</p>

**It speaks the language of the text.** Pick the languages you read, out of 13, and Recito detects the language of each text or sentence and switches the voice.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/languages-dark.webp">
    <img src="docs/images/languages-light.webp" alt="Chips for the 13 reading languages" width="720">
  </picture>
</p>

**It reads notes and formulas cleanly.** Rules remove Markdown and Obsidian marks and turn LaTeX into words, so `**SQL**` is read as "SQL" and no asterisk is spoken. Every rule can be edited, and you can add your own.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/rules-dark.webp">
    <img src="docs/images/rules-light.webp" alt="Text clean-up page with rule groups and a Try it box showing a text before and after" width="720">
  </picture>
</p>

## Quick start

### Download the latest release

Go to the [latest release](https://github.com/lukmay/recito/releases/latest) and download the file for your system. No git is needed.

Windows 11 (Python 3.12 or 3.13, 64-bit): download `Recito-<version>-windows.zip`, extract it and double-click `Install Recito.cmd`. The window stays open at the end so you can read the result.

Fedora KDE Plasma 6: download `Recito-<version>-linux.tar.gz`, then:

```bash
tar xzf Recito-<version>-linux.tar.gz
cd Recito-<version>-linux
./install.sh
```

To update, install the newer release over the old one. `SHA256SUMS.txt` on the release page lets you check the downloads. The release lists what changed; see also [CHANGELOG.md](CHANGELOG.md).

### From the repository

Use this to work on the code. Get it first:

```bash
git clone https://github.com/lukmay/recito.git
cd recito
```

The install scripts are the same ones the release contains. Windows 11:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The script creates a venv in `%LOCALAPPDATA%\piper-venv`, copies the program to `%LOCALAPPDATA%\piper-bin`, downloads the default Piper voices, writes `%APPDATA%\piper\voices.conf`, enables autostart and starts the tray app. Details are in [WINDOWS.md](WINDOWS.md).

Fedora KDE Plasma 6:

```bash
./install.sh
```

The script installs the system packages (with sudo), creates `~/.local/share/piper-venv` and downloads the voices. It installs the daemon as the systemd user service `piper-read`, registers the KDE shortcuts and builds [keyd](https://github.com/rvaiya/keyd) for the AltGr hotkeys. Log out and back in once afterwards. `./uninstall.sh` removes the setup, and `--purge` also deletes voices and the venv.

Both scripts are idempotent. Run them again after changing files in `bin/`, `scripts/` or `neural/`. A setup assistant opens on the first start and walks through languages, engine, voice, speed and shortcuts.

Recito was called TTS-Vorlesen before. Running the install script again removes the old autostart entry, Start menu shortcut, launchers, KDE shortcut bindings and icons. Settings in `%APPDATA%\piper` or `~/.local/share/piper` stay as they are.

The tray icon shows one of four states, idle, speaking, paused or error.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/tray-dark.webp">
    <img src="docs/images/tray-light.webp" alt="The four tray icon states" width="560">
  </picture>
</p>

## Usage

Default hotkeys on Windows (changeable in the settings window):

| Hotkey | Action |
|---|---|
| `Ctrl+Alt+P` / `Ctrl+Alt+Shift+P` | Read the selection / faster |
| `Ctrl+Alt+O` / `Ctrl+Alt+Shift+O` | Read a screen region via OCR / faster |
| `Ctrl+Alt+L` | Pause or resume |
| `Ctrl+Alt+S` | Stop |

On Linux keyd maps AltGr combinations to shortcuts that KDE can bind: `AltGr+P` reads the selection, `AltGr+O` reads a screen region, `AltGr+Shift` makes either faster and `AltGr+L` pauses. Two mouse side buttons read the selection and start OCR. keyd matches the mouse by its USB ID in `keyd/default.conf`; replace the example ID with your own (`sudo keyd monitor` shows it).

Left-click the tray icon to open the settings window, right-click for the menu. The window chooses engine and voices, previews each voice, sets speed and pauses, records hotkeys and creates reference voices for the cloning models.

## Engines

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/voice-dark.webp">
    <img src="docs/images/voice-light.webp" alt="Voice and engine page with one card per engine" width="720">
  </picture>
</p>

| Engine | `engine=` | Runs | GPU | Voice cloning | Notes |
|---|---|---|---|---|---|
| Piper | `piper` | local | CPU only | no | Always loaded, reads any sentence another engine fails on |
| Kokoro | `kokoro2` | local | optional, about 1.1 GB VRAM | no | German and English, first audio after about 0.1 s |
| Chatterbox | `chatterbox` | local | NVIDIA, about 5.2 GB VRAM | yes | Sounds the most natural in listening tests |
| Qwen3-TTS | `qwen3` | local | NVIDIA, about 5.3 GB VRAM | no | Follows style instructions in words |
| Fish Audio S2 Pro | `fish` | local | NVIDIA, about 9.8 GB VRAM | yes | Above the default VRAM budget |
| Step-Audio-EditX | `stepaudio` | local | NVIDIA, about 8.6 GB VRAM | no | About 20 times slower than real time |
| Edge-TTS | `edge` | online | none | no | Microsoft neural voices, free, needs ffmpeg |
| ElevenLabs | `elevenlabs` | online | none | no | Paid per character, API key from the user |
| SAPI5 | `sapi` | local | none | no | Windows voices, Windows only |
| MS Speech Platform | `mssp` | local | none | no | Via balcon under Wine, Linux only, see `linux-mssp/` |

The neural models are optional, installed one by one and run as separate model servers. [neural/README.md](neural/README.md) has measurements, the VRAM budget and the setup steps; [neural/kokoro2/README.md](neural/kokoro2/README.md) covers Kokoro. The setup assistant checks your hardware and recommends an engine.

If the chosen engine fails for a sentence, Piper reads that sentence. With `fallback=off` in `voices.conf` the daemon skips the sentence instead, so the voice never changes mid-text. The daemon reads the former `engine=kokoro` (kokoro-onnx) as `kokoro2` when the Kokoro model server is installed and as `piper` otherwise.

## Features

**Text clean-up rules.** One list of rules, in four groups, runs from top to bottom before a text is read. See [Text clean-up and rules](#text-clean-up-and-rules).

**AI assistant command line.** The `tts-rules` command validates, tests and undoes rule changes. Assistants such as Claude Code or Codex can manage rules through it, and a button in the settings window copies a ready prompt.

**13 reading languages.** German, English, French, Spanish, Italian, Dutch, Portuguese, Polish, Swedish, Danish, Czech, Russian and Turkish. See [Languages](#languages).

**Setup assistant.** It detects the hardware, recommends an engine, plays sample voices and records your shortcuts.

**Speed and loudness.** Every engine reaches the same pace, and the daemon normalizes loudness to -18 LUFS. See [Speed and loudness](#speed-and-loudness).

## Languages

The reading languages are the languages your texts are in. You pick them in the setup assistant or under Voice & engine, and `voices.conf` stores them in order, for example `read_langs=de,en,fr`. Without that line the app uses `de,en`. With the voice set to `auto`, the daemon detects the language with [lingua](https://github.com/pemistahl/lingua-py), either once per text (`lang_detect=text`) or per sentence (`lang_detect=sentence`). Detection only chooses among the reading languages, so a French text is never read as Dutch when you do not read Dutch.

Each reading language has its own voice per engine: `<lang>=<onnx name>` for Piper (`fr=fr_FR-siwis-medium`) and `<engine>_<lang>` for the others (`edge_fr`, `chatterbox_fr`).

| Engine | Reading languages |
|---|---|
| Piper, Edge, ElevenLabs | all of them (Piper with a downloaded voice per language) |
| Chatterbox | all except Czech |
| Fish Audio S2 Pro | German, English, French, Spanish, Italian, Dutch, Portuguese, Polish, Russian |
| Qwen3-TTS | German, English, French, Spanish, Italian, Portuguese, Russian |
| Kokoro, Step-Audio-EditX | German and English |
| Windows voices, MS Speech Platform | the languages of the installed voices |

Engine cards show how many of your reading languages an engine speaks. When a sentence is in a language the engine lacks, the daemon reads it with the Piper voice of that language. If Piper has no voice for it, Edge reads it with its default voice, which needs the internet. With `engine=piper` the daemon stays offline and uses the default Piper voice. An Edge failure turns the Edge step off for the rest of the text.

For each reading language without a Piper voice, the settings window and the setup assistant offer one voice (about 63 MB) from the [Piper voice collection](https://huggingface.co/rhasspy/piper-voices). The download runs in the background and is used from the next start of the daemon.

The app language is separate. The settings window, the tray menu and the setup assistant come in English, German, French, Spanish and Italian; [bin/i18n/README.md](bin/i18n/README.md) explains how to add one.

## Configuration

All settings live in `voices.conf`: `%APPDATA%\piper\voices.conf` on Windows, `~/.local/share/piper/voices.conf` on Linux. The environment variable `PIPER_DIR` moves this folder. [config/voices.conf](config/voices.conf) is the commented template.

The ElevenLabs API key is never stored in `voices.conf`. The settings window writes it to `elevenlabs.key` in the same folder, and the environment variable `ELEVENLABS_API_KEY` overrides it.

## Speed and loudness

Speed is a pace factor, so 1.25 reads a text in 1/1.25 of the time. `voices.conf` stores it as a length scale (`speed_normal`, `speed_fast`), where 0.8 means pace 1.25. Every engine reaches the same pace. Edge-TTS, Kokoro and Step-Audio-EditX render the pace themselves. SAPI5 and the MS Speech Platform render it in steps of their rate scale, and ElevenLabs renders 0.7 to 1.2 on models with speed control. The daemon time-stretches whatever an engine does not render and keeps the pitch. Piper is always time-stretched, because its `length_scale` changes the duration less than requested (0.67 gave 0.78 to 0.82 of the duration in a measurement).

The daemon also normalizes the loudness of every engine and voice to -18 LUFS (ITU-R BS.1770). A quiet English clone and a loud German one then play at the same level. It measures each voice from its first audio and keeps a slow running estimate per engine and voice. Peaks stay below -1 dBFS. The estimates live in `%LOCALAPPDATA%\tts-audio\loudness.json` or `~/.cache/tts-audio/loudness.json`, so a known voice starts at the right level. Deleting the file is safe. Two keys in `voices.conf` control the normalization.

```
loudness_norm=on         # off plays every engine at its own level
loudness_target=-18      # LUFS; -16 is louder, -23 is the broadcast level
```

Cached voice previews are normalized to the same level when they are saved.

## Text clean-up and rules

Before the app reads a text aloud, it runs the text through one list of rules, from top to bottom, each rule working on the result of the one before. A rule finds a piece of text and says what the voice should read instead, for example "SQL" as "S Q L" or "z.B." as "zum Beispiel". It matches the exact text, a whole word, or a regular expression, and it can apply to all texts or only to German or English ones.

The list is split into four groups, which run in this order:

| Group | What its rules do | On in a fresh setup |
|---|---|---|
| Obsidian | Remove Obsidian syntax: `[[Page\|Name]]` links, `![[embeds]]`, tags, callouts, block IDs, `%%` comments, `==highlights==` | no |
| Markdown | Remove Markdown marks (`**`, `#`, `>`, list bullets, table pipes, code fences, HTML tags, footnote marks), read links as their text and web addresses as the site name, and read formulas between `$` signs | yes |
| Formulas | Read copied LaTeX and math symbols, for example from Readest, as German words | yes |
| Your rules | Your own rules | yes |

The rules in the first three groups are presets. They come with the app and serve as examples and starting points, and you can change, move, switch off and delete them like your own rules. "Restore presets" adds deleted presets back and resets changed ones. "Reset to original" does the same for one rule, and a group whose rules you deleted offers to add them back. Each group also has its own switch, which turns all its rules off at once and keeps the switches of the single rules. Most presets are regular expressions. Two run program code, because a regular expression cannot turn a formula into words. These are "Formulas between $ signs" and "LaTeX and math symbols". They show the label "Built in" and can be switched, moved or deleted, but not edited.

You manage the list in the settings window under Text clean-up. The groups start closed and show how many of their rules are on. A box at the end of the page tries the whole list on any text and names the rules that changed it. Changes apply to the next text you read, without a restart.

The app keeps the list in `rules.json` in the config folder. Change it in the window or with the `tts-rules` command line, which validates every change, writes the file atomically and keeps the previous version for one step of `undo`:

```bash
tts-rules list                      # all rules, under their group
tts-rules add 'SQL' 'S Q L' --kind word
tts-rules add 'z.B.' 'zum Beispiel' --kind literal --lang de
tts-rules test 'Mit **SQL** geht das z.B. schneller.' --lang de
tts-rules disable --group obsidian  # one group off
tts-rules reset md-bold             # one preset back to how it came
tts-rules restore --all             # deleted presets back, changed ones reset
tts-rules undo
```

New rules go to the end of your own rules unless `--group` names another group. `tts-rules doctor` checks that the tool runs in the current shell and shows which rules file it uses, and `tts-rules --help` lists every command. With `--json` the output is machine-readable. The exit codes are 0 (ok), 1 (invalid rule), 2 (bad arguments), 3 (rule not found) and 4 (rules file busy or unreadable). Code 5 means the shell changed non-ASCII text on the way in.

The install scripts create `tts-rules` in `~/.local/bin` on Linux and `tts-rules.cmd` in `%LOCALAPPDATA%\piper-bin` on Windows; `python bin/tts_rules.py` works from the repository. On Windows the `.cmd` wrapper runs through cmd.exe, which treats `|`, `&`, `^` and `%` in arguments as its own syntax. For patterns with these characters, call Python directly from PowerShell, as the instructions for AI assistants do.

AI coding assistants such as Claude Code or Codex can manage rules too. The button "Instructions for an AI assistant" on the Text clean-up page copies a prompt. It contains the exact command for your computer, the groups, the rule format, examples and safety steps. `tts-rules prompt` prints the same text. The prompt tells the assistant to add its rules to your own rules and to leave presets alone unless you ask. Rules an assistant adds or changes carry a badge in the window until you review them.

### Upgrading from the filters= setting

Older versions had two built-in filters, switched on with `filters=` in `voices.conf`, and kept replacements in `replace.conf`. The app, the daemon and `tts-rules` convert both into the rules list once, the first time they run, so reading sounds as before. A `filters=obsidian,latex` line turns all three preset groups on, `filters=obsidian` turns Obsidian and Markdown on, and `filters=latex` turns Formulas on. An empty line turns all three off, and a setup without the line gets Markdown and Formulas. The presets go in front of the rules already in `rules.json`, and `replace.conf` rules follow in their old order. The file is renamed to `replace.conf.migrated`. The previous `rules.json` stays as `rules.json.bak`, so `tts-rules undo` returns to it. After this step the app no longer reads `filters=`, and the line can be deleted.

The presets differ on purpose from the old filter where it read syntax aloud. Embeds, callout markers, tags, block IDs, `%%` comments, highlights, front matter, tables, bare web addresses, HTML tags, footnote marks, task boxes and nested quotes are removed or shortened. Prices such as `$5 and $10` are no longer read as a formula. `tests/golden_corpus.py` lists these cases with the old and the new result.

For changes a rule cannot express, a `tts_filters.py` in the config folder can define functions `text -> text` in a `FILTERS` dict. `tts-rules add-code <name>` adds one of them to the list as a code rule.

## Repository layout

```
bin/                    daemon, client, tray app and their modules
  piper-read-daemon.py  the daemon (engines, sentence streaming, text clean-up)
  piper-read-say        command-line client (selection, clipboard or stdin)
  tts_platform.py       OS layer: paths, IPC, audio, selection, hotkeys, autostart
  tts_config.py         voices.conf parsing
  tts_neural.py         connects the model servers to daemon and settings
  tts_voices.py         reference voices: recording, import, checks
  tts_elevenlabs.py     ElevenLabs API and audio cache
  tts_filters.py        preset rules and code rules (LaTeX, formulas between $)
  tts_rules.py          the rules list, the clean-up pipeline and the tts-rules command line
  win_*.py              Windows hotkeys, OCR, SAPI, shortcuts
  tts_icons.py          app, window and tray icons
assets/brand/           logo and icon sources (SVG), see BRAND.md
assets/icons/           icons generated from assets/brand (committed)
docs/images/            README images, built by tools/make_screenshots.py
neural/                 model servers, launcher, benchmarks
scripts/                hotkey wrappers for Linux
tools/                  icon build, icon install, screenshot and release build scripts
keyd/, kde/, systemd/   Linux integration
config/voices.conf      configuration template
VERSION                 the version; changing it publishes a release
CHANGELOG.md            what changed in each release
tests/                  pytest suite
```

## Development

The tests need neither a GPU, nor network access, nor model weights:

```bash
pip install pytest numpy ruff
python -m pytest
ruff check .
```

`python -m pytest -m "not shells"` skips the tests that run the AI assistant commands in real shells (PowerShell, Git Bash, cmd.exe, bash, zsh, fish and WSL). They take about half a minute and skip shells that are not installed.

The tests redirect every config and data folder into a temporary directory, so they never touch an installed setup. [CONTRIBUTING.md](CONTRIBUTING.md) explains how to add an engine.

The README images in `docs/images/` come from `python tools/make_screenshots.py`, which opens the settings window offscreen with a throwaway config folder and frames each grab. Run it with the interpreter of the app venv.

The icons in `assets/icons/` are generated from the SVG files in `assets/brand/`. After you change a file in `assets/brand/`, rebuild the icons with PySide6 installed and commit the result:

```bash
python tools/build_icons.py           # writes app.ico, the hicolor PNGs and the tray PNGs
python tools/build_icons.py --check   # exits with 1 if assets/icons is out of date
```

## Troubleshooting

| Symptom | Check |
|---|---|
| No sound (Linux) | `systemctl --user status piper-read`, `journalctl --user -u piper-read -n 40`, `echo Test \| ~/.local/bin/piper-read-say de --stdin` |
| No sound (Windows) | `%APPDATA%\piper\daemon.log` |
| Hotkey does nothing (Linux) | `sudo keyd monitor` shows whether keyd sees the key, and a log out and in after installing may be needed |
| Edge or ElevenLabs always falls back to Piper | network, `ffmpeg` on the PATH (Edge), API key and credits (ElevenLabs); the daemon log names the reason |
| A neural engine does not start | `server.log` in the model's data folder; `python neural/launcher.py status` shows budget and free VRAM |

## Known limitations

Piper voices speak one language each. Kokoro reads only German and English here, although the model has voices for more languages. On Wayland only the primary selection is readable globally, so Linux reads the mouse selection, not the clipboard. The Linux OCR chain (slurp, spectacle, tesseract) assumes a single monitor at scale 1.

## Licenses

Recito is licensed under the [GNU General Public License v3.0](LICENSE) (GPL-3.0-or-later). Models, voices and the bundled icon font have their own licenses, listed in [THIRD_PARTY.md](THIRD_PARTY.md).
