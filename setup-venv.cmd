@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem Pip wheel cache on D: (same defaults as scripts\dev-cache-env.ps1)
set "DEV_CACHE=D:\dev"
if not exist "%DEV_CACHE%\pip-cache" mkdir "%DEV_CACHE%\pip-cache" 2>nul
if not defined PIP_CACHE_DIR set "PIP_CACHE_DIR=%DEV_CACHE%\pip-cache"

set "VENV_PY=%~dp0backend\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
  echo Creating virtual environment at backend\.venv ...
  python -m venv "backend\.venv"
)
if not exist "%VENV_PY%" (
  echo Failed to create venv. Try: py -3.11 -m venv backend\.venv
  exit /b 1
)

echo Upgrading pip...
"%VENV_PY%" -m pip install --upgrade pip >nul

set "REQ=%~dp0requirements.txt"
if not exist "%REQ%" set "REQ=%~dp0..\requirements.txt"
if not exist "%REQ%" (
  echo ERROR: requirements.txt not found in xm1 or parent folder.
  exit /b 1
)
echo Installing requirements from "%REQ%" ...
"%VENV_PY%" -m pip install -r "%REQ%"
if errorlevel 1 exit /b 1

echo.
echo Done. Run backend: backend\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
echo Or use: start.ps1  ^(may require: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned^)
exit /b 0
