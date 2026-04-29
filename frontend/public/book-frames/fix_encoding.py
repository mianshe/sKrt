#!/usr/bin/env python3
import os
import sys

# Get the current directory
dir_path = os.path.dirname(__file__)

# Define replacement patterns
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

print("Starting to fix SVG files...")

for filename in os.listdir(dir_path):
    if filename.endswith(".svg"):
        filepath = os.path.join(dir_path, filename)
        
        # Read the file
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Apply replacements
        for old, new in replacements:
            content = content.replace(old, new)
        
        # Write back
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        
        print(f"Fixed: {filename}")

print("All SVG files have been fixed!")
print("Chinese characters should now display correctly.")