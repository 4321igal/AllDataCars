@echo off
REM Launch AllDataCars: activate venv if present, run preflight, then start the bot.
cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
  call ".venv\Scripts\activate.bat"
)

python doctor.py
if errorlevel 1 (
  echo.
  echo Preflight failed. Fix the items above and try again.
  exit /b 1
)

echo.
python bot.py
