; SuperPicky CUDA 补丁安装脚本
; SuperPicky CUDA Patch installer script
; Non-commercial use only
;
; 本脚本是 Full CPU 安装包的 CUDA 差异更新包，不是独立应用安装包。
; This script is a CUDA delta update for the Full CPU installer, not a standalone app installer.

#define MyAppName "SuperPicky"
#define MyAppVersion "unknown"
#define MyAppPublisher "JamesPhotography"
#define MyAppURL "superpicky.app"
#define MyAppExeName "SuperPicky.exe"
#define MyAppCommitHash "unknown"
#define OutputBaseFilename "SuperPicky_CUDA_Patch_Win64_" + MyAppVersion + "_" + MyAppCommitHash

[Setup]
; 与 Full CPU/CUDA 安装包保持同一个 AppId，使补丁作为同一应用的更新写入卸载日志。
; Keep the same AppId as the Full CPU/CUDA installers so this patch is logged as an update of the same app.
AppId={{B7E3F2A1-8D4C-4F5A-9E6B-1C2D3E4F5A6B}
AppName={#MyAppName} CUDA Patch
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} CUDA Patch {#MyAppVersion}
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
CreateUninstallRegKey=no
UpdateUninstallLogAppName=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
chinesesimplified.PatchTargetMissing=所选目录中未找到 SuperPicky.exe。请先安装 SuperPicky Full CPU 版本，再将 CUDA 补丁安装到该目录。
english.PatchTargetMissing=SuperPicky.exe was not found in the selected folder. Install SuperPicky Full CPU first, then install the CUDA patch into that folder.
chinesesimplified.PatchInternalMissing=所选目录中未找到 _internal 目录。该目录不像有效的 SuperPicky Full 安装目录。
english.PatchInternalMissing=The _internal folder was not found in the selected folder. This does not look like a valid SuperPicky Full installation folder.

[Files]
; build_release_win.py 会把 CUDA 差异文件放在脚本同级目录；排除安装器资源和脚本本身。
; build_release_win.py places CUDA delta files beside this script; exclude installer resources and the script itself.
Source: "*"; DestDir: "{app}"; Excludes: "\img\*,\output\*,\*.iss,\ChineseSimplified.isl"; Flags: ignoreversion recursesubdirs

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
function IsValidPatchTarget(const TargetDir: string; var ErrorMessage: string): Boolean;
var
  NormalizedTargetDir: string;
begin
  ErrorMessage := '';
  NormalizedTargetDir := AddBackslash(Trim(TargetDir));

  if not FileExists(NormalizedTargetDir + '{#MyAppExeName}') then
  begin
    ErrorMessage := ExpandConstant('{cm:PatchTargetMissing}');
    Result := False;
    exit;
  end;

  if not DirExists(NormalizedTargetDir + '_internal') then
  begin
    ErrorMessage := ExpandConstant('{cm:PatchInternalMissing}');
    Result := False;
    exit;
  end;

  Result := True;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  ErrorMessage: string;
begin
  Result := True;
  if CurPageID <> wpSelectDir then
    exit;

  Result := IsValidPatchTarget(WizardForm.DirEdit.Text, ErrorMessage);
  if not Result then
    MsgBox(ErrorMessage, mbCriticalError, MB_OK);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ErrorMessage: string;
begin
  if IsValidPatchTarget(ExpandConstant('{app}'), ErrorMessage) then
    Result := ''
  else
    Result := ErrorMessage;
end;
