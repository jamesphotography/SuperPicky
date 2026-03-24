[Setup]
AppId=SuperPicky.CUDAPatch
AppName=SuperPicky CUDA Patch
AppVersion=4.2.0-113b079
DefaultDirName={autopf}\SuperPicky
DefaultGroupName=SuperPicky
AppPublisherURL=https://superpicky.app/
OutputBaseFilename=SuperPicky_CUDA_Patch_Win64_4.2.0_113b079
Compression=lzma2/ultra64
LZMAUseSeparateProcess=yes
LZMADictionarySize=1048576
LZMANumFastBytes=273
SolidCompression=yes
CreateAppDir=yes
Uninstallable=no
SetupIconFile=img\icon.ico
WizardStyle=modern
DisableProgramGroupPage=yes
DisableDirPage=no
DisableReadyPage=no
DisableFinishedPage=no
DirExistsWarning=no
VersionInfoCompany=https://superpicky.app/
WizardImageFile=img\icon.png
WizardSmallImageFile=img\icon.png
AlwaysShowComponentsList=no
AlwaysShowGroupOnReadyPage=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=no

[Registry]
Root: HKLM64; Subkey: "SOFTWARE\SuperPicky"; ValueType: string; ValueName: "InstallDir"; ValueData: "{app}"
Root: HKLM64; Subkey: "SOFTWARE\SuperPicky"; ValueType: string; ValueName: "Version"; ValueData: "{#SetupSetting('AppVersion')}"
Root: HKLM64; Subkey: "SOFTWARE\SuperPicky"; ValueType: string; ValueName: "CUDA_Patch_Installed"; ValueData: "1"
Root: HKLM64; Subkey: "SOFTWARE\SuperPicky"; ValueType: string; ValueName: "CUDA_Patch_Version"; ValueData: "{#SetupSetting('AppVersion')}"
Root: HKLM64; Subkey: "SOFTWARE\SuperPicky"; ValueType: string; ValueName: "CUDA_Patch_TargetDir"; ValueData: "{app}"
Root: HKLM64; Subkey: "SOFTWARE\SuperPicky"; ValueType: string; ValueName: "CUDA_Patch_FileList"; ValueData: "{app}\_internal\cuda_patch_manifest.txt"
Root: HKLM64; Subkey: "SOFTWARE\SuperPicky"; ValueType: string; ValueName: "CUDA_Patch_InstalledAt"; ValueData: "{code:GetPatchInstallTimestamp}"

[Code]
const
  AppRegistryKey = 'SOFTWARE\SuperPicky';
  UninstallKeyAppId = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\SuperPicky';
  UninstallKeyLegacy = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\SuperPicky_is1';
  PatchManifestRelativePath = '_internal\cuda_patch_manifest.txt';

var
  PreviousInstallDir: string;

function QueryStringValue(const RootKey: Integer; const SubKey, ValueName: string; var Value: string): Boolean;
begin
  Result := RegQueryStringValue(RootKey, SubKey, ValueName, Value) and (Trim(Value) <> '');
end;

function QueryInstallDir(var Value: string): Boolean;
begin
  Result :=
    QueryStringValue(HKLM64, AppRegistryKey, 'InstallDir', Value) or
    QueryStringValue(HKLM64, UninstallKeyAppId, 'Inno Setup: App Path', Value) or
    QueryStringValue(HKLM64, UninstallKeyLegacy, 'Inno Setup: App Path', Value) or
    QueryStringValue(HKLM, AppRegistryKey, 'InstallDir', Value) or
    QueryStringValue(HKLM, UninstallKeyAppId, 'Inno Setup: App Path', Value) or
    QueryStringValue(HKLM, UninstallKeyLegacy, 'Inno Setup: App Path', Value) or
    QueryStringValue(HKCU, AppRegistryKey, 'InstallDir', Value) or
    QueryStringValue(HKCU, UninstallKeyAppId, 'Inno Setup: App Path', Value) or
    QueryStringValue(HKCU, UninstallKeyLegacy, 'Inno Setup: App Path', Value);
end;

function NormalizePath(const Value: string): string;
begin
  Result := Trim(Value);
  StringChangeEx(Result, '/', '\', True);
  while (Length(Result) > 3) and (Result[Length(Result)] = '\') do
    Delete(Result, Length(Result), 1);
  Result := Uppercase(Result);
end;

function PathsEqual(const A, B: string): Boolean;
begin
  Result := NormalizePath(A) = NormalizePath(B);
end;

function ValidatePatchTarget(const TargetDir: string; var ErrorMessage: string): Boolean;
var
  ExpectedDir: string;
  MainExePath: string;
begin
  ErrorMessage := '';
  MainExePath := AddBackslash(TargetDir) + 'SuperPicky.exe';

  if not FileExists(MainExePath) then
  begin
    ErrorMessage := '所选目录中未找到 SuperPicky.exe，请先安装主程序，再将 CUDA 补丁安装到该目录。';
    Result := False;
    exit;
  end;

  if QueryInstallDir(ExpectedDir) and (ExpectedDir <> '') and (not PathsEqual(ExpectedDir, TargetDir)) then
  begin
    ErrorMessage := '所选目录与注册表中的 SuperPicky 安装目录不一致。请将 CUDA 补丁安装到现有 SuperPicky 安装目录。';
    Result := False;
    exit;
  end;

  Result := True;
end;

function GetPatchInstallTimestamp(Param: string): string;
begin
  Result := GetDateTimeString('yyyy-mm-dd hh:nn:ss', '-', ':');
end;

function InitializeSetup(): Boolean;
begin
  PreviousInstallDir := '';
  QueryInstallDir(PreviousInstallDir);
  Result := True;
end;

procedure InitializeWizard;
begin
  if PreviousInstallDir <> '' then
    WizardForm.DirEdit.Text := PreviousInstallDir;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  ErrorMessage: string;
begin
  Result := True;
  if CurPageID <> wpSelectDir then
    exit;

  Result := ValidatePatchTarget(WizardForm.DirEdit.Text, ErrorMessage);
  if not Result then
    MsgBox(ErrorMessage, mbCriticalError, MB_OK);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ErrorMessage: string;
begin
  if ValidatePatchTarget(ExpandConstant('{app}'), ErrorMessage) then
    Result := ''
  else
    Result := ErrorMessage;
end;

[Files]
Source: "SuperPicky.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Run]
Filename: "{app}\SuperPicky.exe"; Description: "{cm:LaunchProgram,SuperPicky}"; Flags: nowait postinstall skipifsilent

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
