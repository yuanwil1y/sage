; Sage - Inno Setup installer
; Sage now ships as one complete offline application payload.

#define MyAppName "Sage"
#define MyAppVersion "1.0.14"
#define MyAppPublisher "Williamyuan132"
#define MyAppId "{{A2C7F0E4-1D18-4C4A-9BB5-4B8FE4E74A1D}"

#ifndef CompressionMode
#define CompressionMode "lzma2/fast"
#endif

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://github.com/yuanwil1y/sage
AppSupportURL=https://github.com/yuanwil1y/sage/issues
DefaultDirName={localappdata}\Programs\ValorantTranslator
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=dist\installer
OutputBaseFilename=Sage_Setup
SetupIconFile=resources\Sage.ico
Compression={#CompressionMode}
CompressionThreads=auto
LZMANumBlockThreads=2
SolidCompression=no
MergeDuplicateFiles=yes
WizardStyle=modern
SetupLogging=yes
Uninstallable=yes
UninstallDisplayIcon={app}\resources\Sage.ico
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} offline installer
VersionInfoCopyright=Copyright (C) 2026 Williamyuan132

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："; Flags: unchecked

[Files]
Source: "dist\variants\full\ValorantTranslator\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\ValorantTranslator.exe"; Parameters: "--ui"; WorkingDir: "{app}"; IconFilename: "{app}\resources\Sage.ico"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"; IconFilename: "{app}\resources\Sage.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\ValorantTranslator.exe"; Parameters: "--ui"; WorkingDir: "{app}"; IconFilename: "{app}\resources\Sage.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\ValorantTranslator.exe"; Parameters: "--ui"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /F /T /IM ValorantTranslator.exe"; Flags: runhidden waituntilterminated; RunOnceId: "TerminateValorantTranslator"
Filename: "{cmd}"; Parameters: "/C taskkill /F /T /IM SageWidgetService.exe"; Flags: runhidden waituntilterminated; RunOnceId: "TerminateSageWidgetService"
Filename: "{app}\ValorantTranslator.exe"; Parameters: "--cleanup-gamebar"; Flags: runhidden waituntilterminated; RunOnceId: "CleanupValorantTranslatorGameBar"
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ""$p = Get-AppxPackage -Name 'ValorantTranslator' -ErrorAction SilentlyContinue | Sort-Object Version -Descending | Select-Object -First 1; if ($p) {{ $e = Start-Process -FilePath 'CheckNetIsolation.exe' -ArgumentList @('LoopbackExempt','-d',('-n=' + $p.PackageFamilyName)) -Verb RunAs -WindowStyle Hidden -Wait -PassThru; $p | Remove-AppxPackage -ErrorAction SilentlyContinue }"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveValorantTranslatorWidget"

[Code]
var
  KeepUserData: Boolean;

function InitializeSetup(): Boolean;
begin
  if WizardSilent then
  begin
    Result := True;
    Exit;
  end;
  Result := MsgBox(
    '安装 Sage 时会添加用于 Game Bar 小组件的个人签名证书，' +
    '并自动安装小组件和本机通信权限。' + #13#10 + #13#10 +
    '只有确认安装包来自可信来源时才应继续。',
    mbConfirmation,
    MB_YESNO) = IDYES;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    WizardForm.StatusLabel.Caption := '正在安装 Sage Game Bar 小组件…';
    if (not Exec(
      ExpandConstant('{app}\ValorantTranslator.exe'),
      '--initialize-gamebar',
      ExpandConstant('{app}'),
      SW_HIDE,
      ewWaitUntilTerminated,
      ResultCode)) or (ResultCode <> 0) then
      MsgBox(
        '主程序已经安装，但 Game Bar 小组件没有完成初始化。' + #13#10 +
        '打开 Sage 后点击“修复小组件”可以重试。',
        mbError,
        MB_OK);
  end;
end;

function InitializeUninstall(): Boolean;
var
  Answer: Integer;
begin
  KeepUserData := True;
  Answer := MsgBox(
    '是否删除已下载的模型和配置？' + #13#10 + #13#10 +
    '选择“是”会删除 %LOCALAPPDATA%\ValorantTranslator；' + #13#10 +
    '选择“否”会保留它们，之后重新安装可以继续使用。',
    mbConfirmation,
    MB_YESNOCANCEL);
  if Answer = IDCANCEL then
  begin
    Result := False;
    Exit;
  end;
  KeepUserData := Answer <> IDYES;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if not KeepUserData then
      DelTree(ExpandConstant('{localappdata}\ValorantTranslator'), True, True, True);
  end;
end;
