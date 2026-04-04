$ErrorActionPreference = "Stop"

$artifactDir = Join-Path $PSScriptRoot "artifacts"
$androidSource = Join-Path $PSScriptRoot "android\\android\\app\\build\\outputs\\apk\\debug\\app-debug.apk"
$desktopTarget = Join-Path $artifactDir "sKrt-setup.exe"
$androidTarget = Join-Path $artifactDir "sKrt.apk"
$desktopSource = Get-ChildItem (Join-Path $PSScriptRoot "desktop\\src-tauri\\target\\release\\bundle\\nsis") -Filter "*-setup.exe" |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1 -ExpandProperty FullName

New-Item -ItemType Directory -Force $artifactDir | Out-Null

if ([string]::IsNullOrWhiteSpace($desktopSource) -or !(Test-Path $desktopSource)) {
  throw "Desktop installer not found under clients\\desktop\\src-tauri\\target\\release\\bundle\\nsis"
}

if (!(Test-Path $androidSource)) {
  throw "Android apk not found: $androidSource"
}

Copy-Item $desktopSource $desktopTarget -Force
Copy-Item $androidSource $androidTarget -Force

Get-ChildItem $artifactDir | Select-Object Name, FullName, Length, LastWriteTime
