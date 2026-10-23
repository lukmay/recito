; Recito-Setup: per-user installer for Windows (Inno Setup 6.4 or newer).
;
;   python tools/fetch_uv.py                       puts uv.exe into installer\
;   ISCC.exe /DAppVersion=1.0.0 installer\recito.iss     writes dist\Recito-Setup-1.0.0.exe
;
; The installer copies the program files, then runs install.ps1 hidden. That
; script creates the venv with the bundled uv and a uv-managed Python 3.12,
; installs the packages, downloads the default Piper voices and creates the
; autostart and Start menu shortcuts through bin\win_autostart.py.
;
; Folders are the historical ones, so a setup made by install.ps1 is upgraded in
; place and keeps its settings:
;   %LOCALAPPDATA%\piper-bin      program (DefaultDirName)
;   %LOCALAPPDATA%\piper-venv     venv; %LOCALAPPDATA%\piper-python holds uv's Python
;   %APPDATA%\piper               voices.conf and Piper voices (PIPER_DIR overrides)
; With /DIR=<other folder> the venv and the Python go to <other folder>\venv and
; <other folder>\piper-python. /TESTMODE=1 makes no shortcut, starts no tray and
; renders no previews (used by the installer test, see CONTRIBUTING.md).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{7BDC5E63-641F-496B-BD76-A13E5B3A913E}
AppName=Recito TTS
AppVersion={#AppVersion}
AppVerName=Recito TTS {#AppVersion}
AppPublisher=lukmay
AppPublisherURL=https://github.com/lukmay/recito
AppSupportURL=https://github.com/lukmay/recito/issues
AppUpdatesURL=https://github.com/lukmay/recito/releases
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\piper-bin
UsePreviousAppDir=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\dist
OutputBaseFilename=Recito-Setup-{#AppVersion}
SetupIconFile=..\assets\icons\app.ico
UninstallDisplayIcon={app}\icons\app.ico
UninstallDisplayName=Recito TTS
LicenseFile=..\LICENSE
WizardStyle=modern
WizardImageFile=wizard.bmp
WizardSmallImageFile=wizard-small.bmp
Compression=lzma2/ultra64
SolidCompression=yes
; Off on purpose: with RedirectionGuard on (the default since 6.6), every child process
; inherits it, and uv then fails to create the junction of its managed Python (error 448).
RedirectionGuard=no
CloseApplications=no
RestartApplications=no
ShowLanguageDialog=auto

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"

[CustomMessages]
en.SetupFailed=Recito could not be set up.%n%n%1%n%nRecito downloads Python and its packages during setup, so it needs an internet connection. Check the connection and run the setup again.%n%nLog: %2
de.SetupFailed=Recito konnte nicht eingerichtet werden.%n%n%1%n%nRecito lädt bei der Einrichtung Python und seine Pakete herunter und braucht dazu eine Internetverbindung. Prüfe die Verbindung und starte das Setup erneut.%n%nProtokoll: %2
en.KeepData=Keep your settings, text rules and downloaded voices?%n%nYes keeps them in %1, so a later setup finds them again. No deletes them.
de.KeepData=Einstellungen, Textregeln und heruntergeladene Stimmen behalten?%n%nJa behält sie in %1, ein späteres Setup findet sie wieder. Nein löscht sie.
en.Preparing=Preparing the setup...
de.Preparing=Einrichtung wird vorbereitet...

[Files]
; Program files, same layout as install.ps1 makes.
Source: "..\bin\*"; DestDir: "{app}"; Excludes: "__pycache__,*.pyc,ffmpeg.exe"; Flags: recursesubdirs ignoreversion
Source: "..\neural\*"; DestDir: "{app}\neural"; Excludes: "__pycache__,*.pyc"; Flags: recursesubdirs ignoreversion
Source: "..\assets\icons\*"; DestDir: "{app}\icons"; Flags: recursesubdirs ignoreversion
Source: "..\VERSION"; DestName: "recito-version.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\config\voices.conf"; DestName: "voices.conf.template"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\install.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "uv.exe"; DestDir: "{app}"; Flags: ignoreversion
; The early "stop a running Recito" call needs the script before {app} exists.
Source: "..\install.ps1"; DestDir: "{tmp}"; Flags: dontcopy

[UninstallDelete]
; Files the setup script creates (tts-rules.cmd, ffmpeg.exe, caches of Python).
Type: filesandordirs; Name: "{app}"

[Run]
; Starts the tray; it starts the daemon, and on the first start it opens the setup assistant.
Filename: "{code:PythonwPath}"; Parameters: """{app}\tts-tray.py"""; WorkingDir: "{app}"; Flags: nowait; Check: ShouldStartTray

[Code]
const
  SYNCHRONIZE = $00100000;
  WAIT_TIMEOUT = 258;

function OpenProcess(Access: Cardinal; Inherit: Integer; Pid: Cardinal): Cardinal;
  external 'OpenProcess@kernel32.dll stdcall';
function WaitForSingleObject(Handle: Cardinal; Milliseconds: Cardinal): Cardinal;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function CloseHandle(Handle: Cardinal): Integer;
  external 'CloseHandle@kernel32.dll stdcall';
procedure ExitProcess(ExitCode: Cardinal);
  external 'ExitProcess@kernel32.dll stdcall';

var
  SetupOk: Boolean;

function IsTestMode: Boolean;
begin
  Result := ExpandConstant('{param:TESTMODE|0}') = '1';
end;

function ShouldStartTray: Boolean;
begin
  Result := SetupOk and not IsTestMode;
end;

function PowerShellExe: String;
begin
  Result := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
end;

{ The venv sits next to the default program folder; with another folder it sits inside. }
function VenvDirFor(const AppDir: String): String;
begin
  if CompareText(ExtractFileName(AppDir), 'piper-bin') = 0 then
    Result := ExtractFilePath(AppDir) + 'piper-venv'
  else
    Result := AppDir + '\venv';
end;

function PythonHomeFor(const VenvDir: String): String;
begin
  Result := ExtractFilePath(VenvDir) + 'piper-python';
end;

function ConfigDir: String;
begin
  Result := GetEnv('PIPER_DIR');
  if Result = '' then
    Result := ExpandConstant('{userappdata}\piper');
end;

function PythonwPath(Param: String): String;
begin
  Result := VenvDirFor(ExpandConstant('{app}')) + '\Scripts\pythonw.exe';
end;

function ScriptArgs(const ScriptPath, Extra: String): String;
begin
  Result := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + ScriptPath +
    '" -BinDir "' + ExpandConstant('{app}') + '" -VenvDir "' + VenvDirFor(ExpandConstant('{app}')) +
    '" -PiperDir "' + ConfigDir + '" ' + Extra;
end;

{ A running Recito locks files in the venv, so it is stopped before anything is copied. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Rc: Integer;
begin
  Result := '';
  ExtractTemporaryFile('install.ps1');
  Exec(PowerShellExe, ScriptArgs(ExpandConstant('{tmp}\install.ps1'), '-StopOnly'), '', SW_HIDE,
    ewWaitUntilTerminated, Rc);
end;

procedure RunSetupScript;
var
  StatusFile, Extra, Line, Failure, LastStep: String;
  Lines: TArrayOfString;
  I, Rc, StepNo, Waited: Integer;
  Pid, Proc: Cardinal;
  Done, Failed, Alive: Boolean;
begin
  StatusFile := ExpandConstant('{tmp}\recito-status.txt');
  DeleteFile(StatusFile);
  Extra := '-Quiet -Uv "' + ExpandConstant('{app}\uv.exe') + '" -SkipCopy -NoStart -Template "' +
    ExpandConstant('{app}\voices.conf.template') + '" -StatusFile "' + StatusFile + '"';
  if IsTestMode then
    Extra := Extra + ' -NoShortcuts -NoPreviews';

  WizardForm.ProgressGauge.Min := 0;
  WizardForm.ProgressGauge.Max := 7;
  WizardForm.ProgressGauge.Position := 0;
  WizardForm.StatusLabel.Caption := CustomMessage('Preparing');
  WizardForm.FilenameLabel.Caption := '';

  Exec(PowerShellExe, ScriptArgs(ExpandConstant('{app}\install.ps1'), Extra), '', SW_HIDE, ewNoWait, Rc);

  Pid := 0; Proc := 0; Waited := 0; Done := False; Failed := False; LastStep := '';
  Failure := '';
  repeat
    Sleep(250);
    Waited := Waited + 250;
    if LoadStringsFromFile(StatusFile, Lines) then
      for I := 0 to GetArrayLength(Lines) - 1 do
      begin
        Line := Trim(Lines[I]);
        if Copy(Line, 1, 4) = 'PID|' then
          Pid := StrToIntDef(Copy(Line, 5, 20), 0)
        else if Copy(Line, 1, 5) = 'STEP ' then
        begin
          if Line <> LastStep then
          begin
            LastStep := Line;
            StepNo := StrToIntDef(Copy(Line, 6, 1), 0);
            WizardForm.ProgressGauge.Position := StepNo - 1;
            WizardForm.StatusLabel.Caption := Copy(Line, Pos('|', Line) + 1, Length(Line));
          end;
        end
        else if Copy(Line, 1, 5) = 'DONE|' then
          Done := True
        else if Copy(Line, 1, 5) = 'FAIL|' then
        begin
          Failed := True;
          Failure := Copy(Line, 6, Length(Line));
        end;
      end;
    if (Proc = 0) and (Pid > 0) then
      Proc := OpenProcess(SYNCHRONIZE, 0, Pid);
    Alive := (Proc <> 0) and (WaitForSingleObject(Proc, 0) = WAIT_TIMEOUT);
  until Done or Failed or ((Proc <> 0) and (not Alive)) or ((Proc = 0) and (Waited > 120000));
  if Proc <> 0 then
    CloseHandle(Proc);

  { The script may have written its last line just before it ended. }
  if (not Done) and (not Failed) and LoadStringsFromFile(StatusFile, Lines) then
    for I := 0 to GetArrayLength(Lines) - 1 do
    begin
      Line := Trim(Lines[I]);
      if Copy(Line, 1, 5) = 'DONE|' then Done := True;
      if Copy(Line, 1, 5) = 'FAIL|' then
      begin
        Failed := True;
        Failure := Copy(Line, 6, Length(Line));
      end;
    end;

  if Done and (not Failed) then
  begin
    WizardForm.ProgressGauge.Position := 7;
    SetupOk := True;
  end else
  begin
    if Failure = '' then
      Failure := 'The setup script ended unexpectedly.';
    Log('Setup script failed: ' + Failure);
    SuppressibleMsgBox(FmtMessage(CustomMessage('SetupFailed'), [Failure, '%TEMP%\recito-install.log']),
      mbError, MB_OK, IDOK);
    { Abort and exceptions leave Setup with exit code 0, so scripted installs would
      not notice the failure; end the process with a real code instead. }
    ExitProcess(2);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Info: String;
begin
  if CurStep = ssPostInstall then
  begin
    { The uninstaller reads this, so it removes exactly what this setup made. }
    Info := 'venv=' + VenvDirFor(ExpandConstant('{app}')) + #13#10 +
            'config=' + ConfigDir + #13#10;
    if IsTestMode then
      Info := Info + 'testmode=1' + #13#10;
    SaveStringToFile(ExpandConstant('{app}\recito-paths.txt'), Info, False);
    RunSetupScript;
  end;
end;

{ ---- Uninstall ---- }

function PathValue(const Lines: TArrayOfString; const Key: String): String;
var
  I: Integer;
begin
  Result := '';
  for I := 0 to GetArrayLength(Lines) - 1 do
    if Pos(Key + '=', Lines[I]) = 1 then
    begin
      Result := Trim(Copy(Lines[I], Length(Key) + 2, Length(Lines[I])));
      Exit;
    end;
end;

{ Deletes a folder tree, but never a drive root or a very short path. }
procedure RemoveTree(const Dir: String);
begin
  if (Length(Dir) > 8) and DirExists(Dir) then
    DelTree(Dir, True, True, True);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Lines: TArrayOfString;
  Venv, Config, Local, NeuralDir, Programs: String;
  TestMode, Keep: Boolean;
  Rc: Integer;
begin
  if CurUninstallStep <> usUninstall then
    Exit;

  Venv := VenvDirFor(ExpandConstant('{app}'));
  Config := ExpandConstant('{userappdata}\piper');
  TestMode := False;
  if LoadStringsFromFile(ExpandConstant('{app}\recito-paths.txt'), Lines) then
  begin
    if PathValue(Lines, 'venv') <> '' then Venv := PathValue(Lines, 'venv');
    if PathValue(Lines, 'config') <> '' then Config := PathValue(Lines, 'config');
    TestMode := PathValue(Lines, 'testmode') = '1';
  end;

  { Stop the tray and the daemon, they run from the venv. }
  Exec(PowerShellExe, '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' +
    ExpandConstant('{app}\install.ps1') + '" -StopOnly -BinDir "' + ExpandConstant('{app}') +
    '" -VenvDir "' + Venv + '"', '', SW_HIDE, ewWaitUntilTerminated, Rc);

  { Settings and voices stay unless the user says otherwise; a silent uninstall keeps them. }
  Keep := True;
  if UninstallSilent then
    Keep := ExpandConstant('{param:PURGE|0}') <> '1'
  else
    Keep := MsgBox(FmtMessage(CustomMessage('KeepData'), [Config]), mbConfirmation, MB_YESNO or MB_DEFBUTTON1) = IDYES;

  RemoveTree(Venv);
  RemoveTree(PythonHomeFor(Venv));

  Local := GetEnv('LOCALAPPDATA');
  if Local = '' then
    Local := ExpandConstant('{localappdata}');
  RemoveTree(Local + '\tts-previews');
  RemoveTree(Local + '\tts-audio');
  if not Keep then
  begin
    RemoveTree(Config);
    NeuralDir := GetEnv('TTS_NEURAL_DIR');
    if NeuralDir = '' then
      NeuralDir := Local + '\tts-neural';
    RemoveTree(NeuralDir);
  end;

  { Autostart and Start menu shortcuts come from win_autostart. }
  if not TestMode then
  begin
    Programs := ExpandConstant('{userprograms}');
    DeleteFile(Programs + '\Recito TTS.lnk');
    DeleteFile(Programs + '\Startup\Recito TTS.lnk');
    DeleteFile(Programs + '\Recito.lnk');
    DeleteFile(Programs + '\Startup\Recito.lnk');
  end;
end;
