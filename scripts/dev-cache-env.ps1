# Session-only: redirect pip / npm / HF caches to D: (used by start.ps1).
# Override root: $env:XM_DEV_CACHE_ROOT = "D:\your\path" before dot-sourcing.

$ErrorActionPreference = "Stop"
$DevCacheRoot = if ($env:XM_DEV_CACHE_ROOT -and $env:XM_DEV_CACHE_ROOT.Trim()) {
    $env:XM_DEV_CACHE_ROOT.Trim()
} else {
    "D:\dev"
}

$pipCache = Join-Path $DevCacheRoot "pip-cache"
$npmCache = Join-Path $DevCacheRoot "npm-cache"
$hfRoot = Join-Path $DevCacheRoot "ml-cache\huggingface"
$pdx = Join-Path $DevCacheRoot "ml-cache\paddlex"

foreach ($d in @($pipCache, $npmCache, (Split-Path $hfRoot -Parent), $hfRoot, (Split-Path $pdx -Parent), $pdx)) {
    if (-not (Test-Path $d)) {
        New-Item -ItemType Directory -Force -Path $d | Out-Null
    }
}

$env:PIP_CACHE_DIR = $pipCache
$env:HF_HOME = $hfRoot
$env:HF_HUB_CACHE = (Join-Path $hfRoot "hub")
$env:HUGGINGFACE_HUB_CACHE = $env:HF_HUB_CACHE
$env:XDG_CACHE_HOME = Join-Path $DevCacheRoot "ml-cache"
# npm honors this for the current process
$env:npm_config_cache = $npmCache

# Baidu OCR fallback (optional): set in User environment or uncomment here for the session.
# Do not commit real keys. After paddle/tesseract fail, auto mode calls Baidu if both are set.
# $env:BAIDU_OCR_API_KEY = ""
# $env:BAIDU_OCR_SECRET_KEY = ""
# $env:PDF_OCR_BAIDU_FALLBACK = "1"
# $env:BAIDU_OCR_PRODUCT = "accurate_basic"

# 默认已在 runtime_config 中为 API 优先。若要改回本地优先：$env:HYBRID_LOCAL_FIRST = "1"
