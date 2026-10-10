# Contributing

## Running the tests

```bash
pip install pytest numpy ruff
python -m pytest
ruff check .
```

The suite runs in about two seconds on Windows and Linux and needs no GPU, network access or model weights. Model servers are replaced by a fake backend on an ephemeral port, and `nvidia-smi` by a stub. A fixture in `tests/conftest.py` points `PIPER_DIR`, `TTS_NEURAL_DIR`, `APPDATA`, `LOCALAPPDATA` and `XDG_*` into a temporary folder for every test, so the tests never read or write an installed setup. GitHub Actions runs the same commands on Ubuntu and Windows (`.github/workflows/tests.yml`).

Tests marked `shells` run the command lines of the AI assistant prompt in every shell found on the machine, including WSL on Windows. `python -m pytest -m "not shells"` leaves them out for a quick run.

Do not add tests that start a real model server or load weights onto the GPU.

## Cutting a release

A release is published by GitHub Actions when `VERSION` changes on `master`.

1. Change `VERSION` to the new number, for example `1.1.0` (a plain `MAJOR.MINOR.PATCH`; a suffix such as `-rc1` makes it a pre-release).
2. Add a `## 1.1.0` section to `CHANGELOG.md` that tells users what changed. It is put at the top of the release notes, below the install steps; GitHub adds the list of merged changes after it.
3. Commit and push to `master`.

`.github/workflows/release.yml` runs the tests and ruff, creates the tag `v1.1.0` on that commit if it does not exist, and attaches `Recito-Setup-1.1.0.exe`, `Recito-1.1.0-x86_64.AppImage`, `Recito-1.1.0-linux.tar.gz` and `SHA256SUMS.txt`. The Linux archive is built from the commit with `git archive`, so only committed files are in it, and tests, CI files, icon sources and screenshots are left out. The setup is built and tried out on a Windows runner first (see [Windows setup](#windows-setup)); if that job fails, no release is published. There is no Windows zip any more, because the setup replaces it and a second Windows download only raises the question of which one to take.

To build the Linux archive locally, run `python tools/build_release.py 1.1.0 dist`. To rebuild the assets of a release that exists, run the workflow by hand from the Actions tab; it replaces the attached files of the tag of `VERSION`. A push that changes `VERSION` but finds the release already published does nothing. The AppImage is built from the Linux archive by `tools/build_appimage.sh 1.1.0 dist` on Ubuntu 22.04 or newer (it needs meson, ninja, libwayland-dev and the other tools named in the script header; `SKIP_WL_CLIPBOARD=1` leaves the bundled wl-clipboard out). It holds `appimage/AppRun`, a pinned uv and the Linux archive; AppRun runs the same `install.sh` as the one-liner, so test installer changes with `install.sh` and the AppImage only for the packaging. The pinned versions and hashes of uv, appimagetool and wl-clipboard are at the top of the script; update them together. To rebuild the assets of a release that exists, run the workflow by hand from the Actions tab; it replaces the attached files of the tag of `VERSION`. A push that changes `VERSION` but finds the release already published does nothing.

## Windows setup

`Recito-Setup-<version>.exe` is an [Inno Setup](https://jrsoftware.org/isinfo.php) 6 installer, defined in `installer/recito.iss`. It installs per user (no administrator rights), copies `bin/`, `neural/` and the icons, and then runs `install.ps1` hidden. With the bundled `uv.exe` the script creates the venv with a uv-managed Python 3.12, installs the packages, copies a static ffmpeg (from the `imageio-ffmpeg` package) next to the program, downloads the default Piper voices and makes the autostart and Start menu shortcuts through `bin/win_autostart.py`. The setup shows the step the script reports and starts the tray at the end. Without a network the setup fails with a message and a non-zero exit code. Only the voice download may fail; the settings window offers the voice again at the first start.

The setup keeps the historical folders, so it upgrades an install made by `install.ps1` in place and keeps its settings: `%LOCALAPPDATA%\piper-bin` (program), `%LOCALAPPDATA%\piper-venv` (venv), `%LOCALAPPDATA%\piper-python` (the Python that uv manages) and `%APPDATA%\piper` (config and voices). With `/DIR=<folder>` the venv and the Python go into that folder instead. The uninstaller stops Recito, removes the program, venv, Python and shortcuts, and asks whether to keep the config and voices. A silent uninstall keeps them unless it gets `/PURGE=1`.

To build it locally:

```powershell
winget install JRSoftware.InnoSetup
python tools/fetch_uv.py                  # uv.exe into installer/, version in installer/UV_VERSION
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" /DAppVersion=1.0.0 installer\recito.iss
```

The result is `dist\Recito-Setup-1.0.0.exe`. Test it only through `tools/test_installer.ps1`. It installs, upgrades and uninstalls in a throwaway folder and points `LOCALAPPDATA`, `APPDATA`, `PIPER_DIR` and `TTS_NEURAL_DIR` there, so a running Recito on the same machine stays untouched:

```powershell
powershell -ExecutionPolicy Bypass -File tools\test_installer.ps1 -Setup dist\Recito-Setup-1.0.0.exe
```

The test needs the internet and takes about five minutes. By hand, the switches are `/VERYSILENT /SUPPRESSMSGBOXES /DIR=<folder>` and `/TESTMODE=1`, which makes no shortcut, starts no tray and renders no previews. The wizard images come from `tools/build_installer_art.py`. The release job pins Inno Setup and checks its hash, and `tools/fetch_uv.py` checks the hash of the uv release. To update either, change the version and the hash in `release.yml` or the version in `installer/UV_VERSION`.

The setup is not code-signed, so SmartScreen warns on the first run. A certificate would remove that.

## Code style

The app was called TTS-Vorlesen before it became Recito. The internal names `%APPDATA%\piper`, `piper-bin`, `piper-venv`, `tts-neural`, the keys in `voices.conf` and the module file names are historical names and stay as they are, so that installed setups keep working.

Code, comments, log messages and identifiers are in English. Comments describe what the code does now, in short phrases; leave out the history of how it got there. Texts shown in the settings window, the tray menu or notifications are user-facing and will move to translation files, so keep them separate from log messages.

`ruff check .` must pass; the configuration is in `pyproject.toml`. `.editorconfig` sets UTF-8, LF line endings and 4-space indentation.

## Adding a neural model

A local neural model is a separate process with its own venv, so its dependencies never touch the daemon. Adding one takes a folder and a registry entry; the daemon, `bin/tts_neural.py` and the launcher need no changes.

1. Create `neural/<key>/server.py`. Subclass `Backend` from `neural/server_base.py`, implement `load()`, `voices()`, `default_voices()` and `synth()`, and end with `run(MyBackend(), "<key>")`. `synth()` is a generator of float arrays in -1..1 or of s16le bytes; yield small pieces early to get an early first sound.

   ```python
   import sys
   from pathlib import Path

   sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
   from server_base import Backend, log_fn, run

   KEY = "mymodel"
   log = log_fn(KEY)


   class MyModel(Backend):
       name = KEY
       sample_rate = 24000
       tags = []           # inline tags the model understands, e.g. ["[laugh]"]
       instruct = False    # accepts a style instruction in the "instruct" field

       def load(self):
           ...             # load weights; runs once before the server listens

       def voices(self):
           return [{"id": "anna", "lang": "de", "desc": "female, calm"}]

       def default_voices(self):
           return {"de": "anna", "en": "anna"}

       def synth(self, text, voice, lang, speed=1.0, instruct=""):
           for sentence in split_sentences(text):
               yield self.model.generate(sentence, voice)


   if __name__ == "__main__":
       run(MyModel(), KEY)
   ```

   `server_base` handles the HTTP protocol, the port file, the VRAM cap, CPU priority, idle exit and `/shutdown`. Reference voices for cloning are in `server_base.voices_dir()` as `<id>.wav` with an optional `<id>.txt` transcript; offer them as `ref:<id>`. For CUDA graphs and static KV caches, `neural/cuda_graph.py` has `capture()` and `StaticKV`.

2. Add `neural/<key>/setup.ps1` (and `setup.sh` if the model runs on Linux). The script creates `<data folder>/<key>/venv`, installs pinned dependencies, downloads pinned weights and runs an import self-test without loading the model onto the GPU. The data folder is `TTS_NEURAL_DIR` if set, else `%LOCALAPPDATA%\tts-neural` or `~/.local/share/tts-neural`. `neural/kokoro2/setup.ps1` and `setup.sh` are compact examples.

3. Add an entry to `neural/models.json`:

   ```json
   "mymodel": {"title": "My Model", "dir": "mymodel", "port": 37780, "vram_mb": 3000,
               "clone": false, "instruct": false, "tags": false}
   ```

   `port` must be unique. `vram_mb` is the measured need of the whole server process: start the server with `python neural/launcher.py start mymodel`, run `python neural/bench.py mymodel` and read the used memory from `nvidia-smi`. The launcher uses this number for the budget check and as the hard cap for the PyTorch allocator, so a value that is too low makes synthesis fail with out-of-memory errors. Set `cpu_ok` only if the model reads aloud in real time without an NVIDIA GPU. Set `native_speed` only if `synth()` renders the `speed` argument itself over 0.5 to 2.0 with the duration in proportion; without it the daemon sends speed 1.0 and time-stretches the output. `env` passes environment variables to the server process. `tests/test_models_json.py` checks the entry.

4. Optionally add `neural/<key>/samples.json` with model-specific examples for `bench.py`, and a `README.md` with measurements, voices and the `instruct` syntax.

Users then select the model with `engine=<key>` in `voices.conf` and set voices with `<key>_de` and `<key>_en`. `bin/tts_neural.capabilities(key)` reports the flags from `models.json` to the settings window. The settings window currently also keeps a label and description per neural engine in `NEURAL_INFO` in `bin/tts_settings.py`; a new model only appears there once it has an entry.

## Adding an engine inside the daemon

Engines that run inside the daemon (like Edge-TTS, SAPI or ElevenLabs) are entries in the `ENGINES` table in `bin/piper-read-daemon.py`. An `EngineSpec` has three functions:

- `ready(key)`: whether the engine can speak voice key `key` right now (set during startup, for example after checking an API key).
- `speak(key, sentence, length_scale, ctx)`: a generator of `_Chunk` objects for one sentence. Raise before the first chunk on failure, so Piper can read the sentence instead.
- `preview(sentence, test, length_scale)`: the same for a preview request from the settings window; `test` holds `voice`, `key`, `model` and `instruct`.

Set `native` to a function `(pace, test) -> pace` that returns the pace the engine renders itself for a requested pace (the default renders none). The daemon passes the engine a `length_scale` of 1/that pace and time-stretches the rest, so every engine reaches the same pace. An engine with a limited range clamps, for example `_clamp_pace(0.5, 2.0)`.

Set `online=True` if one failure should send the rest of the text to Piper (to avoid a network timeout per sentence), and `chunked=True` if the engine renders whole units before playback and benefits from clause-sized units and a prebuffer. If the engine reads voices as `<engine>_<key>=<voice>` from `voices.conf`, add its prefix to `VOICE_MAP_PREFIXES` in `bin/tts_config.py`.

## Text rules and presets

Everything the daemon does to a text before reading it is one ordered list of rules in `rules.json`. `bin/tts_rules.py` owns that file: validation, the locked and atomic writes with the `.bak` copy for undo, the group switches, restore and reset of presets, the one-time setup from `replace.conf` and `filters=` (`migrate()`), the pipeline the daemon reads through (`Pipeline`, which reloads on change), the command line and the instructions for AI assistants. The daemon, the settings window and `tts-rules test` all run the same list.

`bin/tts_filters.py` holds what needs code: the code rules (`BUILTINS`, functions `text -> text` such as `latex` and `dollar_math`) and the shipped presets (`PRESETS`). A preset is a rule with a stable `preset_id`, a `group` (`obsidian`, `markdown` or `formulas`) and the `preset_version` it came from. Prefer a regular expression over a code rule, because users can read, change and copy it.

To add a preset:

1. Add an entry to `PRESETS` at the place in its group where it should run, with a short English note.
2. Give it a title in `bin/i18n/en.json` and `de.json` under `preset.<preset_id>`; a code rule also needs `preset.<preset_id>.desc`.
3. Add a case to `tests/golden_corpus.py` that shows what it changes.

A new preset does not appear in existing lists by itself; `restore` adds it. Changing a shipped preset changes what `reset` and `restore` bring back, and lists that still hold the old version then show that preset as changed. Raise `PRESET_VERSION` when you change one. Change the file format only together with `VERSION` in `tts_rules.py`. Keep the command line output and exit codes stable, because AI assistants rely on them.

`tests/test_presets.py` compares the presets with the former `obsidian` filter, frozen in `tests/legacy_filters.py`, on the notes in `tests/golden_corpus.py`, and checks the setup from every kind of `filters=` line. `tests/test_tts_rules.py` and `tests/test_daemon_rules.py` cover the store, the command line and the daemon.
