import React, { useEffect, useState, useRef } from "react";

interface SvgFrameAnimationProps {
  /** Whether the animation is currently playing */
  isPlaying: boolean;
  /** Callback when animation completes */
  onComplete?: () => void;
}

/**
 * SVG Frame-by-Frame Animation Component
 * Uses 13 pre-rendered SVG frames from public/book-frames directory
 * Implements true frame-by-frame animation with precise timing
 */
const SvgFrameAnimation: React.FC<SvgFrameAnimationProps> = ({
  isPlaying,
  onComplete,
}) => {
  const [currentFrame, setCurrentFrame] = useState<number>(0);
  const [isAnimating, setIsAnimating] = useState<boolean>(false);
  const animationRef = useRef<NodeJS.Timeout | null>(null);
  
  const totalFrames = 13; // We have frames 00-12
  
  // Frame durations in milliseconds (total: 850ms)
  const FRAME_DURATIONS = [
    80,  // Frame 0: Initial frame - longer for user to notice
    60,  // Frame 1: Start of flip - fast movement  
    50,  // Frame 2: Continuing flip
    50,  // Frame 3: More flip
    50,  // Frame 4: Almost halfway
    50,  // Frame 5: Halfway point
    50,  // Frame 6: Passed halfway
    50,  // Frame 7: Near completion
    60,  // Frame 8: Beginning to show content - slightly slower
    60,  // Frame 9: More content visible
    70,  // Frame 10: Detailed content - even slower
    80,  // Frame 11: Rich details - emphasize content
    100, // Frame 12: Final frame - hold for reading
  ];
  
  const startAnimation = () => {
    if (isAnimating) return;
    
    setIsAnimating(true);
    setCurrentFrame(0);
    
    let frameIndex = 0;
    
    const animateNextFrame = () => {
      if (frameIndex < totalFrames) {
        setCurrentFrame(frameIndex);
        
        // Get duration for this frame
        const duration = FRAME_DURATIONS[frameIndex] || 60;
        
        animationRef.current = setTimeout(() => {
          frameIndex++;
          animateNextFrame();
        }, duration);
      } else {
        // Animation complete
        setIsAnimating(false);
        setCurrentFrame(totalFrames - 1);
        if (onComplete) {
          onComplete();
        }
      }
    };
    
    animateNextFrame();
  };
  
  const resetAnimation = () => {
    if (animationRef.current) {
      clearTimeout(animationRef.current);
      animationRef.current = null;
    }
    setCurrentFrame(0);
    setIsAnimating(false);
  };
  
  useEffect(() => {
    if (isPlaying && !isAnimating) {
      startAnimation();
    } else if (!isPlaying && isAnimating) {
      resetAnimation();
    }
    
    return () => {
      if (animationRef.current) {
        clearTimeout(animationRef.current);
      }
    };
  }, [isPlaying]);
  
  // Generate SVG frame paths
  const getFramePath = (frameNumber: number): string => {
    const paddedNumber = frameNumber.toString().padStart(2, '0');
    return `/book-frames/book-frame-${paddedNumber}.svg`;
  };
  
  // Get the current frame content
  const getCurrentFrame = () => {
    return (
      <img 
        src={getFramePath(currentFrame)}
        alt={`Book animation frame ${currentFrame}`}
        className="w-full h-full object-contain"
        style={{ imageRendering: 'crisp-edges' }}
      />
    );
  };
  
  return (
    <div className="relative w-full h-full">
      {/* Current frame display */}
      <div className="absolute inset-0">
        {getCurrentFrame()}
      </div>
      
      {/* Debug info */}
      {process.env.NODE_ENV === 'development' && (
        <div className="absolute top-2 right-2 z-50 bg-black/70 text-white px-2 py-1 rounded text-xs">
          Frame: {currentFrame + 1}/{totalFrames}
        </div>
      )}
    </div>
  );
};

export default SvgFrameAnimation;