@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if not errorlevel 1 goto run_py

where python >nul 2>&1
if not errorlevel 1 goto run_python

echo Python 3 was not found.
echo Download it from https://www.python.org/downloads/windows/
echo During installation, enable "Add python.exe to PATH".
goto done

:run_py
echo Starting the World of Tanks 0.8.2 LAN server on TCP port 28782...
py -3 lan_battle_server.py --host 0.0.0.0 --port 28782
goto done

:run_python
python -c "import sys; raise SystemExit(sys.version_info[0] - 3)" >nul 2>&1
if errorlevel 1 goto wrong_python
echo Starting the World of Tanks 0.8.2 LAN server on TCP port 28782...
python lan_battle_server.py --host 0.0.0.0 --port 28782
goto done

:wrong_python
echo The 'python' command is not Python 3.
echo Install Python 3 from https://www.python.org/downloads/windows/

:done
echo.
echo The server has stopped. Review any message above before closing this window.
pause
