# PowerShell script to create 10 SVG frames for book flip animation

$framesDir = "$PSScriptRoot/../public/book-frames"

# Ensure directory exists
if (-not (Test-Path $framesDir)) {
    New-Item -ItemType Directory -Path $framesDir -Force
}

Write-Host "Creating 10 book flip animation frames..." -ForegroundColor Green

# Frame 0: Book closed
$frame0 = @'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="200" height="280" viewBox="0 0 200 280" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="#90aee5" stroke="#3a3530" stroke-width="2"/>
  <rect x="8" y="20" width="4" height="240" fill="#3a3530"/>
  <text x="90" y="120" text-anchor="middle" font-family="Arial" font-size="18" font-weight="bold" fill="white">DOODLE BOOK</text>
  <text x="90" y="150" text-anchor="middle" font-family="Arial" font-size="14" fill="white" opacity="0.9">上传区</text>
</svg>
'@
$frame0 | Out-File -FilePath "$framesDir/book-frame-00.svg" -Encoding UTF8

# Frame 1: Slight lift
$frame1 = @'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="200" height="280" viewBox="0 0 200 280" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="#90aee5" stroke="#3a3530" stroke-width="2" opacity="0.95"/>
  <rect x="8" y="20" width="4" height="240" fill="#3a3530"/>
  <g transform="rotate(-20, 10, 140)">
    <path d="M 10,20 Q 170,25 170,140 Q 170,255 10,260 L 10,20 Z" fill="#90aee5" stroke="#3a3530" stroke-width="2"/>
  </g>
</svg>
'@
$frame1 | Out-File -FilePath "$framesDir/book-frame-01.svg" -Encoding UTF8

# Frame 2: More lift
$frame2 = @'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="200" height="280" viewBox="0 0 200 280" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="#90aee5" stroke="#3a3530" stroke-width="2" opacity="0.9"/>
  <rect x="8" y="20" width="4" height="240" fill="#3a3530"/>
  <g transform="rotate(-40, 10, 140)">
    <path d="M 10,20 Q 170,30 170,140 Q 170,250 10,260 L 10,20 Z" fill="#90aee5" stroke="#3a3530" stroke-width="2"/>
  </g>
</svg>
'@
$frame2 | Out-File -FilePath "$framesDir/book-frame-02.svg" -Encoding UTF8

# Frame 3: Halfway lift
$frame3 = @'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="200" height="280" viewBox="0 0 200 280" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="#90aee5" stroke="#3a3530" stroke-width="2" opacity="0.85"/>
  <rect x="8" y="20" width="4" height="240" fill="#3a3530"/>
  <g transform="rotate(-60, 10, 140)">
    <path d="M 10,20 Q 170,40 170,140 Q 170,240 10,260 L 10,20 Z" fill="#90aee5" stroke="#3a3530" stroke-width="2"/>
  </g>
</svg>
'@
$frame3 | Out-File -FilePath "$framesDir/book-frame-03.svg" -Encoding UTF8

# Frame 4: Almost vertical
$frame4 = @'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="200" height="280" viewBox="0 0 200 280" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="#90aee5" stroke="#3a3530" stroke-width="2" opacity="0.8"/>
  <rect x="8" y="20" width="4" height="240" fill="#3a3530"/>
  <g transform="rotate(-80, 10, 140)">
    <path d="M 10,20 Q 170,60 170,140 Q 170,220 10,260 L 10,20 Z" fill="#90aee5" stroke="#3a3530" stroke-width="2"/>
  </g>
</svg>
'@
$frame4 | Out-File -FilePath "$framesDir/book-frame-04.svg" -Encoding UTF8

# Frame 5: Vertical edge
$frame5 = @'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="200" height="280" viewBox="0 0 200 280" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="#90aee5" stroke="#3a3530" stroke-width="2" opacity="0.7"/>
  <rect x="8" y="20" width="4" height="240" fill="#3a3530"/>
  <rect x="10" y="20" width="2" height="240" fill="#cccccc" stroke="#999999" stroke-width="0.5"/>
</svg>
'@
$frame5 | Out-File -FilePath "$framesDir/book-frame-05.svg" -Encoding UTF8

# Frame 6: Starting to show back
$frame6 = @'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="200" height="280" viewBox="0 0 200 280" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="#90aee5" stroke="#3a3530" stroke-width="2" opacity="0.6"/>
  <rect x="8" y="20" width="4" height="240" fill="#3a3530"/>
  <g transform="rotate(-100, 10, 140)">
    <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="#dbe8ff" stroke="#999999" stroke-width="1"/>
  </g>
</svg>
'@
$frame6 | Out-File -FilePath "$framesDir/book-frame-06.svg" -Encoding UTF8

# Frame 7: More back visible
$frame7 = @'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="200" height="280" viewBox="0 0 200 280" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="#90aee5" stroke="#3a3530" stroke-width="2" opacity="0.5"/>
  <rect x="8" y="20" width="4" height="240" fill="#3a3530"/>
  <g transform="rotate(-120, 10, 140)">
    <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="#dbe8ff" stroke="#999999" stroke-width="1"/>
    <rect x="30" y="60" width="120" height="4" fill="#39342d" fill-opacity="0.5"/>
    <rect x="30" y="80" width="100" height="4" fill="#39342d" fill-opacity="0.5"/>
  </g>
</svg>
'@
$frame7 | Out-File -FilePath "$framesDir/book-frame-07.svg" -Encoding UTF8

# Frame 8: Almost fully open
$frame8 = @'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="200" height="280" viewBox="0 0 200 280" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="#90aee5" stroke="#3a3530" stroke-width="2" opacity="0.3"/>
  <rect x="8" y="20" width="4" height="240" fill="#3a3530"/>
  <g transform="rotate(-150, 10, 140)">
    <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="#dbe8ff" stroke="#999999" stroke-width="1"/>
    <rect x="30" y="60" width="120" height="4" fill="#39342d" fill-opacity="0.7"/>
    <rect x="30" y="80" width="100" height="4" fill="#39342d" fill-opacity="0.7"/>
    <rect x="30" y="100" width="130" height="4" fill="#39342d" fill-opacity="0.7"/>
    <rect x="30" y="180" width="80" height="60" rx="4" ry="4" fill="#fff5b9" stroke="#999999" stroke-width="1"/>
    <text x="70" y="210" text-anchor="middle" font-family="Arial" font-size="9" fill="#666666">笔记</text>
  </g>
</svg>
'@
$frame8 | Out-File -FilePath "$framesDir/book-frame-08.svg" -Encoding UTF8

# Frame 9: Fully open
$frame9 = @'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="200" height="280" viewBox="0 0 200 280" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="20" width="80" height="240" rx="12" ry="12" fill="#dbe8ff" stroke="#999999" stroke-width="1"/>
  <rect x="90" y="20" width="4" height="240" fill="#3a3530"/>
  <rect x="94" y="20" width="76" height="240" rx="12" ry="12" fill="white" stroke="#999999" stroke-width="1"/>
  <rect x="30" y="60" width="50" height="4" fill="#39342d" fill-opacity="0.7"/>
  <rect x="30" y="80" width="40" height="4" fill="#39342d" fill-opacity="0.7"/>
  <rect x="30" y="150" width="40" height="60" rx="4" ry="4" fill="#fff5b9" stroke="#999999" stroke-width="1"/>
  <text x="50" y="180" text-anchor="middle" font-family="Arial" font-size="8" fill="#666666">解析笔记</text>
  <text x="132" y="80" text-anchor="middle" font-family="Arial" font-size="16" font-weight="bold" fill="#39342d">解析页</text>
  <text x="132" y="110" text-anchor="middle" font-family="Arial" font-size="12" fill="#666666">知识已加载</text>
</svg>
'@
$frame9 | Out-File -FilePath "$framesDir/book-frame-09.svg" -Encoding UTF8

Write-Host "Created 10 frames in: $framesDir" -ForegroundColor Green
Write-Host ""
Write-Host "To use these frames in React, update DoodleBookLayout.tsx to use:" -ForegroundColor Cyan
Write-Host "const frames = ["
Write-Host "  '/book-frames/book-frame-00.svg',"
Write-Host "  '/book-frames/book-frame-01.svg',"
Write-Host "  ..."
Write-Host "];"
Write-Host ""
Write-Host "Then use: <img src={frames[currentFrame]} alt={`Book frame ${currentFrame}`} />" -ForegroundColor Cyan