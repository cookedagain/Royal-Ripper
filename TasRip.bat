@echo off
setlocal
cd /d "%~dp0"
rem TasRip launcher. The first run sets everything up by itself:
rem  - uses this PC's Python if it has one with Tk, otherwise downloads a portable Python
rem    into bin\python (nothing gets installed on the PC, so it travels on a USB stick)
rem  - the app then downloads yt-dlp, ffmpeg and the other tools into bin
rem "TasRip.bat setup" only does the Python part (build_exe.bat uses that).

if /i "%~1"=="setup" goto setup
rem Old Tas Rip Pack scripts expect an "Extractors" folder: point it at bin (no admin needed).
if not exist "Extractors" mklink /J "Extractors" "bin" >nul 2>nul
for %%e in (yt-dlp-iView-beta.exe yt-dlp-modified.exe) do (
  if exist "bin\engines\%%e" if not exist "bin\%%e" mklink /H "bin\%%e" "bin\engines\%%e" >nul 2>nul
)
if not exist "TasRip.exe" goto runpy
if not exist "tasrip.py" goto runexe

rem A TasRip.exe built from an older tasrip.py would keep running the old version after an
rem update, so compare the two and rebuild the exe once when they differ.
set "PYVER="
set "EXEVER="
for /f "tokens=3" %%v in ('findstr /b /c:"VERSION = " "tasrip.py"') do set "PYVER=%%~v"
if exist "bin\.exe-version" set /p EXEVER=<"bin\.exe-version"
if "%PYVER%"=="%EXEVER%" goto runexe
echo.
echo TasRip was updated to v%PYVER% - rebuilding TasRip.exe to match.
echo This happens once and takes a few minutes. Please wait ...
echo.
call :findpython
if not defined PY call :getpython
if not defined PY goto nopython
%PY% tasrip.py --build
if errorlevel 1 goto rebuild_failed
:runexe
start "" "TasRip.exe"
exit /b 0

:rebuild_failed
echo.
echo Couldn't rebuild TasRip.exe - starting v%PYVER% without it this time.
timeout /t 5 >nul
goto runpy_found

:runpy
call :findpython
if not defined PYW call :getpython
if not defined PYW goto nopython
:runpy_found
start "" %PYW% "tasrip.py"
exit /b 0

:setup
call :findpython
if not defined PY call :getpython
if not defined PY goto nopython
exit /b 0

:nopython
echo.
echo Couldn't set up Python automatically. Check the internet connection and run this again,
echo or install Python from https://www.python.org/downloads/ (keep "tcl/tk" ticked).
pause
exit /b 1

:findpython
set "PY="
set "PYW="
if exist "bin\python\python.exe" goto usebundled
where py >nul 2>nul
if errorlevel 1 goto trypython
py -3 -c "import tkinter" >nul 2>nul
if errorlevel 1 goto trypython
set "PY=py -3"
set "PYW=pyw -3"
exit /b 0
:trypython
where python >nul 2>nul
if errorlevel 1 exit /b 0
python -c "import tkinter" >nul 2>nul
if errorlevel 1 exit /b 0
set "PY=python"
set "PYW=pythonw"
exit /b 0
:usebundled
set "PY=bin\python\python.exe"
set "PYW=bin\python\pythonw.exe"
if not exist "bin\python\pythonw.exe" set "PYW=bin\python\python.exe"
exit /b 0

:getpython
echo First run: downloading a portable Python into bin\python (about 30 MB) ...
if not exist "bin" mkdir "bin"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $h=@{'User-Agent'='TasRip'}; $r=Invoke-RestMethod -Uri 'https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest' -Headers $h; $a=$r.assets | Where-Object { $_.name -match '^cpython-3\.12\.\d+\+\d+-x86_64-pc-windows-msvc-install_only\.tar\.gz$' } | Select-Object -First 1; if (-not $a) { throw 'no Windows build in the latest release' }; Write-Host ('  ' + $a.name); Invoke-WebRequest -Uri $a.browser_download_url -OutFile 'bin\python.tar.gz' -UseBasicParsing -Headers $h"
if errorlevel 1 goto getpython_fail
tar -xzf "bin\python.tar.gz" -C "bin"
if errorlevel 1 goto getpython_fail
del "bin\python.tar.gz" >nul 2>nul
"bin\python\python.exe" -c "import tkinter" >nul 2>nul
if errorlevel 1 goto getpython_fail
echo Python is ready.
goto usebundled
:getpython_fail
del "bin\python.tar.gz" >nul 2>nul
echo Downloading Python failed.
exit /b 1
