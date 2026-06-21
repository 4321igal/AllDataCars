@echo off
REM ===================================================================
REM  AllDataCars - one-click setup & launch for Windows.
REM  Double-click this file, or run:  start.bat
REM  It creates the virtual env, installs dependencies, asks for your
REM  Telegram token (once), runs the preflight, then starts the bot.
REM  Uses the venv's python.exe directly, so no PowerShell activation
REM  / execution-policy issues.
REM ===================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM --- pick a Python launcher (prefer the 'py' launcher on Windows) ---
where py >nul 2>&1
if %errorlevel%==0 (set "PY=py") else (set "PY=python")

REM --- create the virtual environment if it does not exist ---
if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Creating virtual environment .venv ...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo.
    echo ERROR: could not create the virtual environment.
    echo Make sure Python 3.10+ is installed and on PATH ^(python --version^).
    pause
    exit /b 1
  )
) else (
  echo [1/4] Virtual environment already exists.
)

set "VENV_PY=.venv\Scripts\python.exe"

REM --- install dependencies ---
echo [2/4] Installing dependencies ...
"%VENV_PY%" -m pip install --upgrade pip >nul 2>&1
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo ERROR: dependency installation failed. Scroll up for details.
  pause
  exit /b 1
)

REM --- make sure .env exists and has a token ---
if not exist ".env" (
  echo [3/4] Creating .env from template ...
  copy .env.example .env >nul
  echo.
  echo  ============================================================
  echo   Notepad will open .env now.
  echo   Paste your Telegram token after  TELEGRAM_BOT_TOKEN=
  echo   then SAVE ^(Ctrl+S^) and CLOSE Notepad to continue.
  echo  ============================================================
  echo.
  notepad .env
) else (
  echo [3/4] .env already exists.
)

REM --- preflight + run ---
echo [4/4] Running preflight checks ...
echo.
"%VENV_PY%" doctor.py
echo.
echo Starting the bot. Keep this window open. Press Ctrl+C to stop.
echo.
"%VENV_PY%" bot.py

echo.
echo Bot stopped.
pause
endlocal
