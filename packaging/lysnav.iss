#define AppName "Lysnav for Windows"
#define AppExe "Lysnav.exe"

[Setup]
AppId={{987A148D-E765-45DD-AFA9-B462D85EDA6C}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\Lysnav
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
MinVersion=10.0.22000
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=Lysnav-Windows-{#AppVersion}-x64-Setup
SetupIconFile=..\build\lysnav.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
CloseApplicationsFilter={#AppExe},Dirigera.exe
RestartApplications=no

[Files]
Source: "..\dist\Lysnav\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "autostart"; Description: "Start Lysnav when I sign in"; GroupDescription: "Windows startup:"; Flags: unchecked

[InstallDelete]
; Retire files owned by the previous release on upgrade.
Type: files; Name: "{app}\Dirigera.exe"
Type: files; Name: "{autoprograms}\DIRIGERA for Windows.lnk"
Type: files; Name: "{userstartup}\DIRIGERA for Windows.lnk"
Type: files; Name: "{userstartup}\{#AppName}.lnk"; Tasks: not autostart

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--tray"; WorkingDir: "{app}"; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
