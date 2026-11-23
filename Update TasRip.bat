@echo off
setlocal
cd /d "%~dp0"
title TasRip updater
rem Run this after unzipping a new TasRip over this folder (say Yes to replacing files).
rem It keeps your settings, archive, watch list, history, logs and site engines, then:
rem  - tidies away old MediaRipper files (TasRip's old name)
rem  - downloads any missing tools and updates yt-dlp
rem  - rebuilds TasRip.exe if you were using the exe
rem "Update TasRip.bat full" also re-downloads ffmpeg, N_m3u8DL-RE, aria2c and Streamlink.

echo.
echo  TasRip updater
echo  ==============
echo.
tasklist /fi "imagename eq TasRip.exe" 2>nul | find /i "TasRip.exe" >nul
if not errorlevel 1 goto running
tasklist /fi "imagename eq MediaRipper.exe" 2>nul | find /i "MediaRipper.exe" >nul
if not errorlevel 1 goto running

set "REBUILD="
if exist "TasRip.exe" set "REBUILD=1"
if exist "MediaRipper.exe" set "REBUILD=1"
if exist "MediaRipper.exe" del /q "MediaRipper.exe"
if exist "mediaripper.py" del /q "mediaripper.py"
if exist "MediaRipper.bat" del /q "MediaRipper.bat"
if exist "__pycache__" rmdir /s /q "__pycache__"

call "%~dp0TasRip.bat" setup
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
  %PY% tasrip.py --update --full
) else (
  %PY% tasrip.py --update
)
if errorlevel 1 goto failed

if not defined REBUILD goto finish
echo.
echo Rebuilding TasRip.exe (a few minutes) ...
if exist "TasRip.exe" del /q "TasRip.exe"
%PY% tasrip.py --build
if errorlevel 1 goto failed

:finish
echo.
echo All up to date. Starting TasRip ...
timeout /t 2 >nul
call "%~dp0TasRip.bat"
exit /b 0

:running
echo TasRip is still open. Close it, then run this again.
pause
exit /b 1

:failed
echo.
echo Something went wrong - see the messages above. Your settings and downloads are untouched.
pause
exit /b 1
