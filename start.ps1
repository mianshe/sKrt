$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvScript = Join-Path $RootDir "scripts\dev-cache-env.ps1"
if (Test-Path $EnvScript) {
    . $EnvScript
}
Set-Location $RootDir

$VenvPython = Join-Path $RootDir "backend/.venv/Scripts/python.exe"
if (-not (Test-Path $VenvPython)) {
  throw "backend/.venv not found. Create with: py -3.11 -m venv backend/.venv"
}

$ReqFile = Join-Path $RootDir "requirements.txt"
if (-not (Test-Path $ReqFile)) {
  $ReqFile = Join-Path $RootDir "..\requirements.txt"
}
if (-not (Test-Path $ReqFile)) {
  throw "requirements.txt not found (tried $RootDir and parent directory)."
}
$ReqFile = (Resolve-Path $ReqFile).Path

Write-Host '[1/4] Installing backend dependencies...'
& $VenvPython -m pip install --upgrade pip | Out-Null
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed (exit $LASTEXITCODE)" }
& $VenvPython -m pip install -r $ReqFile | Out-Null
if ($LASTEXITCODE -ne 0) { throw "pip install -r failed (exit $LASTEXITCODE): $ReqFile" }

$FrontendDir = Join-Path $RootDir "frontend"
$FrontendPkg = Join-Path $FrontendDir "package.json"
if (-not (Test-Path $FrontendPkg)) {
  throw "frontend/package.json not found: $FrontendDir"
}

Write-Host '[2/4] Installing frontend dependencies...'
Push-Location $FrontendDir
try {
  npm install | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "npm install failed (exit $LASTEXITCODE)" }
} finally {
  Pop-Location
}

Write-Host '[3/4] Starting backend on :8000 ...'
$backendProc = Start-Process -FilePath $VenvPython -WorkingDirectory $RootDir -ArgumentList @("-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000") -PassThru

try {
  Write-Host '[4/4] Starting frontend on :5173 ...'
  Push-Location $FrontendDir
  try {
    npm run dev -- --host 0.0.0.0 --port 5173
  } finally {
    Pop-Location
  }
}
finally {
  if ($backendProc -and -not $backendProc.HasExited) {
    Write-Host 'Shutting down backend...'
    Stop-Process -Id $backendProc.Id -Force -ErrorAction SilentlyContinue
  }
}
