# Simple script to fix Chinese characters in SVG files
param([string]$framesDir = "$PSScriptRoot\..\public\book-frames")

Write-Host "Fixing Chinese characters in SVG files..." -ForegroundColor Green

# Read the correct Chinese text from DoodleBookLayout.tsx
$correctText = @{
    MainTitle = "上传区"
    Subtitle = "把文档拖进插画框，开始构建知识书"
    ContentPlaceholder = "文档上传区域"
    Note = "笔记区域"
    AnalysisNote = "解析笔记"
    KnowledgePage = "解析页"
    KnowledgeLoaded = "知识已加载"
    ContentArea = "内容区域"
    SimpleNote = "笔记"
}

$svgFiles = Get-ChildItem -Path $framesDir -Filter "*.svg"

foreach ($file in $svgFiles) {
    $content = [System.IO.File]::ReadAllText($file.FullName, [System.Text.Encoding]::UTF8)
    
    # Replace each garbled pattern
    $content = $content -replace "涓婁紶鍖?", $correctText.MainTitle
    $content = $content -replace "鎶婃枃妗ｆ嫋杩涙彃鐢绘锛屽紑濮嬫瀯寤虹煡璇嗕功", $correctText.Subtitle
    $content = $content -replace "鏂囨。涓婁紶鍖哄煙", $correctText.ContentPlaceholder
    $content = $content -replace "绗旇鍖哄煙", $correctText.Note
    $content = $content -replace "瑙ｆ瀽绗旇", $correctText.AnalysisNote
    $content = $content -replace "瑙ｆ瀽椤?", $correctText.KnowledgePage
    $content = $content -replace "鐭ヨ瘑宸插姞杞?", $correctText.KnowledgeLoaded
    $content = $content -replace "鍐呭鍖哄煙", $correctText.ContentArea
    $content = $content -replace "绗旇", $correctText.SimpleNote
    
    # Write back
    [System.IO.File]::WriteAllText($file.FullName, $content, [System.Text.Encoding]::UTF8)
    Write-Host "  Fixed: $($file.Name)" -ForegroundColor Yellow
}

Write-Host "All SVG files have been fixed!" -ForegroundColor Green