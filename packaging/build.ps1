<#
  Compila GoPro Sync para Windows:
    1. PyInstaller  -> dist\GoProSync\GoProSync.exe (carpeta, arranque rapido)
    2. ZIP portable -> dist\GoProSync-<version>-portable.zip
    3. Inno Setup   -> dist\GoProSync-Setup-<version>.exe  (si ISCC esta instalado)

  Uso (desde la raiz del repo):
    powershell -ExecutionPolicy Bypass -File packaging\build.ps1
    powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -SkipInstaller
    powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Python C:\ruta\python.exe

  La version se lee de APP_VERSION en app\main.py (la fija release.ps1).
#>
param(
    [string]$Python = "python",
    [switch]$SkipInstaller,
    [switch]$SkipDeps
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$mainPy = Join-Path $Root "app\main.py"
$m = Select-String -Path $mainPy -Pattern '^APP_VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $m) { throw "No se encontro APP_VERSION en app\main.py" }
$Version = $m.Matches[0].Groups[1].Value
Write-Host "== GoPro Sync $Version ==" -ForegroundColor Cyan

if (-not $SkipDeps) {
    Write-Host "-> Instalando dependencias de build"
    & $Python -m pip install --disable-pip-version-check -q -r app\requirements.txt pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "pip install fallo" }
}

Write-Host "-> PyInstaller"
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
& $Python -m PyInstaller --noconfirm --clean --windowed `
    --name GoProSync `
    --icon "app\assets\app_icon.ico" `
    --add-data "app\assets;assets" `
    --exclude-module tkinter `
    --exclude-module unittest `
    --exclude-module pydoc `
    --distpath dist --workpath build `
    app\main.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller fallo" }
if (-not (Test-Path "dist\GoProSync\GoProSync.exe")) { throw "No se genero dist\GoProSync\GoProSync.exe" }

Write-Host "-> ZIP portable"
$zip = "dist\GoProSync-$Version-portable.zip"
Compress-Archive -Path "dist\GoProSync\*" -DestinationPath $zip -Force

if (-not $SkipInstaller) {
    $iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
    if (-not $iscc) {
        foreach ($c in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
                         "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")) {
            if (Test-Path $c) { $iscc = $c; break }
        }
    }
    if (-not $iscc) {
        Write-Warning "Inno Setup (ISCC.exe) no esta instalado: se omite el instalador. Instalalo con: winget install JRSoftware.InnoSetup"
    } else {
        Write-Host "-> Inno Setup"
        & $iscc /Qp "/DAppVersion=$Version" "/DSourceDir=$Root\dist\GoProSync" "/O$Root\dist" "packaging\GoProSync.iss"
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup fallo" }
    }
}

Write-Host ""
Write-Host "Listo. Archivos en dist\:" -ForegroundColor Green
Get-ChildItem dist -File | ForEach-Object { "  {0}  ({1:N1} MB)" -f $_.Name, ($_.Length / 1MB) }
