# One-time (re-runnable): create D:\dev cache dirs and set USER environment variables
# so new terminals use D: for pip/npm/HF without dot-sourcing dev-cache-env.ps1.
# Run:  powershell -ExecutionPolicy Bypass -File scripts\install-dev-cache-to-d.ps1
# Optional junction for PaddleX models (was %USERPROFILE%\.paddlex): see bottom comments.

$ErrorActionPreference = "Stop"
$DevCacheRoot = if ($args[0]) { $args[0] } elseif ($env:XM_DEV_CACHE_ROOT) { $env:XM_DEV_CACHE_ROOT.Trim() } else { "D:\dev" }

$pipCache = Join-Path $DevCacheRoot "pip-cache"
$npmCache = Join-Path $DevCacheRoot "npm-cache"
$hfRoot = Join-Path $DevCacheRoot "ml-cache\huggingface"
$pdxTarget = Join-Path $DevCacheRoot "ml-cache\paddlex"

foreach ($d in @($pipCache, $npmCache, (Split-Path $hfRoot -Parent), $hfRoot, (Split-Path $pdxTarget -Parent), $pdxTarget)) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

function Set-UserEnv {
    param([string]$Name, [string]$Value)
    [Environment]::SetEnvironmentVariable($Name, $Value, "User")
    Write-Host "User env: $Name = $Value"
}

Set-UserEnv "PIP_CACHE_DIR" $pipCache
Set-UserEnv "HF_HOME" $hfRoot
$hub = Join-Path $hfRoot "hub"
Set-UserEnv "HF_HUB_CACHE" $hub
Set-UserEnv "HUGGINGFACE_HUB_CACHE" $hub
Set-UserEnv "XDG_CACHE_HOME" (Join-Path $DevCacheRoot "ml-cache")

# npm global user config (persists)
if (Get-Command npm -ErrorAction SilentlyContinue) {
    & npm config set cache $npmCache
    Write-Host "npm cache = $npmCache"
} else {
    Write-Host "npm not in PATH; set cache manually: npm config set cache `"$npmCache`""
}

Write-Host ""
Write-Host "Done. Open a NEW terminal and run: pip cache dir"
Write-Host "Optional PaddleX: if models are under $env:USERPROFILE\.paddlex and you want them on D:,"
Write-Host "  1) Move/rename that folder, 2) cmd admin: mklink /J `"$env:USERPROFILE\.paddlex`" `"$pdxTarget`""
