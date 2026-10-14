; Inno Setup script: wraps the folder build.ps1 makes into LS-IHC-Setup.exe.
;
;   iscc /DAppVersion=1.2.0 /DBundle=dist\LS-IHC packaging\windows\installer.iss
;
; Per-user, no administrator rights: installs to %LOCALAPPDATA%\Programs, which
; the app can write to (it keeps its crash and self-test logs beside itself).
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
AppName=LS IHC pipeline
AppVersion={#AppVersion}
AppPublisher=mbel-tech
AppPublisherURL=https://github.com/mbel-tech/ls-ihc-pipeline
DefaultDirName={autopf}\LS IHC pipeline
DefaultGroupName=LS IHC pipeline
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist
OutputBaseFilename=LS-IHC-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
WizardStyle=modern
UninstallDisplayName=LS IHC pipeline {#AppVersion}

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

[Files]
Source: "{#Bundle}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\LS IHC pipeline"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-m app"; WorkingDir: "{app}"
Name: "{group}\Uninstall LS IHC pipeline"; Filename: "{uninstallexe}"
Name: "{userdesktop}\LS IHC pipeline"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-m app"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"

[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: "-m app"; WorkingDir: "{app}"; Description: "Start LS IHC pipeline"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Written by the app at run time, beside itself.
Type: files; Name: "{app}\lsapp-crash.log"
Type: files; Name: "{app}\lsapp-selftest.log"
Type: filesandordirs; Name: "{app}\keras"
Type: filesandordirs; Name: "{app}\runtime"
