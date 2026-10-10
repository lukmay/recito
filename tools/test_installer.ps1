#Requires -Version 5.1
<#
  Tests Recito-Setup-<version>.exe in a throwaway folder, never in the real
  %LOCALAPPDATA%\piper-bin, piper-venv or %APPDATA%\piper.

    powershell -ExecutionPolicy Bypass -File tools\test_installer.ps1 -Setup dist\Recito-Setup-1.0.0.exe

  The setup and the uninstaller run with /VERYSILENT /TESTMODE=1 (no shortcut,
  no tray, no previews) and with LOCALAPPDATA, APPDATA, PIPER_DIR and
  TTS_NEURAL_DIR pointing into -Root. It checks the files, the uv-made venv and
  the imports, installs a second time over the first (upgrade, config kept) and
  checks that the uninstaller removes everything. It needs the internet, like
  the real setup. Exit code 0 means every check passed.
#>
param(
    [Parameter(Mandatory)][string]$Setup,
    [string]$Root = (Join-Path $env:TEMP "recito-setup-test-$PID"),
    [switch]$KeepRoot
)
$ErrorActionPreference = 'Stop'
$Setup = (Resolve-Path $Setup).Path
$failed = 0
function Check($ok, $what) {
    if ($ok) { Write-Host "ok    $what" -ForegroundColor Green }
    else { Write-Host "FAIL  $what" -ForegroundColor Red; $script:failed++ }
}

$Local = Join-Path $Root 'local'
$Roam  = Join-Path $Root 'roaming'
New-Item -ItemType Directory -Force $Local, $Roam | Out-Null
$realBin = Join-Path $env:LOCALAPPDATA 'piper-bin'
$App   = Join-Path $Local 'piper-bin'
$Venv  = Join-Path $Local 'piper-venv'
$Conf  = Join-Path $Roam 'piper'
if ($App -eq $realBin) { throw "refusing to test in the real install folder" }

# Child processes see only the throwaway folders.
$saved = @{}
foreach ($n in 'LOCALAPPDATA', 'APPDATA', 'PIPER_DIR', 'TTS_NEURAL_DIR') { $saved[$n] = [Environment]::GetEnvironmentVariable($n) }
$env:LOCALAPPDATA = $Local; $env:APPDATA = $Roam
$env:PIPER_DIR = $Conf; $env:TTS_NEURAL_DIR = Join-Path $Local 'tts-neural'

function Run-Setup($label) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $p = Start-Process $Setup -Wait -PassThru -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
        "/DIR=$App", '/TESTMODE=1', "/LOG=$Root\setup-$label.log"
    Write-Host ("      {0}: exit {1} after {2:N0} s" -f $label, $p.ExitCode, $sw.Elapsed.TotalSeconds)
    return $p.ExitCode
}

try {
    # --- a failing setup reports it (uv cannot download Python) ---------------
    $env:UV_PYTHON_INSTALL_MIRROR = 'http://127.0.0.1:9'
    Check ((Run-Setup 'failure') -ne 0) 'setup without network exits with a non-zero code'
    Remove-Item Env:UV_PYTHON_INSTALL_MIRROR
    $un = Start-Process (Join-Path $App 'unins000.exe') -Wait -PassThru -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/PURGE=1'
    Start-Sleep -Seconds 3
    Check (-not (Test-Path $App)) 'the half-finished install can be uninstalled'

    # --- first install -------------------------------------------------------
    Check ((Run-Setup 'install') -eq 0) 'setup exits with 0'
    $py = Join-Path $Venv 'Scripts\python.exe'
    Check (Test-Path $py) 'venv exists'
    $cfg = Get-Content (Join-Path $Venv 'pyvenv.cfg') -Raw
    Check ($cfg -match 'uv = ') 'venv was made by uv (pyvenv.cfg has "uv =")'
    Check ($cfg -match [regex]::Escape((Join-Path $Local 'piper-python'))) 'venv uses the uv-managed Python inside the test root'
    $ver = & $py -c "import sys; print(sys.version_info[:2])"
    Check ($ver -eq '(3, 12)') "Python is 3.12 ($ver)"
    $imp = & $py -c "import PySide6, piper, numpy, edge_tts, sounddevice, onnxruntime; print('imports ok')"
    Check ($imp -eq 'imports ok') 'PySide6, piper, numpy, edge_tts, sounddevice, onnxruntime import'
    # Every module of the app imports in the new venv (win32 and winsdk packages included).
    $mods = (Get-ChildItem (Join-Path $App 'tts_*.py'), (Join-Path $App 'win_*.py') | ForEach-Object { $_.BaseName }) -join ','
    $all = & $py -c "import sys, importlib; sys.path.insert(0, r'$App'); [importlib.import_module(m) for m in '$mods'.split(',')]; print('modules ok')"
    Check ($all -eq 'modules ok') 'every app module imports'
    foreach ($f in 'tts-tray.py', 'piper-read-daemon.py', 'tts_platform.py', 'win_autostart.py', 'tts-rules.cmd',
                   'ffmpeg.exe', 'uv.exe', 'install.ps1', 'recito-version.txt', 'icons\app.ico', 'ui', 'i18n', 'neural\models.json',
                   'unins000.exe', 'LICENSE') {
        Check (Test-Path (Join-Path $App $f)) "file $f"
    }
    $ff = & (Join-Path $App 'ffmpeg.exe') -version | Select-Object -First 1
    Check ($ff -match '^ffmpeg version') 'bundled ffmpeg runs'
    Check (Test-Path (Join-Path $Conf 'voices.conf')) 'voices.conf written'
    Check ((Get-ChildItem $Conf -Filter *.onnx).Count -ge 1) 'default Piper voice downloaded'
    # Without a system ffmpeg on PATH, as on a fresh PC.
    $realPath = $env:PATH; $env:PATH = "$env:SystemRoot\System32"
    $platPath = & $py -c "import sys; sys.path.insert(0, r'$App'); import tts_platform, shutil; print(shutil.which('ffmpeg'))"
    $env:PATH = $realPath
    Check ($platPath -like "*$App*") 'tts_platform finds the bundled ffmpeg on PATH'

    # The shortcut code runs against the throwaway APPDATA (the setup skipped it in test mode).
    & $py -c "import sys; sys.path.insert(0, r'$App'); import win_autostart; win_autostart.set_enabled(True); win_autostart.start_menu_shortcut()"
    Check (Test-Path (Join-Path $Roam 'Microsoft\Windows\Start Menu\Programs\Startup\Recito.lnk')) 'win_autostart makes the autostart shortcut'
    Check (Test-Path (Join-Path $Roam 'Microsoft\Windows\Start Menu\Programs\Recito.lnk')) 'win_autostart makes the Start menu shortcut'

    # --- upgrade over the existing install ------------------------------------
    Add-Content (Join-Path $Conf 'voices.conf') "`nmy_test_key=keep-me"
    Check ((Run-Setup 'upgrade') -eq 0) 'second setup (upgrade) exits with 0'
    Check ((Get-Content (Join-Path $Conf 'voices.conf') -Raw) -match 'my_test_key=keep-me') 'upgrade keeps the config'
    Check ((& $py -c "import PySide6; print(1)") -eq '1') 'venv still works after the upgrade'

    # --- uninstall, config kept --------------------------------------------------
    $un = Start-Process (Join-Path $App 'unins000.exe') -Wait -PassThru -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', "/LOG=$Root\unins-keep.log"
    Check ($un.ExitCode -eq 0) 'uninstaller exits with 0'
    Start-Sleep -Seconds 3
    Check (-not (Test-Path $App)) 'uninstall removed the program folder'
    Check (-not (Test-Path $Venv)) 'uninstall removed the venv'
    Check (-not (Test-Path (Join-Path $Local 'piper-python'))) 'uninstall removed the uv-managed Python'
    Check (Test-Path (Join-Path $Conf 'voices.conf')) 'silent uninstall keeps the config and voices'
    Check (-not (Test-Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{7BDC5E63-641F-496B-BD76-A13E5B3A913E}_is1")) 'Apps entry is gone'

    # --- reinstall, then uninstall with removal of the config --------------------
    Check ((Run-Setup 'reinstall') -eq 0) 'setup after uninstall exits with 0'
    $un = Start-Process (Join-Path $App 'unins000.exe') -Wait -PassThru -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/PURGE=1', "/LOG=$Root\unins-purge.log"
    Start-Sleep -Seconds 3
    Check (-not (Test-Path $App) -and -not (Test-Path $Venv)) 'purge uninstall removed program and venv'
    Check (-not (Test-Path $Conf)) 'purge uninstall removed the config and voices'
    $left = Get-ChildItem $Local -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -notin 'Microsoft' }
    Check (-not $left) ("nothing left in the test root" + $(if ($left) { ": " + ($left.Name -join ', ') }))
}
finally {
    foreach ($n in $saved.Keys) { [Environment]::SetEnvironmentVariable($n, $saved[$n]) }
    if (-not $KeepRoot) {
        Remove-Item $Root -Recurse -Force -ErrorAction SilentlyContinue
    } else { Write-Host "test root kept: $Root" }
}
if ($failed) { Write-Host "$failed check(s) failed" -ForegroundColor Red; exit 1 }
Write-Host 'all checks passed' -ForegroundColor Green
