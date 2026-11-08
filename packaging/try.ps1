<#
  Prueba la version actual del codigo en tu PC SIN publicar nada.

  Uso (desde la raiz del repo):
    .\packaging\try.ps1              # compila y abre la app compilada
    .\packaging\try.ps1 -FromSource  # abre directo desde el codigo (mas rapido, sin compilar)
    .\packaging\try.ps1 -Fresh       # con un perfil limpio temporal (como una instalacion nueva:
                                     #   asistente, idioma...). No toca tu configuracion real.

  Por defecto usa TU configuracion real (%APPDATA%\GoProSyncPro), asi pruebas con
  tu carpeta y tu camara. Cierra la GoPro Sync instalada mientras pruebas, para
  que no haya dos copias a la vez.

  La primera vez crea un entorno .venv en el repo con las dependencias (unos minutos).
#>
param(
    [switch]$FromSource,
    [switch]$Fresh,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# --- entorno de desarrollo (.venv) ---
if (-not $Python) {
    $venvPy = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPy)) {
        Write-Host "-> Creando entorno .venv (solo la primera vez)" -ForegroundColor Cyan
        & python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "No se pudo crear .venv (hace falta Python 3.10+)" }
    }
    $Python = $venvPy
    & $Python -m pip install --disable-pip-version-check -q -r app\requirements.txt -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install fallo" }
}

# --- cerrar la version instalada si esta abierta ---
$installed = Join-Path $env:LOCALAPPDATA "Programs\GoPro Sync\GoProSync.exe"
Get-Process GoProSync -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $installed } | ForEach-Object {
    Write-Host "-> Cerrando la GoPro Sync instalada mientras pruebas" -ForegroundColor Yellow
    Stop-Process -Id $_.Id -Force
}

# --- perfil limpio opcional ---
if ($Fresh) {
    $profileDir = Join-Path $env:TEMP ("GoProSync-prueba-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    New-Item -ItemType Directory -Force $profileDir | Out-Null
    $env:APPDATA = $profileDir
    Write-Host "-> Perfil limpio temporal: $profileDir" -ForegroundColor Yellow
}

if ($FromSource) {
    Write-Host "-> Abriendo desde el codigo (app\main.py)" -ForegroundColor Green
    & $Python app\main.py
    exit $LASTEXITCODE
}

Write-Host "-> Compilando (sin instalador)" -ForegroundColor Cyan
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "build.ps1") -Python $Python -SkipInstaller -SkipDeps
if ($LASTEXITCODE -ne 0) { throw "Fallo la compilacion" }

$exe = Join-Path $Root "dist\GoProSync\GoProSync.exe"
Write-Host "-> Abriendo version de prueba: $exe" -ForegroundColor Green
Start-Process $exe
Write-Host ""
Write-Host "Cuando termines de probar, cierra la app. Si todo esta bien: .\release.ps1 X.Y.Z" -ForegroundColor Green
