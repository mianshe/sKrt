#!/usr/bin/env python3
"""
Fix Chinese character encoding in SVG files.
The SVG files have garbled Chinese text that needs to be corrected.
"""

import os
import re

def fix_chinese_in_svg(file_path):
    """Fix Chinese characters in a single SVG file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # These are the garbled patterns found in the SVG files
    # and their correct replacements from DoodleBookLayout.tsx
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
    
    for wrong, correct in replacements:
        content = content.replace(wrong, correct)
    
    # Write back with UTF-8 encoding
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    return True

def main():
    frames_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "public", "book-frames")
    
    if not os.path.exists(frames_dir):
        print(f"Error: Directory not found: {frames_dir}")
        return
    
    svg_files = [f for f in os.listdir(frames_dir) if f.endswith('.svg')]
    
    print(f"Found {len(svg_files)} SVG files to fix...")
    
    for svg_file in svg_files:
        file_path = os.path.join(frames_dir, svg_file)
        fix_chinese_in_svg(file_path)
        print(f"  Fixed: {svg_file}")
    
    print("
All SVG files have been fixed!")
    print("Chinese characters should now display correctly.")

if __name__ == "__main__":
    main()