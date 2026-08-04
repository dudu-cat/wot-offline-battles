@echo off
setlocal

if "%~1"=="" goto usage

set "GAME_ROOT=%~f1"
set "MOD_ROOT=%GAME_ROOT%\res_mods\0.8.2"
set "MODS_DIR=%MOD_ROOT%\scripts\client\gui\mods"

if not exist "%GAME_ROOT%\WorldOfTanks.exe" (
    echo WorldOfTanks.exe was not found in:
    echo   %GAME_ROOT%
    exit /b 2
)

echo Copying client mod files...
xcopy "%~dp0scripts" "%MOD_ROOT%\scripts\" /E /I /Y /Q >nul
if errorlevel 2 goto copy_failed
xcopy "%~dp0gui" "%MOD_ROOT%\gui\" /E /I /Y /Q >nul
if errorlevel 2 goto copy_failed

rem The 0.8.2 CameraNode loader scans .pyc before .py. An old entry bytecode
rem file therefore masks the updated source and prevents the LAN UI from loading.
del /F /Q "%MODS_DIR%\mod_offhangar.pyc" >nul 2>&1

echo Client mod refreshed successfully.
echo Start the game and look for LAN SETTINGS in the upper-right of the hangar.
echo If it is still missing, search python.log for:
echo   Offline Battles source loader active
exit /b 0

:copy_failed
echo Failed to copy the client mod files.
exit /b 1

:usage
echo Usage:
echo   refresh_client.bat "C:\Games\World_of_Tanks_0.8.2"
exit /b 2
