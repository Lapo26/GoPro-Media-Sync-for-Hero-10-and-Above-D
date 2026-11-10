; Instalador de GoPro Sync (Inno Setup 6)
; Lo compila packaging\build.ps1, que pasa AppVersion y SourceDir.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\GoProSync"
#endif

#define AppName "GoPro Sync"
#define AppExe "GoProSync.exe"

[Setup]
; AppId fijo: identifica la app para actualizar/desinstalar. NO cambiarlo.
AppId={{6F2B4C1E-3A5D-4E8B-9C21-7D4A0E5F9B13}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Lapo26
AppPublisherURL=https://github.com/Lapo26/GoPro-Media-Sync-for-Hero-10-and-Above-D
AppSupportURL=https://github.com/Lapo26/GoPro-Media-Sync-for-Hero-10-and-Above-D/issues
AppUpdatesURL=https://github.com/Lapo26/GoPro-Media-Sync-for-Hero-10-and-Above-D/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Por defecto se instala solo para el usuario actual (sin pedir administrador);
; el instalador ofrece instalar para todos los usuarios.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=GoProSync-Setup-{#AppVersion}
SetupIconFile=..\app\assets\app_icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Cierra la app si esta abierta al actualizar.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Limpia los archivos de la version anterior (PyInstaller cambia nombres entre versiones).
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; Actualizacion desde la app (instalador en modo /SILENT): volver a abrirla sola.
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: WizardSilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
// Al desinstalar: quitar el inicio automatico con Windows (la app lo crea en
// HKCU\...\Run\GoProSyncPro si se activo en Ajustes). La configuracion y la
// cache de miniaturas en %APPDATA%\GoProSyncPro se conservan.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'GoProSyncPro');
end;
