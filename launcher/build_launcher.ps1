$ErrorActionPreference = "Stop"

$LauncherRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $LauncherRoot
$DistRoot = Join-Path $LauncherRoot "dist"
$BuildRoot = Join-Path $LauncherRoot "build"
$WorkRoot = Join-Path $BuildRoot "pyinstaller"
$SpecRoot = Join-Path $BuildRoot "spec"
# PyInstaller owns its work directory, so the payload is staged beside it.
$PayloadRoot = Join-Path $BuildRoot "payload"
$AppName = "WoT-Offline-Battles-Launcher"

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
    throw "Payload staging failed with exit code $LASTEXITCODE"
}

foreach ($entry in @("servers\0.8.2\lan_battle_server.py",
                     "servers\0.9.22\server\windows_server.py",
                     "client\0.8.2\scripts",
                     "client\0.8.2\gui",
                     "client\0.9.22\mods")) {
    if (-not (Test-Path -LiteralPath (Join-Path $PayloadRoot $entry))) {
        throw "Payload is incomplete: $entry"
    }
}

# A one-folder build keeps the 65 MB payload on disk instead of extracting it
# on every launch and on every server start.
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --noupx `
    --name $AppName `
    --distpath $DistRoot `
    --workpath $WorkRoot `
    --specpath $SpecRoot `
    --add-data "$PayloadRoot\servers;servers" `
    --add-data "$PayloadRoot\client;client" `
    (Join-Path $LauncherRoot "wot_launcher.py")

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

Copy-Item -Force `
    (Join-Path $LauncherRoot "LAUNCHER_README.txt") `
    (Join-Path $DistRoot "$AppName\README.txt")

Compress-Archive -Force `
    -Path (Join-Path $DistRoot $AppName) `
    -DestinationPath (Join-Path $DistRoot "$AppName-Windows.zip")

Write-Host "Built $DistRoot\$AppName\$AppName.exe"
