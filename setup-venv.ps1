$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RootDir

$VenvPath = Join-Path $RootDir "backend/.venv"
$VenvPython = Join-Path $RootDir "backend/.venv/Scripts/python.exe"

if (-not (Test-Path $VenvPython)) {
  Write-Host "Creating virtual environment at backend/.venv ..."
  & python -m venv $VenvPath
  if (-not (Test-Path $VenvPython)) {
    throw "Failed to create venv. Try: py -3.11 -m venv backend/.venv"
  }
} else {
  Write-Host "Virtual environment already exists at backend/.venv"
}

Write-Host "Upgrading pip..."
& $VenvPython -m pip install --upgrade pip | Out-Null

Write-Host "Installing requirements from requirements.txt ..."
& $VenvPython -m pip install -r "requirements.txt"

Write-Host "Done. Run backend with: .\backend\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000"
Write-Host "Or use: .\start.ps1"
