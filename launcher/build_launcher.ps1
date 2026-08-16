$ErrorActionPreference = "Stop"

$LauncherRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $LauncherRoot
$DistRoot = Join-Path $LauncherRoot "dist"
$BuildRoot = Join-Path $LauncherRoot "build"
$WorkRoot = Join-Path $BuildRoot "pyinstaller"
$SpecRoot = Join-Path $BuildRoot "spec"
# PyInstaller owns its work directory, so the payload is staged beside it.
$PayloadRoot = Join-Path $BuildRoot "servers"

foreach ($directory in @($DistRoot, $BuildRoot)) {
    if (Test-Path -LiteralPath $directory) {
        Remove-Item -LiteralPath $directory -Recurse -Force
    }
}
New-Item -ItemType Directory -Force -Path $DistRoot | Out-Null
New-Item -ItemType Directory -Force -Path $WorkRoot | Out-Null
New-Item -ItemType Directory -Force -Path $SpecRoot | Out-Null

python (Join-Path $LauncherRoot "stage_payload.py") `
    --output $PayloadRoot `
    --source $RepoRoot
if ($LASTEXITCODE -ne 0) {
    throw "Server payload staging failed with exit code $LASTEXITCODE"
}

foreach ($entry in @("0.8.2\lan_battle_server.py",
                     "0.9.22\server\windows_server.py")) {
    if (-not (Test-Path -LiteralPath (Join-Path $PayloadRoot $entry))) {
        throw "Server payload is incomplete: $entry"
    }
}

python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --noupx `
    --name "WoT-Offline-Battles-Launcher" `
    --distpath $DistRoot `
    --workpath $WorkRoot `
    --specpath $SpecRoot `
    --add-data "$PayloadRoot;servers" `
    (Join-Path $LauncherRoot "wot_launcher.py")

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

Copy-Item -Force `
    (Join-Path $LauncherRoot "LAUNCHER_README.txt") `
    (Join-Path $DistRoot "README.txt")

Write-Host "Built $DistRoot\WoT-Offline-Battles-Launcher.exe"
