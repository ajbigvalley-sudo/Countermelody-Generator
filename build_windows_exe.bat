@echo off
REM Build a standalone CountermelodyGenerator.exe with PyInstaller.
REM Run this on Windows. The executable lands in dist\.

title Build CountermelodyGenerator.exe
cd /d "%~dp0"

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    set "PY=py"
) else (
    set "PY=python"
)

%PY% --version >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo Python is not installed or not on PATH.
    echo Install Python 3.10+ from https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Installing build dependencies...
%PY% -m pip install --upgrade pyinstaller
%PY% -m pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo Dependency install failed.
    pause
    exit /b 1
)

echo.
echo Building executable (this takes a few minutes)...
%PY% -m PyInstaller ^
    --name "CountermelodyGenerator" ^
    --windowed ^
    --onefile ^
    --noconfirm ^
    --icon assets\icon.ico ^
    --add-data "assets;assets" ^
    --collect-all music21 ^
    --collect-submodules pygame ^
    gui.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Build failed. See messages above.
    pause
    exit /b 1
)

echo.
echo Done. Find the executable at:
echo     dist\CountermelodyGenerator.exe
echo.
pause
