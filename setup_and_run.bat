@echo off
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
  set "PY=py -3"
) else (
  set "PY=python"
)
if not exist ".venv\Scripts\python.exe" (
  echo Creating the Python environment...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo Python was not found. Install Python 3.10 or newer and check Add python.exe to PATH.
    pause
    exit /b 1
  )
)
call ".venv\Scripts\activate.bat"
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo The package install failed.
  pause
  exit /b 1
)
python gui.py
if errorlevel 1 pause
