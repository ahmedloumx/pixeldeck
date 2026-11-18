; PixelDeck's Windows installer (Inno Setup 6). packaging/build.py compiles it and passes:
;   AppVersion  1.2.0          SourceDir  the PyInstaller app folder
;   OutputDir   where Setup goes   IconFile  pixeldeck.ico
; A per-user install: no administrator rights, files in %LOCALAPPDATA%\Programs\PixelDeck.
#ifndef AppVersion
  #error Run packaging/build.py: it passes AppVersion, SourceDir, OutputDir and IconFile
#endif

[Setup]
AppId={{4D50F1D7-FC8D-41E0-8257-F35E9FDC2EA5}
AppName=PixelDeck
AppVersion={#AppVersion}
AppVerName=PixelDeck {#AppVersion}
AppPublisher=PixelDeck
AppPublisherURL=https://github.com/ahmedloumx/pixeldeck
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\PixelDeck
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=PixelDeck-Setup-{#AppVersion}-x64
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\pixeldeckw.exe
UninstallDisplayName=PixelDeck
LicenseFile={#SourceDir}\_internal\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The [Code] below stops the server; Restart Manager would also close the terminals' shells.
CloseApplications=no
ChangesEnvironment=yes

[Tasks]
Name: "startmenu"; Description: "Add PixelDeck to the Start menu"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked
Name: "path"; Description: "Add the pixeldeck command to PATH, for terminals and agents (pixeldeck --propose)"

[InstallDelete]
; The files of the version before: a module it had and this one dropped must not linger.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\PixelDeck"; Filename: "{app}\pixeldeckw.exe"; Tasks: startmenu
Name: "{autodesktop}\PixelDeck"; Filename: "{app}\pixeldeckw.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\pixeldeckw.exe"; Description: "Start PixelDeck"; Flags: nowait postinstall skipifsilent

[Code]
const
  EnvKey = 'Environment';
  BusyExit = 5;  { pixeldeck --stop-if-idle: a terminal or a proposal is open }

var
  RemoveSettings: Boolean;

{ Stop the running server before its files are replaced or removed, but never under open
  terminals: that would end a Claude run. Answers '' when it is stopped or was not running. }
function StopPixelDeck(): String;
var
  Exe: String;
  Code: Integer;
begin
  Result := '';
  Exe := ExpandConstant('{app}\pixeldeck.exe');
  if not FileExists(Exe) then
    Exit;
  if Exec(Exe, '--stop-if-idle', '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = BusyExit) then
    Result := 'PixelDeck has open terminals or a pending proposal. Finish them, or run  pixeldeck --stop  to close them, then try again.';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := StopPixelDeck();
end;

function OnPath(Dir: String; var Paths: String): Integer;
begin
  Result := 0;
  if RegQueryStringValue(HKCU, EnvKey, 'Path', Paths) then
    Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Paths) + ';');
end;

{ Inno cannot read a value's type: a Path with %VARIABLES% must stay expandable, and one
  without stays a plain string, as most were. }
procedure WritePath(Paths: String);
begin
  if Pos('%', Paths) > 0 then
    RegWriteExpandStringValue(HKCU, EnvKey, 'Path', Paths)
  else
    RegWriteStringValue(HKCU, EnvKey, 'Path', Paths);
end;

procedure AddToPath(Dir: String);
var
  Paths: String;
begin
  if OnPath(Dir, Paths) > 0 then
    Exit;
  if (Paths <> '') and (Copy(Paths, Length(Paths), 1) <> ';') then
    Paths := Paths + ';';
  WritePath(Paths + Dir);
end;

procedure RemoveFromPath(Dir: String);
var
  Paths: String;
  At: Integer;
begin
  At := OnPath(Dir, Paths);
  if At = 0 then
    Exit;
  { At is where Dir starts in Paths; take one separator with it }
  if At > 1 then
    Delete(Paths, At - 1, Length(Dir) + 1)
  else
    Delete(Paths, At, Length(Dir) + 1);
  WritePath(Paths);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('path') then
    AddToPath(ExpandConstant('{app}'));
end;

function InitializeUninstall(): Boolean;
var
  Message: String;
begin
  Message := StopPixelDeck();
  Result := Message = '';
  if not Result then
    SuppressibleMsgBox(Message, mbError, MB_OK, IDOK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    RemoveSettings := SuppressibleMsgBox('Also remove your PixelDeck settings and token?' + #13#10#13#10 +
      'Settings: ' + ExpandConstant('{userappdata}\PixelDeck') + #13#10 +
      'Saved terminals, logs and cache: ' + ExpandConstant('{localappdata}\PixelDeck') + #13#10 +
      'Token: Windows Credential Manager' + #13#10#13#10 +
      'A PixelDeck run from a git clone uses the same folders.',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES;
    if RemoveSettings then
      Exec(ExpandConstant('{app}\pixeldeck.exe'), '--forget-tokens', '', SW_HIDE, ewWaitUntilTerminated, Code);
    RemoveFromPath(ExpandConstant('{app}'));
  end;
  if (CurUninstallStep = usPostUninstall) and RemoveSettings then
  begin
    DelTree(ExpandConstant('{userappdata}\PixelDeck'), True, True, True);
    DelTree(ExpandConstant('{localappdata}\PixelDeck'), True, True, True);
  end;
end;
