# Builds the VMI install wizard: downloads the bundled Python installer
# (cached under vendor\ after the first run) and compiles vmi_installer.iss
# with Inno Setup. Output lands in installer\Output\VMI-Update-Process-Setup.exe.
#
# Requires Inno Setup 6 (installed this session via:
#   winget install --id JRSoftware.InnoSetup -e
# which places ISCC.exe under %LOCALAPPDATA%\Programs\Inno Setup 6\).

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$vendorDir = Join-Path $root 'vendor'
$pythonInstaller = Join-Path $vendorDir 'python-3.12.7-amd64.exe'
$pythonUrl = 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe'

if (-not (Test-Path $vendorDir)) {
    New-Item -ItemType Directory -Path $vendorDir | Out-Null
}

if (-not (Test-Path $pythonInstaller)) {
    Write-Host "Downloading Python installer to $pythonInstaller ..."
    Invoke-WebRequest -Uri $pythonUrl -OutFile $pythonInstaller
} else {
    Write-Host "Using cached Python installer at $pythonInstaller"
}

$iscc = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
if (-not (Test-Path $iscc)) {
    throw "ISCC.exe not found at $iscc -- install Inno Setup 6 first (winget install --id JRSoftware.InnoSetup -e)."
}

& $iscc (Join-Path $root 'vmi_installer.iss')
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compile failed with exit code $LASTEXITCODE"
}

Write-Host "Installer built: $root\Output\VMI-Update-Process-Setup.exe"
