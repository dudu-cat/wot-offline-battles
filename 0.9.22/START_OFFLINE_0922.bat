@echo off
setlocal

set "GAME_ROOT=%~dp0"
if not exist "%GAME_ROOT%WorldOfTanks.exe" (
    echo Extract this file and the mods folder into the directory that contains WorldOfTanks.exe.
    pause
    exit /b 2
)

if "%LOCALAPPDATA%"=="" (
    echo LOCALAPPDATA is unavailable; the normal client preferences were not touched.
    pause
    exit /b 2
)

rem Keep this legacy client's preferences away from the normal WoT profile.
set "APPDATA=%LOCALAPPDATA%\WoTOfflineBattles\client_profiles\0.9.22\Roaming"
if not exist "%APPDATA%" mkdir "%APPDATA%"
if errorlevel 1 (
    echo The isolated preferences directory could not be created.
    pause
    exit /b 2
)

pushd "%GAME_ROOT%"
WorldOfTanks.exe
set "GAME_EXIT=%ERRORLEVEL%"
popd

endlocal & exit /b %GAME_EXIT%
