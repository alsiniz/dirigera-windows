#define AppName "DIRIGERA for Windows"
#define AppExe "Dirigera.exe"

[Setup]
AppId={{987A148D-E765-45DD-AFA9-B462D85EDA6C}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\DIRIGERA
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
MinVersion=10.0.22000
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=DIRIGERA-Windows-{#AppVersion}-x64-Setup
SetupIconFile=..\assets\dirigera.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
CloseApplicationsFilter={#AppExe}
RestartApplications=no

[Files]
Source: "..\dist\Dirigera\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "autostart"; Description: "Start DIRIGERA when I sign in"; GroupDescription: "Windows startup:"; Flags: unchecked

[InstallDelete]
Type: files; Name: "{userstartup}\{#AppName}.lnk"; Tasks: not autostart

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--tray"; WorkingDir: "{app}"; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
