@echo off
setlocal
cd /d "%~dp0"
rem Builds a standalone RoyalRipper.exe that needs no Python on the target PC.
rem Sets up Python by itself if needed (see RoyalRipper.bat), and builds in a private
rem environment, so no Python on the PC is changed.
call "%~dp0RoyalRipper.bat" setup
if errorlevel 1 exit /b 1
set "PY="
if exist "bin\python\python.exe" set "PY=bin\python\python.exe"
if defined PY goto build
py -3 -c "import tkinter" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto build
set "PY=python"
:build
%PY% royalripper.py --build
if errorlevel 1 goto failed
echo.
echo Done: RoyalRipper.exe is in this folder. Keep it next to the bin folder.
pause
exit /b 0
:failed
echo.
echo Build failed - see the messages above.
pause
exit /b 1
