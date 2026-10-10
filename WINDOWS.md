# Windows

The daemon, the client, the filters and the config format are the same on Windows and Linux. Everything that differs between the systems sits in `bin/tts_platform.py` and the `bin/win_*.py` modules; one `voices.conf` is valid on both.

## Requirements

- Python 3.12 or 3.13, 64-bit, from python.org or `winget install Python.Python.3.12`. The Microsoft Store Python redirects writes to `%LOCALAPPDATA%` into its own sandbox, so the installer looks for a regular Python first.
- ffmpeg on the PATH (Edge-TTS decoding, voice import).

`install.ps1` installs ffmpeg through winget if it is missing.

## Install

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

| Path | Content |
|---|---|
| `%LOCALAPPDATA%\piper-venv` | Python venv |
| `%LOCALAPPDATA%\piper-bin` | daemon, client, tray app, modules, `neural\` |
| `%APPDATA%\piper` | `voices.conf`, Piper voices, `daemon.log`, `elevenlabs.key` (`PIPER_DIR` overrides) |
| `%LOCALAPPDATA%\tts-neural` | local neural models (`TTS_NEURAL_DIR` overrides) |

The script stops a running tray app and daemon first, because they lock files in the venv. It installs the Python packages, replaces an onnxruntime-directml left by older versions with the CPU build of onnxruntime, copies the program, adds missing keys to an existing `voices.conf` without touching the rest, downloads the default Piper voices, creates the autostart and Start menu shortcuts (`TTS-Vorlesen.lnk`) and starts the tray app. It is safe to run again.

Kokoro runs as a model server with its own venv. Install it separately with `neural\kokoro2\setup.ps1`, see [neural/kokoro2/README.md](neural/kokoro2/README.md).

## How the Windows layer works

Daemon lifecycle. There is no systemd, so the tray app starts the daemon with `pythonw` (no console window) and writes its output to `%APPDATA%\piper\daemon.log`. A console program started from a `pythonw` process opens a console window of its own, so tray, settings window and daemon start every child process (ffmpeg, nvidia-smi, powershell) through `run_hidden` or `popen_hidden` in `tts_platform.py`, which set `CREATE_NO_WINDOW`. To restart, the tray app sends `{"cmd": "quit"}` over IPC, waits for the port to close and starts a new daemon.

IPC. The daemon listens on TCP `127.0.0.1:8765`. Hyper-V and WSL reserve port ranges dynamically; if 8765 is reserved, the daemon takes a free port and writes it with its process id to `%APPDATA%\piper\daemon.port`, where clients read it. `PIPER_PORT` fixes the port.

Selection. Windows has no primary selection. The hotkey handler copies the current selection with a synthetic Ctrl+C, reads the clipboard and restores the previous clipboard content. With nothing selected, it reads the clipboard.

Hotkeys. The tray app registers global hotkeys with the Win32 API `RegisterHotKey` in a dedicated thread (`win_hotkeys.py`). Windows treats AltGr as Ctrl+Alt, so a `Ctrl+Alt+P` hotkey also fires on `AltGr+P`. The hotkeys are stored as `hotkey_*` keys in `voices.conf`; the settings window records them.

OCR. `win_ocr.py` shows a translucent overlay across all monitors, crops the selected region in device pixels (correct on high-DPI screens) and recognizes it with the built-in Windows OCR (`Windows.Media.Ocr` through `winsdk`). If that fails, it falls back to tesseract when installed.

Audio. `_SoundDeviceSink` in `tts_platform.py` plays PCM through sounddevice (PortAudio) with a callback and its own ring buffer. Nothing blocks inside PortAudio, so stopping from another thread is safe; pause outputs silence and keeps the buffer, so playback resumes where it stopped.

Autostart. `win_autostart.py` creates or removes a shortcut in the Startup folder; the tray menu toggles it.

## GPU for Piper

`device=gpu` in `voices.conf` (or the tray menu) runs Piper on CUDA when onnxruntime has the CUDA provider, which takes `onnxruntime-gpu` in place of the CPU build. The daemon checks the GPU with a short synthesis and uses the CPU if it fails. CPU is the default. For GPU speech use the PyTorch model servers in `neural/`.

## Troubleshooting

| Symptom | Check |
|---|---|
| Nothing happens on a hotkey | `%APPDATA%\piper\daemon.log`; is the tray icon there? Another program may own the hotkey |
| Kokoro falls back to Piper | is the model server installed (`python neural\launcher.py status`)? Its log is `%LOCALAPPDATA%\tts-neural\kokoro2\server.log` |
| Edge always falls back to Piper | `ffmpeg -version` in a new terminal; network |
| pip fails with WinError 5 | a running tray app or daemon locks the venv; quit it from the tray menu |
| No wheels for onnxruntime | use Python 3.12 or 3.13 |
