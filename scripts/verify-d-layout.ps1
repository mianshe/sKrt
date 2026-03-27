# Quick check: xm1 venv + repo frontend on D: (run from repo root or any cwd).
$ErrorActionPreference = "Continue"
$xm1 = Split-Path -Parent $PSScriptRoot
$ok = $true
$py = Join-Path $xm1 "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Warning "Missing venv: $py — run setup-venv.cmd"
    $ok = $false
} else {
    Write-Host "OK venv: $py"
}
$fe = Join-Path $xm1 "frontend\package.json"
$nm = Join-Path $xm1 "frontend\node_modules"
if (-not (Test-Path $fe)) {
    Write-Warning "No xm1\frontend\package.json (optional if you use D:\xm\frontend only)"
} elseif (-not (Test-Path $nm)) {
    Write-Warning "Run npm install in xm1\frontend"
    $ok = $false
} else {
    Write-Host "OK xm1 frontend node_modules"
}
$rootFe = Join-Path (Split-Path -Parent $xm1) "frontend\node_modules"
if (Test-Path $rootFe) {
    Write-Host "OK D:\xm\frontend\node_modules"
}
if (-not $ok) { exit 1 }
Write-Host "verify-d-layout: all required paths present."
exit 0
