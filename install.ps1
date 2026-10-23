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

  The Recito-Setup installer (installer\recito.iss) runs this script hidden
  with these switches:

    -Uv <path>       uv.exe: creates the venv with a uv-managed Python 3.12 and
                     installs the packages with uv, so no Python is needed
    -Quiet           no console output
    -StatusFile <f>  append "STEP n/7|text" lines and a final DONE|0 or FAIL|text
    -SkipCopy        the files are already in -BinDir (the installer copied them)
    -Template <f>    voices.conf template when there is no config\voices.conf
    -StopOnly        stop a running tray and daemon, then exit
    -NoShortcuts     no autostart and no Start menu shortcut
    -NoStart         do not start the tray at the end
    -NoPreviews      do not render the voice previews in the background
    -BinDir -VenvDir -PiperDir   other target folders (tests)
#>
param(
    [string]$Uv,
    [switch]$Quiet,
    [string]$StatusFile,
    [switch]$SkipCopy,
    [string]$Template,
    [switch]$StopOnly,
    [switch]$NoShortcuts,
    [switch]$NoStart,
    [switch]$NoPreviews,
    [string]$BinDir,
    [string]$VenvDir,
    [string]$PiperDir
)
# Not 'Stop' on purpose: pip, uv and piper.download_voices write INFO and
# progress to stderr, and under 'Stop' any such line would abort the installer.
# Critical steps are checked explicitly below (throw or warning).
$ErrorActionPreference = 'Continue'

$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $VenvDir) { $VenvDir = Join-Path $env:LOCALAPPDATA 'piper-venv' }
if (-not $BinDir)  { $BinDir  = Join-Path $env:LOCALAPPDATA 'piper-bin' }
if (-not $PiperDir) {
    $PiperDir = if ($env:PIPER_DIR) { $env:PIPER_DIR } else { Join-Path $env:APPDATA 'piper' }
}
$Venv   = $VenvDir
$Py     = Join-Path $Venv 'Scripts\python.exe'
$Pyw    = Join-Path $Venv 'Scripts\pythonw.exe'
# uv keeps its managed Pythons next to the venv, so the uninstaller can remove
# the whole runtime.
$PyHome  = Join-Path (Split-Path -Parent $Venv) 'piper-python'
$UvCache = Join-Path $env:TEMP 'recito-uv-cache'
$LogFile = Join-Path $env:TEMP 'recito-install.log'

function Write-Status($line) {
    if ($StatusFile) {
        [IO.File]::AppendAllText($StatusFile, "$line`r`n", (New-Object Text.UTF8Encoding($false)))
    }
}
function Say($msg, $color) {
    if ($Quiet) { return }
    if ($color) { Write-Host $msg -ForegroundColor $color } else { Write-Host $msg }
}
function Step($n, $msg) {
    Say "==> $n  $msg" 'Cyan'
    Write-Status "STEP $n|$msg"
}

# A running tray or daemon locks DLLs in the venv (upgrades fail with WinError 5)
# and keeps the old code in memory, so stop it first. The last step restarts it.
function Stop-Recito {
    $running = Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
        Where-Object { $_.CommandLine -match 'tts-tray\.py|piper-read-daemon\.py' -and
                       ($_.ExecutablePath -like "$Venv\*" -or $_.ExecutablePath -like "$PyHome\*" -or
                        $_.CommandLine -like "*$BinDir*") }
    foreach ($p in $running) {
        Say "    stopping running instance (PID $($p.ProcessId))"
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($running) { Start-Sleep -Seconds 1 }
}
if ($StopOnly) { Stop-Recito; exit 0 }

# Runs a program hidden, appends its output to the log, returns the exit code.
function Invoke-Logged($exe, [string[]]$arguments, [switch]$NoEcho) {
    $argLine = ($arguments | ForEach-Object {
        if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
    }) -join ' '
    $out = [IO.Path]::GetTempFileName(); $err = [IO.Path]::GetTempFileName()
    $p = Start-Process -FilePath $exe -ArgumentList $argLine -Wait -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $out -RedirectStandardError $err
    Add-Content $LogFile "> $exe $argLine" -Encoding utf8
    Add-Content $LogFile (Get-Content $out, $err) -Encoding utf8
    if ($p.ExitCode -ne 0 -and -not $NoEcho) { Get-Content $err -Tail 15 | ForEach-Object { Say "    $_" } }
    Remove-Item $out, $err -Force -ErrorAction SilentlyContinue
    return $p.ExitCode
}

# The Microsoft Store Python under ...\WindowsApps\ redirects writes into a
# package sandbox, so the venv ends up elsewhere and paths break. Without -Uv,
# look for a regular Python instead (py launcher, then Programs\Python\*, then
# PATH without WindowsApps).
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

# Package manager calls, through uv when -Uv is given, else through pip.
function Pip-Install([string[]]$pkgs, [string[]]$extra = @()) {
    if ($Uv) {
        return Invoke-Logged $Uv (@('pip', 'install', '--python', $Py) + $extra + $pkgs)
    }
    $extra = $extra | ForEach-Object { if ($_ -eq '--reinstall') { '--force-reinstall' } else { $_ } }
    return Invoke-Logged $Py (@('-m', 'pip', 'install', '--quiet') + $extra + $pkgs)
}
function Pip-Has([string]$pkg) {
    if ($Uv) { return (Invoke-Logged $Uv @('pip', 'show', '--python', $Py, $pkg) -NoEcho) -eq 0 }
    return (Invoke-Logged $Py @('-m', 'pip', 'show', $pkg) -NoEcho) -eq 0
}
function Pip-Uninstall([string]$pkg) {
    if ($Uv) { Invoke-Logged $Uv @('pip', 'uninstall', '--python', $Py, $pkg) | Out-Null }
    else { Invoke-Logged $Py @('-m', 'pip', 'uninstall', '-y', $pkg) | Out-Null }
}

function Install-Recito {
    # --- 1/7  Python venv --------------------------------------------------- #
    Step '1/7' 'Python environment'
    Stop-Recito
    if (-not (Test-Path $Py)) {
        if ($Uv) {
            # A uv-managed Python 3.12, downloaded once; no system Python needed.
            $env:UV_PYTHON_INSTALL_DIR = $PyHome
            $env:UV_CACHE_DIR = $UvCache
            Say "    uv venv (managed Python 3.12) in $Venv"
            $rc = Invoke-Logged $Uv @('venv', '--python', '3.12', '--managed-python', $Venv)
            if ($rc -ne 0) { throw "Python environment failed (uv exit $rc). Check the network." }
        } else {
            $base = Find-BasePython
            $pre = @($base.Pre)
            Say "    using: $($base.Exe) $($pre -join ' ')"
            & $base.Exe @pre -m venv $Venv
        }
    }
    if (-not (Test-Path $Py)) { throw "venv creation failed ($Venv)." }
    if ($Uv) { $env:UV_CACHE_DIR = $UvCache }

    # --- 2/7  Python packages ----------------------------------------------- #
    Step '2/7' 'Python packages (piper, edge-tts, PySide6, ...), a few minutes'
    if (-not $Uv) { Invoke-Logged $Py @('-m', 'pip', 'install', '--quiet', '--upgrade', 'pip') | Out-Null }
    $rc = Pip-Install @('piper-tts', 'numpy', 'edge-tts', 'PySide6', 'sounddevice', 'soundcard',
                        'pyperclip', 'lingua-language-detector', 'onnx', 'winsdk', 'pywin32',
                        'imageio-ffmpeg') @('--upgrade')
    if ($rc -ne 0) {
        throw "Package installation failed (exit $rc). Check the network, then run the installer again."
    }
    # ONNX Runtime: Piper uses the CPU build that piper-tts pulls in. Older
    # installs replaced it with onnxruntime-directml. Both packages share the
    # 'onnxruntime' folder, so uninstalling one deletes the other's files; hence
    # a forced reinstall of the CPU build followed by an import check.
    if (Pip-Has 'onnxruntime-directml') {
        Pip-Uninstall 'onnxruntime-directml'
        Pip-Install @('onnxruntime') @('--reinstall', '--no-deps') | Out-Null
    }
    if ((Invoke-Logged $Py @('-c', 'from onnxruntime import InferenceSession')) -ne 0) {
        Pip-Install @('onnxruntime') @('--reinstall', '--no-deps') | Out-Null
    }

    # --- 3/7  Files to piper-bin -------------------------------------------- #
    Step '3/7' "Program files"
    New-Item -ItemType Directory -Force $BinDir | Out-Null
    if (-not $SkipCopy) {
        foreach ($f in 'piper-read-daemon.py', 'piper-read-say', 'tts-tray.py',
                        'tts_filters.py', 'tts_rules.py', 'tts_platform.py', 'tts_theme.py', 'tts_config.py',
                        'tts_elevenlabs.py', 'tts_settings.py', 'tts_settings.qml', 'tts_reader.py',
                        'win_autostart.py', 'tts_neural.py', 'tts_voices.py', 'tts_i18n.py', 'tts_langs.py',
                        'win_hotkeys.py', 'win_ocr.py', 'win_sapi.py', 'tts_previews.py', 'tts_icons.py', 'tts_audio.py',
                        'tts_timing.py', 'tts_follow.py') {
            Copy-Item (Join-Path $Repo "bin\$f") (Join-Path $BinDir $f) -Force
        }
        # Version shown in the settings window and the tray tooltip
        if (Test-Path (Join-Path $Repo 'VERSION')) {
            Copy-Item (Join-Path $Repo 'VERSION') (Join-Path $BinDir 'recito-version.txt') -Force
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
    }
    # tts-rules: command line for the text rules (for power users and AI assistants).
    # tts_rules.py writes it, the same code the tests check.
    & $Py (Join-Path $BinDir 'tts_rules.py') write-wrapper (Join-Path $BinDir 'tts-rules.cmd') | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Say "    Warning: could not write tts-rules.cmd (exit $LASTEXITCODE)." 'Yellow'
    }
    # ffmpeg decodes Edge's MP3 and imports reference voices. The static build
    # from imageio-ffmpeg is copied next to the program, which puts it on the
    # app's PATH (tts_platform) when the system has no ffmpeg of its own.
    $ff = Join-Path $BinDir 'ffmpeg.exe'
    if ((Invoke-Logged $Py @('-c',
            'import imageio_ffmpeg, shutil, sys; shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(), sys.argv[1])',
            $ff)) -ne 0) {
        Say "    Warning: ffmpeg could not be set up; Edge voices and voice import need it." 'Yellow'
    }

    # --- 4/7  voices.conf (idempotent, adds Windows keys) ------------------- #
    Step '4/7' 'Configuration'
    New-Item -ItemType Directory -Force $PiperDir | Out-Null
    $conf = Join-Path $PiperDir 'voices.conf'
    if (-not (Test-Path $conf)) {
        $tpl = Join-Path $Repo 'config\voices.conf'
        if (-not (Test-Path $tpl)) { $tpl = $Template }
        Copy-Item $tpl $conf
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
        Say "    + added: $($append -join ', ')"
    }

    # --- 5/7  Download voices ----------------------------------------------- #
    Step '5/7' 'Downloading Piper voices'
    # Piper voices, taken from voices.conf (<lang>=<onnx base name>). Offline this
    # only warns: the settings window and the setup assistant offer the missing
    # voice again at the first start.
    $piperVoices = (Get-Content $conf) | ForEach-Object {
        if ($_ -match '^\s*([a-z]{2})\s*=\s*([a-z]{2}_[A-Z]{2}-[\w\-\.]+)\s*$') { $matches[2] }
    } | Select-Object -Unique
    foreach ($base in $piperVoices) {
        if (-not (Test-Path (Join-Path $PiperDir "$base.onnx"))) {
            Say "    downloading Piper voice $base ..."
            Invoke-Logged $Py @('-m', 'piper.download_voices', $base, '--download-dir', $PiperDir) | Out-Null
            if (-not (Test-Path (Join-Path $PiperDir "$base.onnx"))) {
                Say "    (download failed, the app offers it at the first start: $base)" 'Yellow'
                Write-Status "WARN|voice $base not downloaded"
            }
        }
    }
    # Kokoro is a model server with its own venv: neural\kokoro2\setup.ps1
    # (optional, not part of this run).

    # Voice previews, so "Listen" in the setup assistant plays at once: Piper and
    # Edge, rendered in the background into %LOCALAPPDATA%\tts-previews (log:
    # build.log there). Offline nothing is rendered; the missing previews are
    # saved the first time a voice is played.
    if (-not $NoPreviews) {
        $prevExe = if (Test-Path $Pyw) { $Pyw } else { $Py }
        Start-Process -FilePath $prevExe -WorkingDirectory $BinDir -WindowStyle Hidden `
            -ArgumentList "`"$(Join-Path $BinDir 'tts_previews.py')`" build --engine piper --engine edge"
    }

    # --- 6/7  Autostart and Start menu -------------------------------------- #
    Step '6/7' 'Autostart and Start menu shortcut'
    if (-not $NoShortcuts) {
        # Autostart and Start menu shortcut ('Recito TTS', searchable) through the
        # app logic (single source of truth): app icon and the tray's AppUserModelID.
        # Both calls first delete the shortcuts of the old name (TTS-Vorlesen.lnk and Recito.lnk in the
        # Startup folder and the Start menu), so the tray does not start twice.
        Invoke-Logged $Py @('-c',
            "import sys; sys.path.insert(0, r'$BinDir'); import win_autostart; win_autostart.set_enabled(True); win_autostart.start_menu_shortcut()") | Out-Null
    }

    # --- 7/7  Start the tray ------------------------------------------------ #
    Step '7/7' 'Starting Recito'
    if (-not $NoStart) {
        $trayPy = Join-Path $BinDir 'tts-tray.py'
        $pywExe = if (Test-Path $Pyw) { $Pyw } else { $Py }
        # Start the tray unless it is already running; it starts the daemon itself.
        # Match on the script name: the preview build runs under the same pythonw.
        if (-not (Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
                  Where-Object { $_.CommandLine -like '*tts-tray.py*' })) {
            Start-Process -FilePath $pywExe -ArgumentList "`"$trayPy`"" -WorkingDirectory $BinDir
        }
    }
}

Write-Status "PID|$PID"
try {
    Install-Recito
} catch {
    Write-Status "FAIL|$($_.Exception.Message)"
    Say "Error: $($_.Exception.Message)" 'Red'
    Say "Log: $LogFile"
    exit 1
} finally {
    # The uv cache holds hundreds of MB of wheels the venv no longer needs.
    if ($Uv -and (Test-Path $UvCache)) { Remove-Item $UvCache -Recurse -Force -ErrorAction SilentlyContinue }
}

Write-Status 'DONE|0'
Say ''
Say '============================================================' 'Green'
Say ' Done. The speaker icon is in the system tray.'
Say ' Default hotkeys (configurable in the tray):'
Say '   Ctrl+Alt+P / Shift+P   Read aloud / fast (clipboard)'
Say '   Ctrl+Alt+O / Shift+O   Read OCR region / fast'
Say '   Ctrl+Alt+L             Pause / resume'
Say '   Ctrl+Alt+S             Stop'
Say ' Engine, voices, speed: tray settings.'
Say ' Manual start: Start menu -> "Recito TTS".'
Say '============================================================' 'Green'
