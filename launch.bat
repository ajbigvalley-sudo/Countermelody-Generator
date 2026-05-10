@echo off
REM Countermelody Generator - Windows launcher.
REM Double-click to open. First run installs dependencies; later runs are instant.

title Countermelody Generator
cd /d "%~dp0"

REM Prefer the Python launcher (py / pyw); fall back to python / pythonw on PATH.
where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    set "PY=py"
    set "PYW=pyw"
) else (
    set "PY=python"
    set "PYW=pythonw"
)

REM Verify Python is reachable.
%PY% --version >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo Python is not installed or not on PATH.
    echo Install Python 3.10+ from https://www.python.org/downloads/
    echo and tick "Add Python to PATH" during setup.
    pause
    exit /b 1
)

REM Install dependencies if music21 or pygame is missing.
%PY% -c "import music21, pygame" >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo First-run setup: installing dependencies. This may take a minute...
    %PY% -m pip install -r requirements.txt
    if %ERRORLEVEL% NEQ 0 (
        echo.
        echo Dependency install failed. See messages above.
        pause
        exit /b 1
    )
)

REM Launch the GUI without a lingering console window.
start "" %PYW% gui.py
exit /b 0
