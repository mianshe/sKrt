#!/usr/bin/env python3
"""
Fix Chinese character encoding in SVG files.
Simple version without f-string issues.
"""

import os
import sys

def main():
    frames_dir = os.path.join("public", "book-frames")
    
    if not os.path.exists(frames_dir):
        print("Error: Directory not found: " + frames_dir)
        return
    
    svg_files = [f for f in os.listdir(frames_dir) if f.endswith('.svg')]
    
    print("Found " + str(len(svg_files)) + " SVG files to fix...")
    
    replacements = [
        ("涓婁紶鍖?", "上传区"),
        ("鎶婃枃妗ｆ嫋杩涙彃鐢绘锛屽紑濮嬫瀯寤虹煡璇嗕功", "把文档拖进插画框，开始构建知识书"),
        ("鏂囨。涓婁紶鍖哄煙", "文档上传区域"),
        ("绗旇鍖哄煙", "笔记区域"),
        ("瑙ｆ瀽绗旇", "解析笔记"),
        ("瑙ｆ瀽椤?", "解析页"),
        ("鐭ヨ瘑宸插姞杞?", "知识已加载"),
        ("鍐呭鍖哄煙", "内容区域"),
        ("绗旇", "笔记"),
    ]
    
    for svg_file in svg_files:
        file_path = os.path.join(frames_dir, svg_file)
        
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        for wrong, correct in replacements:
            content = content.replace(wrong, correct)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print("  Fixed: " + svg_file)
    
    print("
All SVG files have been fixed!")
    print("Chinese characters should now display correctly.")

if __name__ == "__main__":
    main()