#!/usr/bin/env python3
"""
Generate 10 SVG frames for book flip animation.
Simple version without f-string issues.
"""

import os

def main():
    """Generate all SVG frames and save them to the public directory."""
    output_dir = os.path.join("public", "book-frames")
    os.makedirs(output_dir, exist_ok=True)
    
    total_frames = 10
    
    print("Generating " + str(total_frames) + " book flip frames...")
    
    # Simple frame 0
    for i in range(total_frames):
        # Create a simple SVG for each frame
        if i == 0:
            svg = '''<?xml version="1.0" encoding="UTF-8"?>
<svg width="200" height="280" viewBox="0 0 200 280" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="#90aee5" stroke="#3a3530" stroke-width="2"/>
  <rect x="8" y="20" width="4" height="240" fill="#3a3530"/>
  <text x="90" y="120" text-anchor="middle" font-family="Arial" font-size="18" font-weight="bold" fill="white">DOODLE BOOK</text>
</svg>'''
        elif i < 5:
            angle = i * 20
            svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg width="200" height="280" viewBox="0 0 200 280" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="#90aee5" stroke="#3a3530" stroke-width="2" opacity="0.9"/>
  <rect x="8" y="20" width="4" height="240" fill="#3a3530"/>
  <g transform="rotate({angle}, 10, 140)">
    <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="#90aee5" stroke="#3a3530" stroke-width="2"/>
  </g>
</svg>'''
        elif i == 5:
            svg = '''<?xml version="1.0" encoding="UTF-8"?>
<svg width="200" height="280" viewBox="0 0 200 280" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="#90aee5" stroke="#3a3530" stroke-width="2" opacity="0.8"/>
  <rect x="8" y="20" width="4" height="240" fill="#3a3530"/>
  <rect x="10" y="20" width="2" height="240" fill="#cccccc"/>
</svg>'''
        elif i < 9:
            angle = 90 + (i - 5) * 22.5
            svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg width="200" height="280" viewBox="0 0 200 280" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="#90aee5" stroke="#3a3530" stroke-width="2" opacity="0.7"/>
  <rect x="8" y="20" width="4" height="240" fill="#3a3530"/>
  <g transform="rotate({angle}, 10, 140)">
    <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="#dbe8ff" stroke="#999999" stroke-width="1"/>
  </g>
</svg>'''
        else:
            svg = '''<?xml version="1.0" encoding="UTF-8"?>
<svg width="200" height="280" viewBox="0 0 200 280" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="20" width="80" height="240" rx="12" ry="12" fill="#dbe8ff" stroke="#999999" stroke-width="1"/>
  <rect x="90" y="20" width="4" height="240" fill="#3a3530"/>
  <rect x="94" y="20" width="76" height="240" rx="12" ry="12" fill="white" stroke="#999999" stroke-width="1"/>
  <text x="132" y="80" text-anchor="middle" font-family="Arial" font-size="16" font-weight="bold" fill="#39342d">解析页</text>
</svg>'''
        
        filename = os.path.join(output_dir, f"book-frame-{i:02d}.svg")
        with open(filename, "w", encoding="utf-8") as f:
            f.write(svg)
        print("  Created: " + filename)
    
    print("
Created all frames in: " + output_dir)
    print("
Usage:")
    print("In DoodleBookLayout.tsx, you can now use:")
    print("const frame0 = '/book-frames/book-frame-00.svg';")
    print("const frame1 = '/book-frames/book-frame-01.svg';")
    print("// ... etc")

if __name__ == "__main__":
    main()