$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvScript = Join-Path $RootDir "scripts\dev-cache-env.ps1"
if (Test-Path $EnvScript) {
    . $EnvScript
}
Set-Location $RootDir

$AppModule = if ($env:APP_MODULE) { $env:APP_MODULE } else { "backend.main:app" }
$BackendHost = if ($env:BACKEND_HOST) { $env:BACKEND_HOST } else { "0.0.0.0" }
$BackendPort = if ($env:BACKEND_PORT) { $env:BACKEND_PORT } else { "8000" }
$InstallDeps = if ($env:INSTALL_DEPS) { $env:INSTALL_DEPS } else { "0" }
$BuildFrontend = if ($env:BUILD_FRONTEND) { $env:BUILD_FRONTEND } else { "0" }
$NpmBin = if ($env:NPM_BIN) { $env:NPM_BIN } else { "npm" }

$VenvCandidates = @(
    (Join-Path $RootDir "backend/.venv/Scripts/python.exe"),
    (Join-Path $RootDir "backend/.venv/bin/python")
)
$VenvPython = $VenvCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $VenvPython) {
    throw "backend/.venv not found. Create it first with Python 3.11."
}

$FrontendDir = Join-Path $RootDir "frontend"
$FrontendPkg = Join-Path $FrontendDir "package.json"

if ($InstallDeps -eq "1") {
    $ReqFile = Join-Path $RootDir "requirements.txt"
    if (-not (Test-Path $ReqFile)) {
        throw "requirements.txt not found: $ReqFile"
    }

    Write-Host "[setup] Installing backend dependencies..."
    & $VenvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed (exit $LASTEXITCODE)" }
    & $VenvPython -m pip install -r $ReqFile
    if ($LASTEXITCODE -ne 0) { throw "pip install -r failed (exit $LASTEXITCODE): $ReqFile" }

    if (Test-Path $FrontendPkg) {
        Write-Host "[setup] Installing frontend dependencies..."
        & $NpmBin --prefix $FrontendDir ci
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed (exit $LASTEXITCODE)" }
    }
}

if ($BuildFrontend -eq "1") {
    if (-not (Test-Path $FrontendPkg)) {
        throw "frontend/package.json not found: $FrontendDir"
    }
    Write-Host "[build] Building frontend..."
    & $NpmBin --prefix $FrontendDir run build
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed (exit $LASTEXITCODE)" }
}

$UvicornArgs = @("-m", "uvicorn", $AppModule, "--host", $BackendHost, "--port", $BackendPort)
$EnvFile = Join-Path $RootDir ".env"
if (Test-Path $EnvFile) {
    $UvicornArgs += @("--env-file", $EnvFile)
}
if ($env:UVICORN_EXTRA_ARGS) {
    $UvicornArgs += ($env:UVICORN_EXTRA_ARGS -split '\s+' | Where-Object { $_ })
}

Write-Host "[start] Working directory: $RootDir"
Write-Host "[start] Python: $VenvPython"
Write-Host "[start] Uvicorn: $AppModule on $BackendHost`:$BackendPort"
if (Test-Path $EnvFile) {
    Write-Host "[start] Env file: $EnvFile"
} else {
    Write-Host "[start] Env file: not found, using current environment"
}

& $VenvPython @UvicornArgs
exit $LASTEXITCODE
