; VMI Update Process - Install Wizard
;
; Bootstraps a brand-new client machine: verifies Git is present, installs
; Python if a working one (with Tkinter) isn't already there, clones this
; repo via plain HTTPS (public repo, no credentials needed), installs its
; Python dependencies, and hands off into control_panel.py for the
; configuration and scheduled-task steps that tool already covers.
;
; Deliberately out of scope: this installer never runs sql/erp_send_state.sql
; and never creates a scheduled task itself -- both need a human's judgment
; against this specific customer's database, exactly as DEPLOYMENT.md and
; control_panel.py's Scheduled Tasks tab already require.
;
; Only for brand-new machines -- every already-deployed site keeps
; self-updating via its existing update_scripts.bat git pull, untouched by
; this installer.
;
; Note on uninstalling (confirmed by testing): the generated uninstaller only
; removes what Inno itself placed (shortcuts, registry entry, its own
; unins000.* files) -- it has no visibility into the repo files the [Run]
; git commands checked out, so it leaves {app} (config.ini, logs, the cloned
; repo) behind rather than guessing what's safe to delete. That's the
; desired behavior, not a bug: uninstalling should never risk silently
; wiping a live machine's configuration/credentials/logs.
;
; Build with installer\build.ps1 (downloads the bundled Python installer and
; invokes Inno Setup's compiler).

#define MyAppName "VMI Update Process"
#define MyAppVersion "1.0"
#define MyAppPublisher "AFI"
#define MyRepoUrl "https://github.com/Lucas-AFI/VMI-Standardized.git"
#define PythonInstallDir "C:\Python312-VMI"
#define PythonInstallerFile "python-3.12.7-amd64.exe"

[Setup]
AppId={{501B1081-9A48-4FF0-A6BA-88D939C0C465}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName=C:\update_process
DefaultGroupName=VMI Update Process
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=Output
OutputBaseFilename=VMI-Update-Process-Setup
SetupIconFile=assets\setup_icon.ico
WizardImageFile=assets\wizard_large.bmp
WizardSmallImageFile=assets\wizard_small.bmp
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"; Flags: unchecked

[Dirs]
Name: "{app}"

[Files]
Source: "vendor\{#PythonInstallerFile}"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedsPythonInstall

[Icons]
Name: "{group}\VMI Control Panel"; Filename: "{code:GetPythonwExe}"; Parameters: """{app}\control_panel.py"" --no-relaunch"; WorkingDir: "{app}"
Name: "{autodesktop}\VMI Control Panel"; Filename: "{code:GetPythonwExe}"; Parameters: """{app}\control_panel.py"" --no-relaunch"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{tmp}\{#PythonInstallerFile}"; Parameters: "/quiet InstallAllUsers=1 PrependPath=1 Include_launcher=0 Include_test=0 Include_tcltk=1 Include_pip=1 TargetDir=""{#PythonInstallDir}"""; StatusMsg: "Installing Python..."; Check: NeedsPythonInstall; Flags: waituntilterminated
Filename: "{cmd}"; Parameters: "{code:GetCloneOrPullArgs}"; WorkingDir: "{app}"; StatusMsg: "Downloading VMI Update Process..."; Flags: waituntilterminated runhidden
Filename: "{code:GetPythonExe}"; Parameters: "-m pip install --quiet -r requirements.txt"; WorkingDir: "{app}"; StatusMsg: "Installing Python dependencies..."; Flags: waituntilterminated runhidden
Filename: "{code:GetPythonwExe}"; Parameters: """{app}\control_panel.py"" --no-relaunch"; WorkingDir: "{app}"; Description: "Launch VMI Control Panel"; Flags: postinstall skipifsilent nowait

[Code]
var
  PythonAlreadyOk: Boolean;
  PythonExePath, PythonwExePath: String;

function IsPythonWorking(): Boolean;
var
  ResultCode: Integer;
begin
  // Checks for Tkinter specifically, not just python.exe on PATH -- a
  // Python installed without the optional Tcl/Tk component would otherwise
  // pass a weaker check and break control_panel.py silently later.
  Result := Exec('python', '-c "import tkinter"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
    and (ResultCode = 0);
end;

function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;

  if not (Exec('git', '--version', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0)) then
  begin
    MsgBox('Git is required but was not found on this machine.' + #13#10 + #13#10 +
      'Install Git for Windows from https://git-scm.com/download/win, then run this installer again.',
      mbCriticalError, MB_OK);
    Result := False;
    Exit;
  end;

  PythonAlreadyOk := IsPythonWorking();
  if PythonAlreadyOk then
  begin
    PythonExePath := 'python';
    PythonwExePath := 'pythonw';
  end
  else
  begin
    PythonExePath := '{#PythonInstallDir}\python.exe';
    PythonwExePath := '{#PythonInstallDir}\pythonw.exe';
  end;
end;

function NeedsPythonInstall(): Boolean;
begin
  Result := not PythonAlreadyOk;
end;

function GetPythonExe(Param: String): String;
begin
  Result := PythonExePath;
end;

function GetPythonwExe(Param: String): String;
begin
  Result := PythonwExePath;
end;

function GetCloneOrPullArgs(Param: String): String;
begin
  // Re-running the installer over an existing, already-cloned {app} pulls
  // instead of re-cloning.
  //
  // A plain `git clone <url> .` does NOT work here: Inno's admin-mode
  // installer always drops unins000.dat/unins000.exe into {app} *before*
  // any [Run] entry executes (to have somewhere to record the uninstaller),
  // so the directory is never actually empty when this runs. `git init` +
  // `remote add` + `fetch` + `checkout -f` tolerates that -- it only cares
  // that no *tracked* filename collides with what's already there, which
  // the two uninstaller files never do.
  if DirExists(ExpandConstant('{app}') + '\.git') then
    Result := '/c "git pull origin main"'
  else
    Result := '/c "git init -q . && git remote add origin {#MyRepoUrl} && git fetch -q origin main && git checkout -q -f main"';
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
  begin
    WizardForm.FinishedLabel.Caption :=
      'Setup has installed ' + '{#MyAppName}' + ' to ' + ExpandConstant('{app}') + '.' + #13#10 + #13#10 +
      'Before this machine is live, you still need to:' + #13#10 +
      '  1. Run sql\erp_send_state.sql against this machine''s Matrix database (see DEPLOYMENT.md).' + #13#10 +
      '  2. Use the Configure tab (below) to fill in config.ini and store credentials.' + #13#10 +
      '  3. Use the Scheduled Tasks tab to create the scheduled tasks.' + #13#10 + #13#10 +
      'Click Finish to close this wizard.';
  end;
end;
