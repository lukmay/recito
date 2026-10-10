#Requires -Version 5.1
<#
  Sets up Recito, the local read-aloud tool, on Windows (counterpart of install.sh
  on Fedora). Idempotent, safe to run again.

    powershell -ExecutionPolicy Bypass -File .\install.ps1

  Creates:
    %LOCALAPPDATA%\piper-venv   venv (Python 3.12/3.13)
    %LOCALAPPDATA%\piper-bin    daemon, client, tray, modules
    %APPDATA%\piper\voices.conf configuration (same format as on Linux)
  and starts the tray, which starts the daemon itself.
#>
# Not 'Stop' on purpose: pip, winget and piper.download_voices write INFO and
# progress to stderr, and under 'Stop' any such line would abort the installer.
# Critical steps are checked explicitly below (throw or warning).
$ErrorActionPreference = 'Continue'

$Repo      = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv      = Join-Path $env:LOCALAPPDATA 'piper-venv'
$BinDir    = Join-Path $env:LOCALAPPDATA 'piper-bin'
$PiperDir  = Join-Path $env:APPDATA      'piper'
$Py        = Join-Path $Venv 'Scripts\python.exe'
$Pyw       = Join-Path $Venv 'Scripts\pythonw.exe'

function Step($n, $msg) { Write-Host "==> $n  $msg" -ForegroundColor Cyan }

# --- 1/7  Python venv ------------------------------------------------------- #
Step '1/7' 'Python venv (3.12/3.13 preferred)'
# The Microsoft Store Python under ...\WindowsApps\ redirects writes into a
# package sandbox, so the venv ends up elsewhere and paths break. Look for a
# regular Python instead (py launcher, then Programs\Python\*, then PATH without
# WindowsApps).
function Find-BasePython {
    $exe = (Get-Command py -ErrorAction SilentlyContinue).Source
    if ($exe) { return [pscustomobject]@{ Exe = $exe; Pre = @('-3.12') } }
    foreach ($p in Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python31*\python.exe",
                                  "$env:ProgramFiles\Python31*\python.exe" -ErrorAction SilentlyContinue |
                   Sort-Object FullName -Descending) {
        return [pscustomobject]@{ Exe = $p.FullName; Pre = @() }
    }
    foreach ($c in Get-Command python.exe -All -ErrorAction SilentlyContinue) {
        if ($c.Source -notlike '*\WindowsApps\*') {
            return [pscustomobject]@{ Exe = $c.Source; Pre = @() }
        }
    }
    throw "No suitable Python found. Please install Python 3.12/3.13 (winget install Python.Python.3.12)."
}
if (-not (Test-Path $Py)) {
    $base = Find-BasePython
    $pre = @($base.Pre)
    Write-Host "    using: $($base.Exe) $($pre -join ' ')"
    & $base.Exe @pre -m venv $Venv
}
if (-not (Test-Path $Py)) { throw "venv creation failed ($Venv)." }

# A running tray or daemon locks DLLs in the venv (pip upgrades fail with
# WinError 5) and keeps the old code in memory, so stop it first. Step 7
# restarts the tray, which starts the daemon.
$running = Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.ExecutablePath -like "$Venv\*" -and
                   $_.CommandLine -match 'tts-tray\.py|piper-read-daemon\.py' }
foreach ($p in $running) {
    Write-Host "    stopping running instance (PID $($p.ProcessId))"
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
if ($running) { Start-Sleep -Seconds 1 }

# --- 2/7  pip packages ------------------------------------------------------ #
Step '2/7' 'pip packages (piper, edge-tts, PySide6, sounddevice, ...)'
& $Py -m pip install --quiet --upgrade pip
& $Py -m pip install --quiet --upgrade `
    piper-tts numpy edge-tts PySide6 sounddevice soundcard pyperclip `
    lingua-language-detector onnx winsdk pywin32
if ($LASTEXITCODE -ne 0) {
    throw "pip installation failed (exit $LASTEXITCODE). Check the network and run install.ps1 again."
}
# ONNX Runtime: Piper uses the CPU build that piper-tts pulls in. Older
# installs replaced it with onnxruntime-directml. Both packages share the
# 'onnxruntime' folder, so uninstalling one deletes the other's files; hence
# a forced reinstall of the CPU build followed by an import check.
$dmlOrt = & $Py -m pip show onnxruntime-directml 2>$null
if ($dmlOrt) {
    & $Py -m pip uninstall -y onnxruntime-directml 2>$null | Out-Null
    & $Py -m pip install --quiet --force-reinstall --no-deps onnxruntime
}
& $Py -c "from onnxruntime import InferenceSession" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $Py -m pip install --quiet --force-reinstall --no-deps onnxruntime
}

# --- 3/7  External tools (ffmpeg) ------------------------------------------- #
# OCR does not need tesseract: it uses the built-in Windows OCR
# (Windows.Media.Ocr via winsdk). tesseract is an optional fallback and is not
# installed here.
Step '3/7' 'Checking external tools (ffmpeg)'
function Ensure-Tool($exe, $wingetId, $optional) {
    if (Get-Command $exe -ErrorAction SilentlyContinue) {
        Write-Host "    ok: $exe"
        return
    }
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "    installing $wingetId ..."
        winget install --silent --accept-package-agreements `
            --accept-source-agreements -e --id $wingetId 2>$null
    }
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) {
        $lvl = if ($optional) { 'Warning' } else { 'Error' }
        Write-Host "    ${lvl}: '$exe' is missing, please install it manually ($wingetId)." `
            -ForegroundColor Yellow
    }
}
Ensure-Tool 'ffmpeg'    'Gyan.FFmpeg'          $false   # Edge MP3 -> PCM

# --- 4/7  Files to piper-bin ------------------------------------------------ #
Step '4/7' "Files to $BinDir"
New-Item -ItemType Directory -Force $BinDir | Out-Null
foreach ($f in 'piper-read-daemon.py', 'piper-read-say', 'tts-tray.py',
                'tts_filters.py', 'tts_rules.py', 'tts_platform.py', 'tts_theme.py', 'tts_config.py',
                'tts_elevenlabs.py', 'tts_settings.py', 'tts_settings.qml', 'tts_reader.py',
                'win_autostart.py', 'tts_neural.py', 'tts_voices.py', 'tts_i18n.py', 'tts_langs.py',
                'win_hotkeys.py', 'win_ocr.py', 'win_sapi.py', 'tts_previews.py', 'tts_icons.py', 'tts_audio.py') {
    Copy-Item (Join-Path $Repo "bin\$f") (Join-Path $BinDir $f) -Force
}
# Version shown in the settings window and the tray tooltip
if (Test-Path (Join-Path $Repo 'VERSION')) {
    Copy-Item (Join-Path $Repo 'VERSION') (Join-Path $BinDir 'recito-version.txt') -Force
}
# tts-rules: command line for the text rules (for power users and AI assistants).
# tts_rules.py writes it, the same code the tests check.
& $Py (Join-Path $BinDir 'tts_rules.py') write-wrapper (Join-Path $BinDir 'tts-rules.cmd') | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "    Warning: could not write tts-rules.cmd (exit $LASTEXITCODE)." -ForegroundColor Yellow
}
# UI translations and shared QML components
foreach ($d in 'i18n', 'ui') {
    $Dst = Join-Path $BinDir $d
    New-Item -ItemType Directory -Force $Dst | Out-Null
    Copy-Item (Join-Path $Repo "bin\$d\*") $Dst -Force
}
# Local neural TTS servers. Only the server scripts are copied; each model venv
# is created by neural\<model>\setup.ps1, which is optional and not part of this run.
$NeuralDst = Join-Path $BinDir 'neural'
New-Item -ItemType Directory -Force $NeuralDst | Out-Null
robocopy (Join-Path $Repo 'neural') $NeuralDst /E /XD __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy neural failed ($LASTEXITCODE)" }
$global:LASTEXITCODE = 0
# Icon font for the settings window (Material Icons Round, Apache 2.0)
New-Item -ItemType Directory -Force (Join-Path $BinDir 'fonts') | Out-Null
Copy-Item (Join-Path $Repo 'bin\fonts\*') (Join-Path $BinDir 'fonts') -Force
# App and tray icons (generated by tools\build_icons.py): app.ico for the
# shortcuts, tray rasters and the brand SVGs for the settings window
$IconsDst = Join-Path $BinDir 'icons'
robocopy (Join-Path $Repo 'assets\icons') $IconsDst /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy icons failed ($LASTEXITCODE)" }
$global:LASTEXITCODE = 0

# --- 5/7  voices.conf (idempotent, adds Windows keys) ----------------------- #
Step '5/7' "voices.conf to $PiperDir"
New-Item -ItemType Directory -Force $PiperDir | Out-Null
$conf = Join-Path $PiperDir 'voices.conf'
if (-not (Test-Path $conf)) {
    Copy-Item (Join-Path $Repo 'config\voices.conf') $conf
}
# Append missing keys; existing and commented ones stay untouched.
$existing = (Get-Content $conf) | ForEach-Object {
    if ($_ -match '^\s*([^#=]+)=') { $matches[1].Trim() }
}
$defaults = [ordered]@{
    'engine'          = 'edge'
    'device'          = 'cpu'
    'lang_detect'     = 'text'
    'pause_sentence'  = '0'
    'speed_normal'    = '1.0'
    'speed_fast'      = '0.8'
    'edge_de'         = 'de-DE-KatjaNeural'
    'edge_en'         = 'en-US-AriaNeural'
    'elevenlabs_model'= 'eleven_flash_v2_5'
    'elevenlabs_cache'= 'on'
    'hotkey_read'     = 'ctrl+alt+p'
    'hotkey_read_fast'= 'ctrl+alt+shift+p'
    'hotkey_ocr'      = 'ctrl+alt+o'
    'hotkey_ocr_fast' = 'ctrl+alt+shift+o'
    'hotkey_pause'    = 'ctrl+alt+l'
    'hotkey_stop'     = 'ctrl+alt+s'
    'hotkey_reader'   = 'ctrl+alt+e'
    'autostart'       = 'on'
}
$append = @()
foreach ($k in $defaults.Keys) {
    if ($existing -notcontains $k) { $append += "$k=$($defaults[$k])" }
}
if ($append.Count) {
    Add-Content $conf "`n# --- Windows: hotkeys, autostart, added defaults ---" -Encoding utf8
    Add-Content $conf $append -Encoding utf8
    Write-Host "    + added: $($append -join ', ')"
}

# --- 6/7  Download models --------------------------------------------------- #
Step '6/7' 'Downloading Piper voices'
# Piper voices, taken from voices.conf (<lang>=<onnx base name>).
$piperVoices = (Get-Content $conf) | ForEach-Object {
    if ($_ -match '^\s*([a-z]{2})\s*=\s*([a-z]{2}_[A-Z]{2}-[\w\-\.]+)\s*$') { $matches[2] }
} | Select-Object -Unique
foreach ($base in $piperVoices) {
    if (-not (Test-Path (Join-Path $PiperDir "$base.onnx"))) {
        Write-Host "    downloading Piper voice $base ..."
        & $Py -m piper.download_voices $base --download-dir $PiperDir 2>$null
        if (-not (Test-Path (Join-Path $PiperDir "$base.onnx"))) {
            Write-Host "    (download failed, place it manually: $PiperDir\$base.onnx)" `
                -ForegroundColor Yellow
        }
    }
}
# Kokoro is a model server with its own venv: neural\kokoro2\setup.ps1
# (optional, not part of this run).

# Voice previews, so "Listen" in the setup assistant plays at once: Piper and
# Edge, rendered in the background into %LOCALAPPDATA%\tts-previews (log:
# build.log there). Offline nothing is rendered; the missing previews are
# saved the first time a voice is played. PATH is reloaded so a freshly
# installed ffmpeg (Edge) is found.
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
            [Environment]::GetEnvironmentVariable('Path', 'User')
$prevExe = if (Test-Path $Pyw) { $Pyw } else { $Py }
Start-Process -FilePath $prevExe -WorkingDirectory $BinDir -WindowStyle Hidden `
    -ArgumentList "`"$(Join-Path $BinDir 'tts_previews.py')`" build --engine piper --engine edge"

# --- 7/7  Autostart, Start menu, start tray --------------------------------- #
Step '7/7' 'Autostart and Start menu shortcut, starting tray'
$trayPy = Join-Path $BinDir 'tts-tray.py'
$pywExe = if (Test-Path $Pyw) { $Pyw } else { $Py }
# Autostart and Start menu shortcut ('Recito', searchable) through the
# app logic (single source of truth): app icon and the tray's AppUserModelID.
# Both calls first delete the shortcuts of the old name (TTS-Vorlesen.lnk in the
# Startup folder and the Start menu), so the tray does not start twice.
& $Py -c "import sys; sys.path.insert(0, r'$BinDir'); import win_autostart; win_autostart.set_enabled(True); win_autostart.start_menu_shortcut()" 2>$null
# Start the tray unless it is already running; it starts the daemon itself.
# Match on the script name: the preview build from step 6 runs under the same pythonw.
if (-not (Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
          Where-Object { $_.CommandLine -like '*tts-tray.py*' })) {
    Start-Process -FilePath $pywExe -ArgumentList "`"$trayPy`"" -WorkingDirectory $BinDir
}

Write-Host ''
Write-Host '============================================================' -ForegroundColor Green
Write-Host ' Done. The speaker icon is in the system tray.'
Write-Host ' Default hotkeys (configurable in the tray):'
Write-Host '   Ctrl+Alt+P / Shift+P   Read aloud / fast (clipboard)'
Write-Host '   Ctrl+Alt+O / Shift+O   Read OCR region / fast'
Write-Host '   Ctrl+Alt+L             Pause / resume'
Write-Host '   Ctrl+Alt+S             Stop'
Write-Host ' Engine, voices, speed: tray settings.'
Write-Host ' Manual start: Start menu -> "Recito".'
Write-Host '============================================================' -ForegroundColor Green
