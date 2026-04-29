# PowerShell script to create SVG frames that MATCH the actual book design
# Based on the real DoodleBookLayout.tsx design

$framesDir = "$PSScriptRoot/../public/book-frames"

# Remove old frames
Remove-Item "$framesDir\*.svg" -Force -ErrorAction SilentlyContinue

Write-Host "Creating 10 book flip animation frames that MATCH the actual UI design..." -ForegroundColor Green

# Design specifications from DoodleBookLayout.tsx:
# - Main container: min-h-[620px], rounded-[20px], border-2 border-[#39342d], bg-[#90aee5]
# - Shadow: shadow-[14px_14px_0_rgba(40,38,34,0.2)]
# - Inner border: absolute inset-3 rounded-[14px] border border-white/40
# - Book spine: left edge
# - Title: "DOODLE BOOK" (text-[11px], font-black, tracking-[0.25em], opacity-70)
# - Main title: "上传区" (text-4xl, font-black, tracking-[0.12em])
# - Subtitle: "把文档拖进插画框，开始构建知识书" (text-[11px], opacity-80)
# - Content box: rounded-2xl border-2 border-[#39342d] bg-[#fffef9] p-5 shadow-[0_10px_0_rgba(57,52,45,0.14)]

# Frame 0: Exact match of the upload page
$frame0 = @'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="460" height="620" viewBox="0 0 460 620" xmlns="http://www.w3.org/2000/svg">
  <!-- Main book cover (matches min-h-[620px], rounded-[20px]) -->
  <rect x="0" y="0" width="460" height="620" rx="20" ry="20" fill="#90aee5" stroke="#39342d" stroke-width="2"/>
  
  <!-- Shadow effect (shadow-[14px_14px_0_rgba(40,38,34,0.2)]) -->
  <rect x="14" y="14" width="460" height="620" rx="20" ry="20" fill="rgba(40,38,34,0.2)" stroke="none"/>
  <rect x="0" y="0" width="460" height="620" rx="20" ry="20" fill="#90aee5" stroke="#39342d" stroke-width="2"/>
  
  <!-- Book spine (left edge) -->
  <rect x="0" y="0" width="8" height="620" fill="#3a3530"/>
  
  <!-- Inner white border (absolute inset-3 rounded-[14px] border border-white/40) -->
  <rect x="12" y="12" width="436" height="596" rx="14" ry="14" fill="none" stroke="rgba(255,255,255,0.4)" stroke-width="1"/>
  
  <!-- Title area -->
  <g transform="translate(230, 80)">
    <!-- DOODLE BOOK text -->
    <text text-anchor="middle" font-family="Arial, sans-serif" font-size="11" font-weight="900" fill="white" opacity="0.7" letter-spacing="0.25em">DOODLE BOOK</text>
    
    <!-- Main title: 上传区 -->
    <text text-anchor="middle" font-family="Arial, sans-serif" font-size="48" font-weight="900" fill="white" letter-spacing="0.12em" y="60">上传区</text>
    
    <!-- Subtitle -->
    <text text-anchor="middle" font-family="Arial, sans-serif" font-size="11" fill="white" opacity="0.8" y="90">把文档拖进插画框，开始构建知识书</text>
  </g>
  
  <!-- Content box (rounded-2xl border-2 border-[#39342d] bg-[#fffef9]) -->
  <rect x="50" y="150" width="360" height="400" rx="16" ry="16" fill="#fffef9" stroke="#39342d" stroke-width="2"/>
  
  <!-- Content box shadow (shadow-[0_10px_0_rgba(57,52,45,0.14)]) -->
  <rect x="50" y="160" width="360" height="400" rx="16" ry="16" fill="rgba(57,52,45,0.14)" stroke="none"/>
  <rect x="50" y="150" width="360" height="400" rx="16" ry="16" fill="#fffef9" stroke="#39342d" stroke-width="2"/>
  
  <!-- Content placeholder (where children would go) -->
  <rect x="80" y="180" width="300" height="340" rx="8" ry="8" fill="rgba(57,52,45,0.05)" stroke="rgba(57,52,45,0.1)" stroke-width="1"/>
  <text x="230" y="250" text-anchor="middle" font-family="Arial, sans-serif" font-size="14" fill="#666666">文档上传区域</text>
</svg>
'@
$frame0 | Out-File -FilePath "$framesDir/book-frame-00.svg" -Encoding UTF8

# Frame 1: Slight lift (rotate -15 degrees)
$frame1 = @'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="460" height="620" viewBox="0 0 460 620" xmlns="http://www.w3.org/2000/svg">
  <!-- Background (faded) -->
  <rect x="0" y="0" width="460" height="620" rx="20" ry="20" fill="#90aee5" stroke="#39342d" stroke-width="2" opacity="0.9"/>
  <rect x="14" y="14" width="460" height="620" rx="20" ry="20" fill="rgba(40,38,34,0.18)" stroke="none" opacity="0.9"/>
  
  <!-- Book spine (left edge) -->
  <rect x="0" y="0" width="8" height="620" fill="#3a3530"/>
  
  <!-- Flipping page (rotate -15 degrees around left edge) -->
  <g transform="rotate(-15, 0, 310)">
    <rect x="0" y="0" width="460" height="620" rx="20" ry="20" fill="#90aee5" stroke="#39342d" stroke-width="2"/>
    
    <!-- Inner border -->
    <rect x="12" y="12" width="436" height="596" rx="14" ry="14" fill="none" stroke="rgba(255,255,255,0.4)" stroke-width="1"/>
    
    <!-- Title area (slightly distorted) -->
    <g transform="translate(230, 80)">
      <text text-anchor="middle" font-family="Arial, sans-serif" font-size="11" font-weight="900" fill="white" opacity="0.7" letter-spacing="0.25em">DOODLE BOOK</text>
      <text text-anchor="middle" font-family="Arial, sans-serif" font-size="48" font-weight="900" fill="white" letter-spacing="0.12em" y="60">上传区</text>
    </g>
    
    <!-- Content box (perspective distortion) -->
    <rect x="50" y="150" width="360" height="400" rx="16" ry="16" fill="#fffef9" stroke="#39342d" stroke-width="2" opacity="0.9"/>
  </g>
  
  <!-- Shadow under lifting page -->
  <ellipse cx="100" cy="310" rx="80" ry="20" fill="rgba(0,0,0,0.1)"/>
</svg>
'@
$frame1 | Out-File -FilePath "$framesDir/book-frame-01.svg" -Encoding UTF8

# Frame 2: More lift (rotate -30 degrees)
$frame2 = @'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="460" height="620" viewBox="0 0 460 620" xmlns="http://www.w3.org/2000/svg">
  <!-- Background (more faded) -->
  <rect x="0" y="0" width="460" height="620" rx="20" ry="20" fill="#90aee5" stroke="#39342d" stroke-width="2" opacity="0.8"/>
  <rect x="14" y="14" width="460" height="620" rx="20" ry="20" fill="rgba(40,38,34,0.16)" stroke="none" opacity="0.8"/>
  
  <!-- Book spine -->
  <rect x="0" y="0" width="8" height="620" fill="#3a3530"/>
  
  <!-- Flipping page (rotate -30 degrees) -->
  <g transform="rotate(-30, 0, 310)">
    <rect x="0" y="0" width="460" height="620" rx="20" ry="20" fill="#90aee5" stroke="#39342d" stroke-width="2"/>
    
    <!-- Inner border -->
    <rect x="12" y="12" width="436" height="596" rx="14" ry="14" fill="none" stroke="rgba(255,255,255,0.4)" stroke-width="1"/>
    
    <!-- Title (more distorted) -->
    <g transform="translate(230, 80)">
      <text text-anchor="middle" font-family="Arial, sans-serif" font-size="11" font-weight="900" fill="white" opacity="0.7" letter-spacing="0.25em">DOODLE BOOK</text>
    </g>
    
    <!-- Content box (smaller due to perspective) -->
    <rect x="50" y="150" width="360" height="400" rx="16" ry="16" fill="#fffef9" stroke="#39342d" stroke-width="2" opacity="0.8"/>
  </g>
  
  <!-- Larger shadow -->
  <ellipse cx="60" cy="310" rx="60" ry="25" fill="rgba(0,0,0,0.15)"/>
</svg>
'@
$frame2 | Out-File -FilePath "$framesDir/book-frame-02.svg" -Encoding UTF8

# Frame 3: Half lift (rotate -45 degrees)
$frame3 = @'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="460" height="620" viewBox="0 0 460 620" xmlns="http://www.w3.org/2000/svg">
  <!-- Background (even more faded) -->
  <rect x="0" y="0" width="460" height="620" rx="20" ry="20" fill="#90aee5" stroke="#39342d" stroke-width="2" opacity="0.7"/>
  <rect x="14" y="14" width="460" height="620" rx="20" ry="20" fill="rgba(40,38,34,0.14)" stroke="none" opacity="0.7"/>
  
  <!-- Book spine -->
  <rect x="0" y="0" width="8" height="620" fill="#3a3530"/>
  
  <!-- Flipping page (rotate -45 degrees) -->
  <g transform="rotate(-45, 0, 310)">
    <rect x="0" y="0" width="460" height="620" rx="20" ry="20" fill="#90aee5" stroke="#39342d" stroke-width="2"/>
    
    <!-- Inner border (barely visible) -->
    <rect x="12" y="12" width="436" height="596" rx="14" ry="14" fill="none" stroke="rgba(255,255,255,0.3)" stroke-width="1"/>
    
    <!-- Content box (very distorted) -->
    <rect x="50" y="150" width="360" height="400" rx="16" ry="16" fill="#fffef9" stroke="#39342d" stroke-width="2" opacity="0.6"/>
  </g>
  
  <!-- Strong shadow -->
  <ellipse cx="40" cy="310" rx="50" ry="30" fill="rgba(0,0,0,0.2)"/>
</svg>
'@
$frame3 | Out-File -FilePath "$framesDir/book-frame-03.svg" -Encoding UTF8

# Frame 4: Almost vertical (rotate -60 degrees)
$frame4 = @'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="460" height="620" viewBox="0 0 460 620" xmlns="http://www.w3.org/2000/svg">
  <!-- Background (faded) -->
  <rect x="0" y="0" width="460" height="620" rx="20" ry="20" fill="#90aee5" stroke="#39342d" stroke-width="2" opacity="0.6"/>
  <rect x="14" y="14" width="460" height="620" rx="20" ry="20" fill="rgba(40,38,34,0.12)" stroke="none" opacity="0.6"/>
  
  <!-- Book spine -->
  <rect x="0" y="0" width="8" height="620" fill="#3a3530"/>
  
  <!-- Flipping page (rotate -60 degrees) - mostly edge view -->
  <g transform="rotate(-60, 0, 310)">
    <!-- Just show edge of page -->
    <rect x="0" y="0" width="460" height="620" rx="20" ry="20" fill="#90aee5" stroke="#39342d" stroke-width="2"/>
  </g>
  
  <!-- Edge highlight -->
  <rect x="0" y="0" width="2" height="620" fill="#cccccc" stroke="#999999" stroke-width="0.5"/>
  
  <!-- Strong shadow -->
  <ellipse cx="25" cy="310" rx="40" ry="35" fill="rgba(0,0,0,0.25)"/>
</svg>
'@
$frame4 | Out-File -FilePath "$framesDir/book-frame-04.svg" -Encoding UTF8

# Frame 5: Vertical edge (rotate -75 degrees, starting to show back)
$frame5 = @'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="460" height="620" viewBox="0 0 460 620" xmlns="http://www.w3.org/2000/svg">
  <!-- Background (very faded) -->
  <rect x="0" y="0" width="460" height="620" rx="20" ry="20" fill="#90aee5" stroke="#39342d" stroke-width="2" opacity="0.5"/>
  <rect x="14" y="14" width="460" height="620" rx="20" ry="20" fill="rgba(40,38,34,0.1)" stroke="none" opacity="0.5"/>
  
  <!-- Book spine -->
  <rect x="0" y="0" width="8" height="620" fill="#3a3530"/>
  
  <!-- Flipping page (rotate -75 degrees, starting to show back side) -->
  <g transform="rotate(-75, 0, 310)">
    <!-- Back side of page (book interior) -->
    <rect x="0" y="0" width="460" height="620" rx="20" ry="20" fill="#dbe8ff" stroke="#999999" stroke-width="1"/>
    
    <!-- Back side content (book pages) -->
    <rect x="30" y="50" width="400" height="20" rx="4" ry="4" fill="rgba(57,52,45,0.1)"/>
    <rect x="30" y="80" width="380" height="20" rx="4" ry="4" fill="rgba(57,52,45,0.1)"/>
    <rect x="30" y="110" width="420" height="20" rx="4" ry="4" fill="rgba(57,52,45,0.1)"/>
  </g>
  
  <!-- Shadow -->
  <ellipse cx="15" cy="310" rx="30" ry="40" fill="rgba(0,0,0,0.3)"/>
</svg>
'@
$frame5 | Out-File -FilePath "$framesDir/book-frame-05.svg" -Encoding UTF8

# Frame 6: Starting to open (rotate -90 degrees, more back visible)
$frame6 = @'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="460" height="620" viewBox="0 0 460 620" xmlns="http://www.w3.org/2000/svg">
  <!-- Background (barely visible) -->
  <rect x="0" y="0" width="460" height="620" rx="20" ry="20" fill="#90aee5" stroke="#39342d" stroke-width="2" opacity="0.4"/>
  
  <!-- Book spine -->
  <rect x="0" y="0" width="8" height="620" fill="#3a3530"/>
  
  <!-- Flipping page (rotate -90 degrees, back side fully visible) -->
  <g transform="rotate(-90, 0, 310)">
    <!-- Back side of page -->
    <rect x="0" y="0" width="460" height="620" rx="20" ry="20" fill="#dbe8ff" stroke="#999999" stroke-width="1"/>
    
    <!-- Book interior pages -->
    <rect x="30" y="50" width="400" height="20" rx="4" ry="4" fill="rgba(57,52,45,0.15)"/>
    <rect x="30" y="80" width="380" height="20" rx="4" ry="4" fill="rgba(57,52,45,0.15)"/>
    <rect x="30" y="110" width="420" height="20" rx="4" ry="4" fill="rgba(57,52,45,0.15)"/>
    <rect x="30" y="140" width="350" height="20" rx="4" ry="4" fill="rgba(57,52,45,0.15)"/>
    <rect x="30" y="200" width="200" height="100" rx="8" ry="8" fill="#fff5b9" stroke="rgba(57,52,45,0.2)" stroke-width="1"/>
    <text x="130" y="250" text-anchor="middle" font-family="Arial, sans-serif" font-size="12" fill="#666666">笔记区域</text>
  </g>
</svg>
'@
$frame6 | Out-File -FilePath "$framesDir/book-frame-06.svg" -Encoding UTF8

# Frame 7: More open (rotate -105 degrees)
$frame7 = @'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="460" height="620" viewBox="0 0 460 620" xmlns="http://www.w3.org/2000/svg">
  <!-- Book spine (center of open book) -->
  <rect x="230" y="0" width="8" height="620" fill="#3a3530"/>
  
  <!-- Left page (back side of flipped page) -->
  <g transform="rotate(-105, 0, 310)">
    <rect x="0" y="0" width="460" height="620" rx="20" ry="20" fill="#dbe8ff" stroke="#999999" stroke-width="1"/>
    
    <!-- Left page content -->
    <rect x="30" y="50" width="200" height="20" rx="4" ry="4" fill="rgba(57,52,45,0.2)"/>
    <rect x="30" y="80" width="180" height="20" rx="4" ry="4" fill="rgba(57,52,45,0.2)"/>
    <rect x="30" y="150" width="150" height="80" rx="8" ry="8" fill="#fff5b9" stroke="rgba(57,52,45,0.2)" stroke-width="1"/>
    <text x="105" y="190" text-anchor="middle" font-family="Arial, sans-serif" font-size="10" fill="#666666">解析笔记</text>
  </g>
  
  <!-- Right page (new knowledge page starting to appear) -->
  <g transform="translate(238, 0)" opacity="0.3">
    <rect x="0" y="0" width="222" height="620" rx="20" ry="20" fill="white" stroke="#999999" stroke-width="1"/>
  </g>
</svg>
'@
$frame7 | Out-File -FilePath "$framesDir/book-frame-07.svg" -Encoding UTF8

# Frame 8: Almost fully open (rotate -120 degrees)
$frame8 = @'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="460" height="620" viewBox="0 0 460 620" xmlns="http://www.w3.org/2000/svg">
  <!-- Book spine (center) -->
  <rect x="230" y="0" width="8" height="620" fill="#3a3530"/>
  
  <!-- Left page (fully visible) -->
  <rect x="0" y="0" width="230" height="620" rx="20" ry="20" fill="#dbe8ff" stroke="#999999" stroke-width="1"/>
  
  <!-- Left page content -->
  <rect x="30" y="50" width="170" height="20" rx="4" ry="4" fill="rgba(57,52,45,0.25)"/>
  <rect x="30" y="80" width="150" height="20" rx="4" ry="4" fill="rgba(57,52,45,0.25)"/>
  <rect x="30" y="150" width="120" height="60" rx="8" ry="8" fill="#fff5b9" stroke="rgba(57,52,45,0.2)" stroke-width="1"/>
  <text x="90" y="180" text-anchor="middle" font-family="Arial, sans-serif" font-size="9" fill="#666666">笔记</text>
  
  <!-- Right page (knowledge page, mostly visible) -->
  <rect x="238" y="0" width="222" height="620" rx="20" ry="20" fill="white" stroke="#999999" stroke-width="1" opacity="0.7"/>
  
  <!-- Right page content -->
  <text x="349" y="80" text-anchor="middle" font-family="Arial, sans-serif" font-size="20" font-weight="bold" fill="#39342d" opacity="0.7">解析页</text>
  <text x="349" y="110" text-anchor="middle" font-family="Arial, sans-serif" font-size="14" fill="#666666" opacity="0.7">知识已加载</text>
</svg>
'@
$frame8 | Out-File -FilePath "$framesDir/book-frame-08.svg" -Encoding UTF8

# Frame 9: Fully open (knowledge page)
$frame9 = @'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="460" height="620" viewBox="0 0 460 620" xmlns="http://www.w3.org/2000/svg">
  <!-- Book spine (center) -->
  <rect x="230" y="0" width="8" height="620" fill="#3a3530"/>
  
  <!-- Left page (book interior) -->
  <rect x="0" y="0" width="230" height="620" rx="20" ry="20" fill="#dbe8ff" stroke="#999999" stroke-width="1"/>
  
  <!-- Left page content -->
  <rect x="30" y="50" width="170" height="20" rx="4" ry="4" fill="rgba(57,52,45,0.3)"/>
  <rect x="30" y="80" width="150" height="20" rx="4" ry="4" fill="rgba(57,52,45,0.3)"/>
  <rect x="30" y="150" width="120" height="60" rx="8" ry="8" fill="#fff5b9" stroke="rgba(57,52,45,0.2)" stroke-width="1"/>
  <text x="90" y="180" text-anchor="middle" font-family="Arial, sans-serif" font-size="9" fill="#666666">解析笔记</text>
  
  <!-- Right page (knowledge page - full visibility) -->
  <rect x="238" y="0" width="222" height="620" rx="20" ry="20" fill="white" stroke="#999999" stroke-width="1"/>
  
  <!-- Knowledge page title (matches the knowledge panel design) -->
  <text x="349" y="80" text-anchor="middle" font-family="Arial, sans-serif" font-size="24" font-weight="bold" fill="#39342d">解析页</text>
  <text x="349" y="120" text-anchor="middle" font-family="Arial, sans-serif" font-size="16" fill="#666666">知识已加载</text>
  
  <!-- Content area (similar to knowledge panel) -->
  <rect x="260" y="150" width="180" height="400" rx="12" ry="12" fill="#e5e9ff" stroke="#cccccc" stroke-width="1"/>
  <text x="350" y="200" text-anchor="middle" font-family="Arial, sans-serif" font-size="14" fill="#666666">内容区域</text>
  
  <!-- Example content items -->
  <rect x="270" y="230" width="160" height="30" rx="4" ry="4" fill="white" stroke="#ddd" stroke-width="1"/>
  <rect x="270" y="270" width="160" height="30" rx="4" ry="4" fill="white" stroke="#ddd" stroke-width="1"/>
  <rect x="270" y="310" width="160" height="30" rx="4" ry="4" fill="white" stroke="#ddd" stroke-width="1"/>
</svg>
'@
$frame9 | Out-File -FilePath "$framesDir/book-frame-09.svg" -Encoding UTF8

Write-Host "Created 10 frames in: $framesDir" -ForegroundColor Green
Write-Host ""
Write-Host "These frames now MATCH the actual UI design:" -ForegroundColor Cyan
Write-Host "- Same dimensions: 460x620 (matching min-h-[620px] and max-w-[460px])" -ForegroundColor Cyan
Write-Host "- Same styling: rounded-[20px], border-2 border-[#39342d], bg-[#90aee5]" -ForegroundColor Cyan
Write-Host "- Same shadows: shadow-[14px_14px_0_rgba(40,38,34,0.2)]" -ForegroundColor Cyan
Write-Host "- Same inner elements: DOODLE BOOK title, content box, etc." -ForegroundColor Cyan