# Remove old C: cache dirs after migrating to D:\dev (see install-dev-cache-to-d.ps1).
# Uses robocopy /MIR to an empty folder when plain Remove-Item hits "access denied".
param(
    [switch]$DryRun,
    [switch]$IncludePaddlePaddlex,
    [switch]$StopNode
)

$ErrorActionPreference = "Continue"

function Test-IsReparsePoint([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $item = Get-Item -LiteralPath $Path -Force
    return [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
}

function Remove-DirRobust([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Host "[skip] $Label : not found — $Path"
        return
    }
    if (Test-IsReparsePoint $Path) {
        Write-Host "[skip] $Label : reparse point (junction/symlink) — $Path"
        return
    }
    $files = Get-ChildItem -LiteralPath $Path -Recurse -Force -File -ErrorAction SilentlyContinue
    $size = if ($files) { ($files | Measure-Object -Property Length -Sum).Sum } else { 0 }
    $mb = if ($size) { [math]::Round($size / 1MB, 2) } else { 0 }
    Write-Host "[remove] $Label (~$mb MB) — $Path"
    if ($DryRun) { return }

    try {
        Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
        Write-Host "  OK (Remove-Item)"
        return
    } catch {
        Write-Host "  Remove-Item failed, trying robocopy mirror..."
    }

    $empty = Join-Path "D:\dev" "_empty_robocopy_mirror"
    New-Item -ItemType Directory -Force -Path $empty | Out-Null
    try {
        & robocopy $empty $Path /MIR /R:1 /W:1 /NFL /NDL /NJH /NJS | Out-Null
        Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $Path) {
            Write-Warning "Still exists: $Path — close Node/antivirus and run again, or delete in Explorer."
        } else {
            Write-Host "  OK (robocopy)"
        }
    } finally {
        Remove-Item -LiteralPath $empty -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "DryRun=$DryRun IncludePaddlePaddlex=$IncludePaddlePaddlex StopNode=$StopNode`n"

if ($StopNode -and -not $DryRun) {
    Get-Process -Name "node" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    Write-Host "(stopped node processes)`n"
}

Remove-DirRobust (Join-Path $env:LOCALAPPDATA "pip\cache") "pip cache"
Remove-DirRobust (Join-Path $env:APPDATA "npm-cache") "npm cache (AppData Roaming)"
Remove-DirRobust (Join-Path $env:LOCALAPPDATA "npm-cache") "npm cache (LocalAppData)"

if ($IncludePaddlePaddlex) {
    Remove-DirRobust (Join-Path $env:USERPROFILE ".paddlex") "PaddleX .paddlex"
}

Remove-DirRobust (Join-Path $env:USERPROFILE ".cache\huggingface") "HuggingFace .cache\huggingface"

$cacheParent = Join-Path $env:USERPROFILE ".cache"
if ((Test-Path $cacheParent) -and -not (Test-IsReparsePoint $cacheParent)) {
    $left = Get-ChildItem -LiteralPath $cacheParent -Force -ErrorAction SilentlyContinue
    if (-not $left) {
        Write-Host "[remove] empty .cache folder — $cacheParent"
        if (-not $DryRun) { Remove-Item -LiteralPath $cacheParent -Force -ErrorAction SilentlyContinue }
    }
}

Write-Host "`nDone."
