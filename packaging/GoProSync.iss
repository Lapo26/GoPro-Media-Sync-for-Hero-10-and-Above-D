; Instalador de GoPro Sync (Inno Setup 6)
; Lo compila packaging\build.ps1, que pasa AppVersion y SourceDir.
;
; Asistente estandar de Windows:
;   1. Instalar solo para mi / para todos los usuarios
;   2. Carpeta de instalacion
;   3. Carpeta del menu Inicio (con la casilla "No crear una carpeta en el menu Inicio")
;   4. Tareas: icono en el escritorio, iniciar con Windows
;   5. Al terminar: casilla "Ejecutar GoPro Sync"
; Queda en Configuracion > Aplicaciones con su desinstalador, que tambien esta
; en la carpeta del menu Inicio.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\GoProSync"
#endif

#define AppName "GoPro Sync"
#define AppExe "GoProSync.exe"
#define AppUrl "https://github.com/Lapo26/GoPro-Sync"

[Setup]
; AppId fijo: identifica la app para actualizar/desinstalar. NO cambiarlo.
AppId={{6F2B4C1E-3A5D-4E8B-9C21-7D4A0E5F9B13}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Lapo26
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription=Instalador de {#AppName}
DefaultDirName={autopf}\{#AppName}
; Pagina "Seleccionar carpeta del menu Inicio", con la opcion de no crearla
DefaultGroupName={#AppName}
DisableProgramGroupPage=no
AllowNoIcons=yes
; Por defecto se instala solo para el usuario actual (sin pedir administrador);
; el asistente ofrece instalar para todos los usuarios.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=GoProSync-Setup-{#AppVersion}
SetupIconFile=..\app\assets\app_icon.ico
; Entrada en Configuracion > Aplicaciones
UninstallDisplayName={#AppName}
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
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "autostart"; Description: "{cm:AutoStartProgram,{#AppName}}"; GroupDescription: "{cm:AutoStartProgramGroupDescription}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Limpia los archivos de la version anterior (PyInstaller cambia nombres entre versiones).
Type: filesandordirs; Name: "{app}\_internal"
; Acceso suelto que dejaron pruebas anteriores (ahora va dentro de la carpeta del menu Inicio).
Type: files; Name: "{autoprograms}\{#AppName}.lnk"

[Icons]
; Carpeta del menu Inicio: la app y su desinstalador (se omiten si se marca
; "No crear una carpeta en el menu Inicio").
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Sincroniza y respalda tu GoPro"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Iniciar con Windows (solo si se marco la tarea): mismo valor que usa la app
; desde Ajustes, para que ambos queden de acuerdo. Se borra al desinstalar.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "GoProSyncPro"; ValueData: """{app}\{#AppExe}"" --silent"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; Actualizacion desde la app (instalador en modo /SILENT): volver a abrirla sola.
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: WizardSilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
// Al desinstalar: quitar el inicio automatico con Windows aunque se haya
// activado desde Ajustes de la app (HKCU\...\Run\GoProSyncPro). La
// configuracion y la cache de miniaturas del usuario se conservan.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'GoProSyncPro');
end;
