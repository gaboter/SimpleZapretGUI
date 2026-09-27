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
Filename: "{app}\{#AppExe}"; Description: "Запустить {#AppName}"; Flags: nowait postinstall shellexec

[UninstallRun]
; остановить обход, удалить службу zapret (если это наша копия), драйвер WinDivert,
; задачу автозапуска и блок в hosts
Filename: "{app}\{#AppExe}"; Parameters: "--uninstall-cleanup"; Flags: runhidden waituntilterminated; RunOnceId: "SZGCleanup"

[Code]
// Все файлы программы хранятся в папке data внутри папки установки. zapret и WinDivert
// не работают, если в пути есть русские (не латинские) буквы, поэтому такую папку выбрать нельзя.
function NextButtonClick(CurPageID: Integer): Boolean;
var
  Dir: String;
  I: Integer;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    Dir := WizardDirValue;
    for I := 1 to Length(Dir) do
      if Ord(Dir[I]) > 127 then
      begin
        MsgBox('В пути установки не должно быть русских букв и других не латинских символов:' + #13#10 +
               Dir + #13#10#13#10 +
               'С таким путём zapret не сможет работать. Выберите другую папку, например' + #13#10 +
               'C:\Program Files\SimpleZapretGUI или C:\SimpleZapretGUI.', mbError, MB_OK);
        Result := False;
        Exit;
      end;
    if Pos('onedrive', Lowercase(Dir)) > 0 then
    begin
      MsgBox('Папка находится в OneDrive — zapret там работает нестабильно. Выберите другую папку.',
             mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  App, Legacy: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    App := ExpandConstant('{app}');
    // место хранения данных в версиях 1.0.x
    Legacy := ExpandConstant('{commonappdata}\SimpleZapretGUI');
    if DirExists(App + '\data') or DirExists(Legacy) then
    begin
      if UninstallSilent or (MsgBox('Удалить также скачанный zapret и все ваши данные' + #13#10 +
         '(настройки, свои стратегии, списки, результаты тестов, журналы)?' + #13#10#13#10 +
         'Если оставить — после повторной установки в ту же папку всё продолжит работать.' + #13#10#13#10 +
         App + '\data', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES) then
      begin
        DelTree(App, True, True, True);
        if DirExists(Legacy) then
          DelTree(Legacy, True, True, True);
      end;
    end
    else
      DelTree(App, True, True, True);
  end;
end;
