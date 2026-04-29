# Fix all SVG files in the current directory
$files = Get-ChildItem "*.svg"

foreach ($file in $files) {
    Write-Host "Processing: $file" -ForegroundColor Yellow
    
    $content = Get-Content $file.FullName -Raw
    
    # Replace all garbled Chinese text
    $content = $content -replace '涓婁紶鍖?', '上传区'
    $content = $content -replace '鎶婃枃妗ｆ嫋杩涙彃鐢绘锛屽紑濮嬫瀯寤虹煡璇嗕功', '把文档拖进插画框，开始构建知识书'
    $content = $content -replace '鏂囨。涓婁紶鍖哄煙', '文档上传区域'
    $content = $content -replace '绗旇鍖哄煙', '笔记区域'
    $content = $content -replace '瑙ｆ瀽绗旇', '解析笔记'
    $content = $content -replace '瑙ｆ瀽椤?', '解析页'
    $content = $content -replace '鐭ヨ瘑宸插姞杞?', '知识已加载'
    $content = $content -replace '鍐呭鍖哄煙', '内容区域'
    $content = $content -replace '绗旇', '笔记'
    
    Set-Content $file.FullName $content -Encoding UTF8
    Write-Host "  Fixed: $file" -ForegroundColor Green
}

Write-Host "All SVG files have been fixed!" -ForegroundColor Green
Write-Host "Chinese characters should now display correctly." -ForegroundColor Green