; SuperPicky Full 安装脚本
; SuperPicky Full installer script
; Non-commercial use only

#define MyAppName "SuperPicky"
#define MyAppVersion "unknown"
#define MyAppPublisher "JamesPhotography"
#define MyAppURL "superpicky.app"
#define MyAppExeName "SuperPicky.exe"
#define MyAppCommitHash "unknown"
#define OutputBaseFilename "SuperPicky_Setup_Full_Win64_" + MyAppVersion + "_" + MyAppCommitHash

[Setup]
AppId={{B7E3F2A1-8D4C-4F5A-9E6B-1C2D3E4F5A6B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\SuperPicky
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=output
OutputBaseFilename={#OutputBaseFilename}
SetupIconFile=img\icon.ico
Compression=lzma2/ultra64
LZMAUseSeparateProcess=yes
LZMADictionarySize=1048576
LZMANumFastBytes=273
SolidCompression=yes
WizardStyle=modern
WizardImageFile=img\icon.png
WizardSmallImageFile=img\icon.png
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkablealone

[InstallDelete]
; 升级前先清空旧 _internal，避免上一版残留的 .pyd/.dll 干扰新版本（参见 issue #100 讨论）
; Wipe stale _internal before copying so old modules/DLLs can't shadow the new build.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
Filename: "https://superpicky.app/"; Description: "访问项目网站"; Flags: postinstall skipifsilent shellexec

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
