import React, { useState, useEffect, useRef } from "react";

interface BookFlipAnimationProps {
  /** Whether the animation is currently playing */
  isPlaying: boolean;
  /** Callback when animation completes */
  onComplete?: () => void;
  /** Total duration in seconds */
  duration?: number;
  /** Number of frames in the animation */
  frameCount?: number;
}

/**
 * A frame-by-frame book flip animation using pre-rendered SVG images.
 * This provides a more realistic "missing frames" effect compared to CSS animations.
 */
const BookFlipAnimation: React.FC<BookFlipAnimationProps> = ({
  isPlaying,
  onComplete,
  duration = 1.6,
  frameCount = 10,
}) => {
  const [currentFrame, setCurrentFrame] = useState(0);
  const animationRef = useRef<NodeJS.Timeout | null>(null);
  const startTimeRef = useRef<number | null>(null);
  
  // Generate frame URLs (from public/book-frames/)
  const frameUrls = Array.from({ length: frameCount }, (_, i) => 
    `/book-frames/book-frame-${i.toString().padStart(2, '0')}.svg`
  );

  useEffect(() => {
    if (!isPlaying) {
      // Reset animation when not playing
      setCurrentFrame(0);
      if (animationRef.current) {
        clearInterval(animationRef.current);
        animationRef.current = null;
      }
      startTimeRef.current = null;
      return;
    }

    // Start the animation
    const frameDuration = duration * 1000 / frameCount; // ms per frame
    let frameIndex = 0;
    
    const animate = (timestamp: number) => {
      if (!startTimeRef.current) {
        startTimeRef.current = timestamp;
      }
      
      const elapsed = timestamp - startTimeRef.current;
      frameIndex = Math.min(Math.floor(elapsed / frameDuration), frameCount - 1);
      
      setCurrentFrame(frameIndex);
      
      if (frameIndex < frameCount - 1) {
        animationRef.current = setTimeout(() => {
          requestAnimationFrame(animate);
        }, frameDuration);
      } else {
        // Animation complete
        if (onComplete) {
          setTimeout(onComplete, 100); // Small delay for visual completion
        }
      }
    };

    // Start animation
    requestAnimationFrame(animate);

    return () => {
      if (animationRef.current) {
        clearTimeout(animationRef.current);
      }
    };
  }, [isPlaying, duration, frameCount, onComplete]);

  return (
    <div className="relative w-full h-full flex items-center justify-center">
      <div className="relative w-[460px] h-[620px]">
        {/* Current frame */}
        {frameUrls.map((url, index) => (
          <img
            key={index}
            src={url}
            alt={`Book flip frame ${index}`}
            className={`absolute top-0 left-0 w-full h-full transition-opacity duration-75 ${
              index === currentFrame ? "opacity-100" : "opacity-0"
            }`}
            style={{ 
              zIndex: index === currentFrame ? 10 : 0,
              // Ensure crisp edges for SVG
              imageRendering: 'crisp-edges'
            }}
          />
        ))}
        
        {/* Debug overlay (visible in development) */}
        {process.env.NODE_ENV === "development" && (
          <div className="absolute top-2 left-2 bg-black/70 text-white text-xs px-2 py-1 rounded">
            Frame {currentFrame + 1}/{frameCount}
          </div>
        )}
      </div>
    </div>
  );
};

export default BookFlipAnimation;