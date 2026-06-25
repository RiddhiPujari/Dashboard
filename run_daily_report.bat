@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Run: py -m venv .venv
  pause
  exit /b 1
)
".venv\Scripts\python.exe" generate_daily_report.py --input-dir data\incoming --output-dir reports
pause
