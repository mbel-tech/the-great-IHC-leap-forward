; Inno Setup script: wraps the folder build.ps1 makes into The-Great-IHC-Leap-Forward-Setup.exe.
;
;   iscc /DAppVersion=1.2.0 /DBundle=dist\LS-IHC packaging\windows\installer.iss
;
; The shortcuts' AppUserModelID must match WINDOWS_APP_ID in app/__main__.py,
; or a pinned shortcut and the running window show as two taskbar buttons.
;
; Per-user, no administrator rights: installs to %LOCALAPPDATA%\Programs, which
; the app can write to (it keeps its self-test log beside itself; the crash log
; is per-user, in %LOCALAPPDATA%\The Great IHC Leap Forward\logs).
; Studies and settings live in %USERPROFILE%\.ls-pipeline and are never touched
; by installing, upgrading or uninstalling.

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#ifndef Bundle
  #define Bundle "..\..\dist\LS-IHC"
#endif

[Setup]
AppId={{6C1E4B0A-7D52-4E43-9C0B-2F5A8E1D3B77}
AppName=The Great IHC Leap Forward
AppVersion={#AppVersion}
AppPublisher=mbel-tech
AppPublisherURL=https://github.com/mbel-tech/the-great-IHC-leap-forward
DefaultDirName={autopf}\The Great IHC Leap Forward
DefaultGroupName=The Great IHC Leap Forward
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist
OutputBaseFilename=The-Great-IHC-Leap-Forward-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
WizardStyle=modern
UninstallDisplayName=The Great IHC Leap Forward {#AppVersion}
; The logo (app/assets/icon.ico) on the Setup .exe, in Apps & features and on
; the shortcuts.
SetupIconFile=..\..\app\assets\icon.ico
UninstallDisplayIcon={app}\app\assets\icon.ico

[InstallDelete]
; An upgrade replaces the code and the interpreter wholesale. Files a newer
; library version no longer ships would otherwise linger in site-packages and
; can shadow the new ones.
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\scripts"
Type: filesandordirs; Name: "{app}\analysis"
Type: filesandordirs; Name: "{app}\atlas"
Type: filesandordirs; Name: "{app}\R"
Type: filesandordirs; Name: "{app}\keras"
; The in-app updater's downloads and its kept previous version, which belong
; to the copy being replaced.
Type: filesandordirs; Name: "{app}\updates"

[Files]
Source: "{#Bundle}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\The Great IHC Leap Forward"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-m app"; WorkingDir: "{app}"; IconFilename: "{app}\app\assets\icon.ico"; AppUserModelID: "mbel-tech.TheGreatIHCLeapForward"
Name: "{group}\Uninstall The Great IHC Leap Forward"; Filename: "{uninstallexe}"
Name: "{userdesktop}\The Great IHC Leap Forward"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-m app"; WorkingDir: "{app}"; IconFilename: "{app}\app\assets\icon.ico"; AppUserModelID: "mbel-tech.TheGreatIHCLeapForward"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"

[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: "-m app"; WorkingDir: "{app}"; Description: "Start The Great IHC Leap Forward"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Written by the app at run time, beside itself (the crash log only by older
; versions; it now lives in %LOCALAPPDATA%).
Type: files; Name: "{app}\lsapp-crash.log"
Type: files; Name: "{app}\lsapp-selftest.log"
Type: filesandordirs; Name: "{app}\keras"
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\updates"
