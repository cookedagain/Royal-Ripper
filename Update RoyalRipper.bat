@echo off
setlocal
cd /d "%~dp0"
title Royal Ripper updater
rem Run this after unzipping a new Royal Ripper over this folder (say Yes to replacing files).
rem It keeps your settings, archive, watch list, history, logs and site engines, then:
rem  - tidies away the old TasRip and MediaRipper files (Royal Ripper's old names)
rem  - downloads any missing tools and updates yt-dlp
rem  - rebuilds RoyalRipper.exe if you were using the exe
rem "Update RoyalRipper.bat full" also re-downloads ffmpeg, N_m3u8DL-RE, aria2c and Streamlink.

echo.
echo  Royal Ripper updater
echo  ====================
echo.
tasklist /fi "imagename eq RoyalRipper.exe" 2>nul | find /i "RoyalRipper.exe" >nul
if not errorlevel 1 goto running
tasklist /fi "imagename eq TasRip.exe" 2>nul | find /i "TasRip.exe" >nul
if not errorlevel 1 goto running
tasklist /fi "imagename eq MediaRipper.exe" 2>nul | find /i "MediaRipper.exe" >nul
if not errorlevel 1 goto running

set "REBUILD="
if exist "RoyalRipper.exe" set "REBUILD=1"
if exist "TasRip.exe" set "REBUILD=1"
if exist "MediaRipper.exe" set "REBUILD=1"
if exist "TasRip.exe" del /q "TasRip.exe"
if exist "tasrip.py" del /q "tasrip.py"
if exist "TasRip.bat" del /q "TasRip.bat"
if exist "Update TasRip.bat" del /q "Update TasRip.bat"
if exist "MediaRipper.exe" del /q "MediaRipper.exe"
if exist "mediaripper.py" del /q "mediaripper.py"
if exist "MediaRipper.bat" del /q "MediaRipper.bat"
if exist "__pycache__" rmdir /s /q "__pycache__"

call "%~dp0RoyalRipper.bat" setup
if errorlevel 1 goto failed
set "PY="
if exist "bin\python\python.exe" set "PY=bin\python\python.exe"
if defined PY goto update
py -3 -c "import tkinter" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto update
set "PY=python"

:update
echo Updating tools ...
if /i "%~1"=="full" (
  %PY% royalripper.py --update --full
) else (
  %PY% royalripper.py --update
)
if errorlevel 1 goto failed

if not defined REBUILD goto finish
echo.
echo Rebuilding RoyalRipper.exe (a few minutes) ...
if exist "RoyalRipper.exe" del /q "RoyalRipper.exe"
%PY% royalripper.py --build
if errorlevel 1 goto failed

:finish
echo.
echo All up to date. Starting Royal Ripper ...
timeout /t 2 >nul
call "%~dp0RoyalRipper.bat"
exit /b 0

:running
echo Royal Ripper is still open. Close it, then run this again.
pause
exit /b 1

:failed
echo.
echo Something went wrong - see the messages above. Your settings and downloads are untouched.
pause
exit /b 1
