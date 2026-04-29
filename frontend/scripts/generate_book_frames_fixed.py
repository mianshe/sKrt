#!/usr/bin/env python3
"""
Generate 10 SVG frames for book flip animation.
Each frame shows a page at a different stage of flipping.
"""

import os
import math

# Colors from the DoodleBookLayout component
BOOK_COVER_COLOR = "#90aee5"  # Blue cover
BOOK_SPINE_COLOR = "#3a3530"  # Dark spine
BOOK_TEXT_COLOR = "#39342d"   # Text color
BOOK_BACK_COLOR = "#dbe8ff"   # Back page color

def generate_frame_svg(frame_num, total_frames=10):
    """Generate SVG for a specific frame of the book flip animation."""
    
    # Calculate progress from 0 to 1
    progress = frame_num / (total_frames - 1) if total_frames > 1 else 0
    
    # Animation parameters based on progress
    # For a flip from right to left (opening a book)
    # Frame 0: book closed (cover facing viewer)
    # Frame 9: book fully open (page fully turned)
    
    if frame_num == 0:
        # Frame 0: Book closed, cover facing viewer
        svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg width="200" height="280" viewBox="0 0 200 280" xmlns="http://www.w3.org/2000/svg">
  <!-- Book cover -->
  <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="{BOOK_COVER_COLOR}" stroke="{BOOK_SPINE_COLOR}" stroke-width="2"/>
  
  <!-- Book spine (left edge) -->
  <rect x="8" y="20" width="4" height="240" fill="{BOOK_SPINE_COLOR}"/>
  
  <!-- Cover title -->
  <text x="90" y="120" text-anchor="middle" font-family="Arial, sans-serif" font-size="18" font-weight="bold" fill="white">DOODLE BOOK</text>
  <text x="90" y="150" text-anchor="middle" font-family="Arial, sans-serif" font-size="14" fill="white" opacity="0.9">上传区</text>
  
  <!-- Decorative elements -->
  <rect x="30" y="180" width="120" height="60" rx="6" ry="6" fill="white" fill-opacity="0.2" stroke="white" stroke-opacity="0.3" stroke-width="1"/>
  <text x="90" y="210" text-anchor="middle" font-family="Arial, sans-serif" font-size="10" fill="white" opacity="0.7">拖入文档</text>
</svg>'''
    
    elif frame_num < total_frames // 2:
        # Frames 1-4: Page starting to lift
        lift_angle = progress * 90  # 0-90 degrees
        page_curl = 20 * math.sin(math.radians(lift_angle))
        
        svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg width="200" height="280" viewBox="0 0 200 280" xmlns="http://www.w3.org/2000/svg">
  <!-- Book cover (static background) -->
  <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="{BOOK_COVER_COLOR}" stroke="{BOOK_SPINE_COLOR}" stroke-width="2" opacity="0.9"/>
  <rect x="8" y="20" width="4" height="240" fill="{BOOK_SPINE_COLOR}"/>
  
  <!-- Flipping page (with 3D transform effect) -->
  <g transform="rotate({lift_angle}, 10, 140)">
    <!-- Page front (cover side) -->
    <path d="M 10,20 Q 170,{20 + page_curl} 170,140 Q 170,{260 - page_curl} 10,260 L 10,20 Z" 
          fill="{BOOK_COVER_COLOR}" stroke="{BOOK_SPINE_COLOR}" stroke-width="2"/>
    
    <!-- Page edge highlight -->
    <path d="M 170,{20 + page_curl} Q 165,{20 + page_curl - 5} 170,140 Q 165,{260 - page_curl + 5} 170,{260 - page_curl}" 
          stroke="white" stroke-width="1" stroke-opacity="0.3" fill="none"/>
  </g>
  
  <!-- Shadow under lifting page -->
  <ellipse cx="60" cy="140" rx="40" ry="10" fill="#000000" opacity="{0.1 + progress * 0.2}"/>
</svg>'''
    
    elif frame_num == total_frames // 2:
        # Frame 5: Page halfway (90 degrees)
        svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg width="200" height="280" viewBox="0 0 200 280" xmlns="http://www.w3.org/2000/svg">
  <!-- Book cover (static background) -->
  <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="{BOOK_COVER_COLOR}" stroke="{BOOK_SPINE_COLOR}" stroke-width="2" opacity="0.8"/>
  <rect x="8" y="20" width="4" height="240" fill="{BOOK_SPINE_COLOR}"/>
  
  <!-- Page at 90 degrees (edge view) -->
  <rect x="10" y="20" width="2" height="240" fill="#cccccc" stroke="#999999" stroke-width="0.5"/>
  
  <!-- Page back side (starting to show) -->
  <g transform="rotate(90, 10, 140)">
    <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="{BOOK_BACK_COLOR}" stroke="#999999" stroke-width="1" opacity="0.5"/>
  </g>
  
  <!-- Strong shadow -->
  <ellipse cx="30" cy="140" rx="30" ry="15" fill="#000000" opacity="0.3"/>
</svg>'''
    
    elif frame_num < total_frames - 1:
        # Frames 6-8: Page continuing to flip, back side becoming visible
        flip_angle = 90 + (progress - 0.5) * 90 * 2  # 90-180 degrees
        back_visibility = (progress - 0.5) * 2  # 0-1
        
        svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg width="200" height="280" viewBox="0 0 200 280" xmlns="http://www.w3.org/2000/svg">
  <!-- Book cover (fading background) -->
  <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="{BOOK_COVER_COLOR}" stroke="{BOOK_SPINE_COLOR}" stroke-width="2" opacity="{0.8 - back_visibility * 0.4}"/>
  <rect x="8" y="20" width="4" height="240" fill="{BOOK_SPINE_COLOR}"/>
  
  <!-- Flipping page (back side becoming visible) -->
  <g transform="rotate({flip_angle}, 10, 140)">
    <!-- Page back (interior page) -->
    <rect x="10" y="20" width="160" height="240" rx="12" ry="12" fill="{BOOK_BACK_COLOR}" stroke="#999999" stroke-width="1"/>
    
    <!-- Page content (text lines) -->
    <g opacity="{back_visibility}">
      <rect x="30" y="60" width="120" height="4" fill="{BOOK_TEXT_COLOR}" fill-opacity="0.7"/>
      <rect x="30" y="80" width="100" height="4" fill="{BOOK_TEXT_COLOR}" fill-opacity="0.7"/>
      <rect x="30" y="100" width="130" height="4" fill="{BOOK_TEXT_COLOR}" fill-opacity="0.7"/>
      <rect x="30" y="120" width="90" height="4" fill="{BOOK_TEXT_COLOR}" fill-opacity="0.7"/>
      <rect x="30" y="180" width="80" height="60" rx="4" ry="4" fill="#fff5b9" stroke="#999999" stroke-width="1"/>
      <text x="70" y="210" text-anchor="middle" font-family="Arial, sans-serif" font-size="9" fill="#666666">笔记</text>
    </g>
  </g>
  
  <!-- Shadow -->
  <ellipse cx="15" cy="140" rx="20" ry="10" fill="#000000" opacity="{0.3 - back_visibility * 0.1}"/>
</svg>'''
    
    else:
        # Frame 9: Book fully open, page turned
        svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg width="200" height="280" viewBox="0 0 200 280" xmlns="http://www.w3.org/2000/svg">
  <!-- Left page (book interior) -->
  <rect x="10" y="20" width="80" height="240" rx="12" ry="12" fill="{BOOK_BACK_COLOR}" stroke="#999999" stroke-width="1"/>
  
  <!-- Book spine (center) -->
  <rect x="90" y="20" width="4" height="240" fill="{BOOK_SPINE_COLOR}"/>
  
  <!-- Right page (new content area) -->
  <rect x="94" y="20" width="76" height="240" rx="12" ry="12" fill="white" stroke="#999999" stroke-width="1"/>
  
  <!-- Left page content -->
  <rect x="30" y="60" width="50" height="4" fill="{BOOK_TEXT_COLOR}" fill-opacity="0.7"/>
  <rect x="30" y="80" width="40" height="4" fill="{BOOK_TEXT_COLOR}" fill-opacity="0.7"/>
  <rect x="30" y="100" width="55" height="4" fill="{BOOK_TEXT_COLOR}" fill-opacity="0.7"/>
  <rect x="30" y="150" width="40" height="60" rx="4" ry="4" fill="#fff5b9" stroke="#999999" stroke-width="1"/>
  <text x="50" y="180" text-anchor="middle" font-family="Arial, sans-serif" font-size="8" fill="#666666">解析笔记</text>
  
  <!-- Right page title -->
  <text x="132" y="80" text-anchor="middle" font-family="Arial, sans-serif" font-size="16" font-weight="bold" fill="{BOOK_TEXT_COLOR}">解析页</text>
  <text x="132" y="110" text-anchor="middle" font-family="Arial, sans-serif" font-size="12" fill="#666666">知识已加载</text>
  
  <!-- Decorative elements -->
  <rect x="104" y="140" width="56" height="80" rx="6" ry="6" fill="#e5e9ff" stroke="#cccccc" stroke-width="1"/>
  <text x="132" y="180" text-anchor="middle" font-family="Arial, sans-serif" font-size="10" fill="#666666">内容区域</text>
</svg>'''
    
    return svg

def main():
    """Generate all SVG frames and save them to the public directory."""
    output_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "public", "book-frames")
    os.makedirs(output_dir, exist_ok=True)
    
    total_frames = 10
    
    print(f"Generating {total_frames} book flip frames...")
    
    for frame_num in range(total_frames):
        svg_content = generate_frame_svg(frame_num, total_frames)
        filename = os.path.join(output_dir, f"book-frame-{frame_num:02d}.svg")
        
        with open(filename, "w", encoding="utf-8") as f:
            f.write(svg_content)
        
        print(f"  Created: {filename}")
    
    # Also create a simple CSS file to demonstrate usage
    css_file = os.path.join(output_dir, "book-animation.css")
    css_content = '''/* Book frame animation CSS */
.book-animation-container {
  width: 200px;
  height: 280px;
  position: relative;
  margin: 0 auto;
}

.book-frame {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  transition: opacity 0.1s ease;
}

.book-frame.active {
  opacity: 1;
}

/* Manual frame-by-frame animation */
@keyframes book-flip {
  0% { background-image: url('/book-frames/book-frame-00.svg'); }
  10% { background-image: url('/book-frames/book-frame-01.svg'); }
  20% { background-image: url('/book-frames/book-frame-02.svg'); }
  30% { background-image: url('/book-frames/book-frame-03.svg'); }
  40% { background-image: url('/book-frames/book-frame-04.svg'); }
  50% { background-image: url('/book-frames/book-frame-05.svg'); }
  60% { background-image: url('/book-frames/book-frame-06.svg'); }
  70% { background-image: url('/book-frames/book-frame-07.svg'); }
  80% { background-image: url('/book-frames/book-frame-08.svg'); }
  90% { background-image: url('/book-frames/book-frame-09.svg'); }
  100% { background-image: url('/book-frames/book-frame-09.svg'); }
}

.animated-book {
  width: 200px;
  height: 280px;
  animation: book-flip 1.6s steps(10, end) infinite;
  background-size: contain;
  background-repeat: no-repeat;
  background-position: center;
}
'''
    
    with open(css_file, "w", encoding="utf-8") as f:
        f.write(css_content)
    
    print(f"
Created CSS file: {css_file}")
    print(f"
Frames saved to: {output_dir}")
    print("
To use these frames in React:")
    print("1. Import images: import frame0 from '/book-frames/book-frame-00.svg';")
    print("2. Create array: const frames = [frame0, frame1, ...];")
    print("3. Use in component: <img src={frames[currentFrame]} />")

if __name__ == "__main__":
    main()