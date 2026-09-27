; Inno Setup 6 — установщик SimpleZapretGUI
#define AppName "SimpleZapretGUI"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppExe "SimpleZapretGUI.exe"

[Setup]
AppId={{7C3E2B7A-4E0B-4E7F-9C1A-5A2F3D8B6E11}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=SimpleZapretGUI
AppSupportURL=https://github.com/Flowseal/zapret-discord-youtube
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
AppMutex=SimpleZapretGUI_Mutex
CloseApplications=yes
OutputDir=..\dist-installer
OutputBaseFilename=SimpleZapretGUI-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Ярлык на рабочем столе"; GroupDescription: "Дополнительно:"

[Files]
Source: "..\dist\SimpleZapretGUI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Удалить {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Запустить {#AppName}"; Flags: nowait postinstall skipifsilent shellexec

[UninstallRun]
; остановить обход, удалить службу zapret (если это наша копия), драйвер WinDivert,
; задачу автозапуска и блок в hosts
Filename: "{app}\{#AppExe}"; Parameters: "--uninstall-cleanup"; Flags: runhidden waituntilterminated; RunOnceId: "SZGCleanup"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Data: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Data := ExpandConstant('{commonappdata}\SimpleZapretGUI');
    if DirExists(Data) then
      if UninstallSilent or (MsgBox('Удалить также копию zapret и все пользовательские данные' + #13#10 +
         '(списки, свои стратегии, результаты тестов, настройки)?' + #13#10#13#10 + Data,
         mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES) then
        DelTree(Data, True, True, True);
  end;
end;
