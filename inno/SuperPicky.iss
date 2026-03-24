[Setup]
AppId=SuperPicky
AppName=SuperPicky
AppVersion=4.2.0-113b079
DefaultDirName={autopf}\SuperPicky
DefaultGroupName=SuperPicky
AppPublisherURL=https://superpicky.app/
OutputBaseFilename=SuperPicky_Setup_Win64_4.2.0_113b079
Compression=lzma2/ultra64
LZMAUseSeparateProcess=yes
LZMADictionarySize=1048576
LZMANumFastBytes=273
SolidCompression=yes
CreateAppDir=yes
DirExistsWarning=no
UninstallDisplayIcon={app}\SuperPicky.exe
SetupIconFile=_internal\img\icon.ico
WizardStyle=modern
DisableProgramGroupPage=yes
DisableDirPage=no
DisableReadyPage=no
DisableFinishedPage=no
VersionInfoCompany=https://superpicky.app/
WizardImageFile=_internal\img\icon.png
WizardSmallImageFile=_internal\img\icon.png
AlwaysShowComponentsList=no
AlwaysShowGroupOnReadyPage=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=no

[Registry]
Root: HKLM64; Subkey: "SOFTWARE\SuperPicky"; ValueType: string; ValueName: "InstallDir"; ValueData: "{app}"; Flags: uninsdeletevalue
Root: HKLM64; Subkey: "SOFTWARE\SuperPicky"; ValueType: string; ValueName: "Version"; ValueData: "{#SetupSetting('AppVersion')}"; Flags: uninsdeletevalue
Root: HKLM64; Subkey: "SOFTWARE\SuperPicky"; ValueType: string; ValueName: "UninstallString"; ValueData: """{uninstallexe}"""; Flags: uninsdeletevalue
Root: HKLM64; Subkey: "SOFTWARE\SuperPicky"; ValueType: string; ValueName: "CUDA_Patch_Installed"; ValueData: "0"; Flags: uninsdeletevalue
Root: HKLM64; Subkey: "SOFTWARE\SuperPicky"; ValueType: string; ValueName: "CUDA_Patch_Version"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM64; Subkey: "SOFTWARE\SuperPicky"; ValueType: string; ValueName: "CUDA_Patch_TargetDir"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM64; Subkey: "SOFTWARE\SuperPicky"; ValueType: string; ValueName: "CUDA_Patch_FileList"; ValueData: ""; Flags: uninsdeletevalue
Root: HKLM64; Subkey: "SOFTWARE\SuperPicky"; ValueType: string; ValueName: "CUDA_Patch_InstalledAt"; ValueData: ""; Flags: uninsdeletevalue

[Code]
const
  AppRegistryKey = 'SOFTWARE\SuperPicky';
  UninstallKeyAppId = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\SuperPicky';
  UninstallKeyLegacy = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\SuperPicky_is1';
  PatchManifestRelativePath = '_internal\cuda_patch_manifest.txt';

var
  PreviousInstallDir: string;
  PreviousUninstallString: string;
  PatchCleanupWarnings: string;

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

function QueryUninstallString(var Value: string): Boolean;
begin
  Result :=
    QueryStringValue(HKLM64, AppRegistryKey, 'UninstallString', Value) or
    QueryStringValue(HKLM64, UninstallKeyAppId, 'UninstallString', Value) or
    QueryStringValue(HKLM64, UninstallKeyLegacy, 'UninstallString', Value) or
    QueryStringValue(HKLM, AppRegistryKey, 'UninstallString', Value) or
    QueryStringValue(HKLM, UninstallKeyAppId, 'UninstallString', Value) or
    QueryStringValue(HKLM, UninstallKeyLegacy, 'UninstallString', Value) or
    QueryStringValue(HKCU, AppRegistryKey, 'UninstallString', Value) or
    QueryStringValue(HKCU, UninstallKeyAppId, 'UninstallString', Value) or
    QueryStringValue(HKCU, UninstallKeyLegacy, 'UninstallString', Value);
end;

function QueryPatchValue(const ValueName: string; var Value: string): Boolean;
begin
  Result :=
    QueryStringValue(HKLM64, AppRegistryKey, ValueName, Value) or
    QueryStringValue(HKLM, AppRegistryKey, ValueName, Value) or
    QueryStringValue(HKCU, AppRegistryKey, ValueName, Value);
end;

procedure LoadPreviousInstallState;
begin
  PreviousInstallDir := '';
  PreviousUninstallString := '';
  QueryInstallDir(PreviousInstallDir);
  QueryUninstallString(PreviousUninstallString);
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

function IsPathUnderBase(const BaseDir, CandidatePath: string): Boolean;
var
  NormalizedBase: string;
  NormalizedCandidate: string;
begin
  NormalizedBase := NormalizePath(BaseDir);
  NormalizedCandidate := NormalizePath(CandidatePath);
  if (NormalizedBase = '') or (NormalizedCandidate = '') then
  begin
    Result := False;
    exit;
  end;

  Result := Pos(AddBackslash(NormalizedBase), AddBackslash(NormalizedCandidate)) = 1;
end;

function ExtractCommand(const CommandLine: string; var Executable, Parameters: string): Boolean;
var
  TrimmedLine: string;
  QuoteEnd: Integer;
  SpacePos: Integer;
begin
  Result := False;
  Executable := '';
  Parameters := '';
  TrimmedLine := Trim(CommandLine);
  if TrimmedLine = '' then
    exit;

  if TrimmedLine[1] = '"' then
  begin
    Delete(TrimmedLine, 1, 1);
    QuoteEnd := Pos('"', TrimmedLine);
    if QuoteEnd = 0 then
      exit;
    Executable := Copy(TrimmedLine, 1, QuoteEnd - 1);
    Parameters := Trim(Copy(TrimmedLine, QuoteEnd + 1, MaxInt));
  end
  else
  begin
    SpacePos := Pos(' ', TrimmedLine);
    if SpacePos = 0 then
      Executable := TrimmedLine
    else
    begin
      Executable := Copy(TrimmedLine, 1, SpacePos - 1);
      Parameters := Trim(Copy(TrimmedLine, SpacePos + 1, MaxInt));
    end;
  end;

  Result := Executable <> '';
end;

function EnsureSilentUninstallParams(const ExistingParams: string): string;
var
  UpperParams: string;
begin
  Result := Trim(ExistingParams);
  UpperParams := Uppercase(Result);
  if Pos('/VERYSILENT', UpperParams) = 0 then
    Result := Trim(Result + ' /VERYSILENT');
  if Pos('/SUPPRESSMSGBOXES', UpperParams) = 0 then
    Result := Trim(Result + ' /SUPPRESSMSGBOXES');
  if Pos('/NORESTART', UpperParams) = 0 then
    Result := Trim(Result + ' /NORESTART');
  if Pos('/SP-', UpperParams) = 0 then
    Result := Trim(Result + ' /SP-');
end;

function RunPreviousUninstaller(): Boolean;
var
  UninstallExe: string;
  UninstallParams: string;
  ResultCode: Integer;
begin
  Result := True;
  if PreviousUninstallString = '' then
    exit;

  if not ExtractCommand(PreviousUninstallString, UninstallExe, UninstallParams) then
  begin
    Result := False;
    exit;
  end;

  if not FileExists(UninstallExe) then
  begin
    Result := False;
    exit;
  end;

  UninstallParams := EnsureSilentUninstallParams(UninstallParams);
  if not Exec(UninstallExe, UninstallParams, ExtractFileDir(UninstallExe), SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode) then
  begin
    Result := False;
    exit;
  end;

  Result := ResultCode = 0;
end;

procedure AppendPatchCleanupWarning(const MessageText: string);
begin
  if Trim(MessageText) = '' then
    exit;

  if PatchCleanupWarnings <> '' then
    PatchCleanupWarnings := PatchCleanupWarnings + #13#10;
  PatchCleanupWarnings := PatchCleanupWarnings + MessageText;
end;

function IsSafeRelativePatchPath(const RelativePath: string): Boolean;
var
  NormalizedPath: string;
begin
  NormalizedPath := Trim(RelativePath);
  StringChangeEx(NormalizedPath, '/', '\', True);
  if NormalizedPath = '' then
  begin
    Result := False;
    exit;
  end;

  Result :=
    (Pos('..', NormalizedPath) = 0) and
    (Pos(':', NormalizedPath) = 0) and
    (NormalizedPath[1] <> '\') and
    (NormalizedPath[1] <> '/');
end;

procedure RemoveEmptyParentDirs(StartingDir, AppDir: string);
begin
  StartingDir := Trim(StartingDir);
  AppDir := Trim(AppDir);

  while IsPathUnderBase(AppDir, StartingDir) and (not PathsEqual(StartingDir, AppDir)) do
  begin
    if not RemoveDir(StartingDir) then
      exit;
    StartingDir := ExtractFileDir(StartingDir);
  end;
end;

procedure ClearPatchRegistryValues;
begin
  RegDeleteValue(HKLM64, AppRegistryKey, 'CUDA_Patch_Installed');
  RegDeleteValue(HKLM64, AppRegistryKey, 'CUDA_Patch_Version');
  RegDeleteValue(HKLM64, AppRegistryKey, 'CUDA_Patch_TargetDir');
  RegDeleteValue(HKLM64, AppRegistryKey, 'CUDA_Patch_FileList');
  RegDeleteValue(HKLM64, AppRegistryKey, 'CUDA_Patch_InstalledAt');
  RegDeleteValue(HKLM, AppRegistryKey, 'CUDA_Patch_Installed');
  RegDeleteValue(HKLM, AppRegistryKey, 'CUDA_Patch_Version');
  RegDeleteValue(HKLM, AppRegistryKey, 'CUDA_Patch_TargetDir');
  RegDeleteValue(HKLM, AppRegistryKey, 'CUDA_Patch_FileList');
  RegDeleteValue(HKLM, AppRegistryKey, 'CUDA_Patch_InstalledAt');
  RegDeleteValue(HKCU, AppRegistryKey, 'CUDA_Patch_Installed');
  RegDeleteValue(HKCU, AppRegistryKey, 'CUDA_Patch_Version');
  RegDeleteValue(HKCU, AppRegistryKey, 'CUDA_Patch_TargetDir');
  RegDeleteValue(HKCU, AppRegistryKey, 'CUDA_Patch_FileList');
  RegDeleteValue(HKCU, AppRegistryKey, 'CUDA_Patch_InstalledAt');
end;

function ResolvePatchManifestPath(const AppDir: string): string;
begin
  Result := AddBackslash(AppDir) + PatchManifestRelativePath;
  QueryPatchValue('CUDA_Patch_FileList', Result);
  if not IsPathUnderBase(AppDir, Result) then
    Result := AddBackslash(AppDir) + PatchManifestRelativePath;
end;

procedure CleanupCudaPatchArtifacts;
var
  AppDir: string;
  ManifestPath: string;
  TargetDir: string;
  PatchInstalledFlag: string;
  Lines: TArrayOfString;
  RelativePath: string;
  FullPath: string;
  I: Integer;
begin
  AppDir := ExpandConstant('{app}');
  ManifestPath := ResolvePatchManifestPath(AppDir);
  TargetDir := '';
  PatchInstalledFlag := '';
  QueryPatchValue('CUDA_Patch_TargetDir', TargetDir);
  QueryPatchValue('CUDA_Patch_Installed', PatchInstalledFlag);

  if (TargetDir <> '') and (not PathsEqual(TargetDir, AppDir)) then
    AppendPatchCleanupWarning('检测到旧补丁记录的目标目录与当前卸载目录不一致，已仅清理当前安装目录内的补丁痕迹。');

  if FileExists(ManifestPath) then
  begin
    if LoadStringsFromFile(ManifestPath, Lines) then
    begin
      for I := 0 to GetArrayLength(Lines) - 1 do
      begin
        RelativePath := Trim(Lines[I]);
        if RelativePath <> '' then
        begin
          if not IsSafeRelativePatchPath(RelativePath) then
            AppendPatchCleanupWarning('已跳过异常的补丁清单项: ' + RelativePath)
          else
          begin
            StringChangeEx(RelativePath, '/', '\', True);
            FullPath := AddBackslash(AppDir) + RelativePath;
            if not IsPathUnderBase(AppDir, FullPath) then
              AppendPatchCleanupWarning('已跳过目录外路径: ' + RelativePath)
            else if FileExists(FullPath) then
            begin
              if DeleteFile(FullPath) then
                RemoveEmptyParentDirs(ExtractFileDir(FullPath), AppDir)
              else
                AppendPatchCleanupWarning('无法删除 CUDA 补丁文件: ' + RelativePath);
            end;
          end;
        end;
      end;
    end
    else
      AppendPatchCleanupWarning('无法读取 CUDA 补丁清单，部分补丁文件可能需要手动删除。');

    if DeleteFile(ManifestPath) then
      RemoveEmptyParentDirs(ExtractFileDir(ManifestPath), AppDir)
    else if FileExists(ManifestPath) then
      AppendPatchCleanupWarning('无法删除 CUDA 补丁清单文件。');
  end
  else if Trim(PatchInstalledFlag) = '1' then
    AppendPatchCleanupWarning('未找到 CUDA 补丁清单，部分补丁文件可能需要手动清理。');

  ClearPatchRegistryValues;
end;

function InitializeSetup(): Boolean;
begin
  LoadPreviousInstallState;
  Result := True;
end;

procedure InitializeWizard;
begin
  if PreviousInstallDir <> '' then
    WizardForm.DirEdit.Text := PreviousInstallDir;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if PreviousUninstallString = '' then
    exit;

  WizardForm.StatusLabel.Caption := '正在卸载旧版本，请稍候...';

  if not RunPreviousUninstaller() then
    Result := '无法自动卸载旧版本，请关闭程序后手动卸载再重试。';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    CleanupCudaPatchArtifacts
  else if (CurUninstallStep = usPostUninstall) and (PatchCleanupWarnings <> '') then
    SuppressibleMsgBox(
      'SuperPicky 卸载时已尽力清理 CUDA 补丁文件，但仍有部分内容可能需要手动删除：' + #13#10#13#10 + PatchCleanupWarnings,
      mbInformation,
      MB_OK,
      IDOK
    );
end;

[Files]
Source: "SuperPicky.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\SuperPicky"; Filename: "{app}\SuperPicky.exe"
Name: "{commondesktop}\SuperPicky"; Filename: "{app}\SuperPicky.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkablealone

[Run]
Filename: "{app}\SuperPicky.exe"; Description: "{cm:LaunchProgram,SuperPicky}"; Flags: nowait postinstall skipifsilent
Filename: "https://superpicky.app/"; Description: "访问项目网站"; Flags: postinstall skipifsilent shellexec

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
