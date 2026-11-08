<#
  Publica una nueva version de GoPro Sync.

  Uso:
    .\release.ps1 1.1.0

  Que hace:
    1. Comprueba que estas en "main", sin cambios pendientes y al dia con GitHub.
    2. Cambia APP_VERSION en app\main.py, hace commit "Release v1.1.0" y crea el tag v1.1.0.
    3. Sube main y el tag. GitHub Actions compila y publica el Release con el
       instalador y el ZIP portable (tarda ~5-10 min).
#>
param(
    [Parameter(Mandatory = $true)][string]$Version
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Run($cmd) {
    Write-Host "> $cmd" -ForegroundColor DarkGray
    Invoke-Expression $cmd
    if ($LASTEXITCODE -ne 0) { throw "Fallo: $cmd" }
}

$Version = $Version.TrimStart("v")
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "Version invalida '$Version'. Formato: 1.2.3" }
$tag = "v$Version"

$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($branch -ne "main") { throw "Estas en '$branch'. Cambia a main antes de publicar." }
if (git status --porcelain) { throw "Hay cambios sin commitear. Commitea o descarta antes de publicar." }

Run "git fetch origin --tags --quiet"
if (git tag -l $tag) { throw "El tag $tag ya existe." }
$behind = (git rev-list --count "HEAD..origin/main").Trim()
if ([int]$behind -gt 0) { throw "Tu main esta $behind commit(s) atras de origin/main. Haz 'git pull' primero." }

$mainPy = Join-Path $PSScriptRoot "app\main.py"
$content = [System.IO.File]::ReadAllText($mainPy)
$new = [regex]::Replace($content, '(?m)^APP_VERSION\s*=\s*"[^"]*"', "APP_VERSION = `"$Version`"")
if ($new -eq $content) {
    Write-Host "APP_VERSION ya es $Version."
} else {
    [System.IO.File]::WriteAllText($mainPy, $new, (New-Object System.Text.UTF8Encoding($false)))
    Run "git add app/main.py"
    Run "git commit -m `"Release $tag`""
}

Run "git tag -a $tag -m `"GoPro Sync $tag`""
Run "git push origin main"
Run "git push origin $tag"

$repo = (git remote get-url origin) -replace '\.git$', '' -replace '^git@github\.com:', 'https://github.com/'
Write-Host ""
Write-Host "Listo. GitHub Actions esta compilando ${tag}:" -ForegroundColor Green
Write-Host "  $repo/actions"
Write-Host "  El release aparecera en: $repo/releases/tag/$tag"
