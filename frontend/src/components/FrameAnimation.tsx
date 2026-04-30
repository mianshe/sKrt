import React, { useEffect, useState, useRef } from "react";

interface FrameAnimationProps {
  /** Whether the animation is currently playing */
  isPlaying: boolean;
  /** Callback when animation completes */
  onComplete?: () => void;
  /** Content for the current page (upload page) */
  currentPage: React.ReactNode;
  /** Content for the next page (knowledge page) */
  nextPage: React.ReactNode;
}

/**
 * Frame-by-frame animation using CSS transforms and clip-path to create 
 * the illusion of a page turning without needing multiple image files.
 * Creates 13 frames of animation through interpolation.
 */
const FrameAnimation: React.FC<FrameAnimationProps> = ({
  isPlaying,
  onComplete,
  currentPage,
  nextPage,
}) => {
  const [frame, setFrame] = useState<number>(0);
  const [isAnimating, setIsAnimating] = useState<boolean>(false);
  const animationRef = useRef<NodeJS.Timeout | null>(null);
  const totalFrames = 13;
  
  const startAnimation = () => {
    if (isAnimating) return;
    
    setIsAnimating(true);
    setFrame(0);
    
    const frameDuration = 50; // 50ms per frame for 650ms total
    
    const animate = (currentFrame: number) => {
      if (currentFrame <= totalFrames) {
        setFrame(currentFrame);
        animationRef.current = setTimeout(() => {
          animate(currentFrame + 1);
        }, frameDuration);
      } else {
        // Animation complete
        setIsAnimating(false);
        setFrame(totalFrames);
        if (onComplete) {
          onComplete();
        }
      }
    };
    
    animate(0);
  };
  
  const resetAnimation = () => {
    if (animationRef.current) {
      clearTimeout(animationRef.current);
    }
    setFrame(0);
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
  
  // Calculate animation progress (0 to 1)
  const progress = frame / totalFrames;
  
  // Calculate clip-path for current frame
  const getClipPath = (): string => {
    // For first half of animation, show decreasing portion of current page
    if (progress < 0.5) {
      // Clip from left to right as page turns
      const clipPercent = 100 * (1 - progress * 2);
      return `polygon(0% 0%, ${clipPercent}% 0%, ${clipPercent}% 100%, 0% 100%)`;
    } else {
      // For second half, show increasing portion of next page
      const clipPercent = 100 * ((progress - 0.5) * 2);
      return `polygon(0% 0%, ${clipPercent}% 0%, ${clipPercent}% 100%, 0% 100%)`;
    }
  };
  
  // Calculate transform for current frame
  const getTransform = (): string => {
    if (progress < 0.5) {
      // First half: current page rotates away
      const rotate = -progress * 180;
      const scale = 1 - progress * 0.2;
      const translateX = -progress * 50;
      return `rotateY(${rotate}deg) scale(${scale}) translateX(${translateX}px)`;
    } else {
      // Second half: next page rotates in
      const rotate = 180 - (progress - 0.5) * 180;
      const scale = 0.8 + (progress - 0.5) * 0.2;
      const translateX = -50 + (progress - 0.5) * 50;
      return `rotateY(${rotate}deg) scale(${scale}) translateX(${translateX}px)`;
    }
  };
  
  // Calculate opacity for each page
  const currentPageOpacity = progress < 0.5 ? 1 - progress * 2 : 0;
  const nextPageOpacity = progress < 0.5 ? 0 : (progress - 0.5) * 2;
  
  return (
    <div className="relative w-full h-full" style={{ perspective: "1500px" }}>
      {/* Current page container */}
      <div 
        className="absolute inset-0 transition-none"
        style={{
          transformStyle: 'preserve-3d',
          opacity: currentPageOpacity,
          clipPath: progress < 0.5 ? getClipPath() : 'none',
          transform: progress < 0.5 ? getTransform() : 'none',
        }}
      >
        {currentPage}
      </div>
      
      {/* Next page container */}
      <div 
        className="absolute inset-0 transition-none"
        style={{
          transformStyle: 'preserve-3d',
          opacity: nextPageOpacity,
          clipPath: progress >= 0.5 ? getClipPath() : 'none',
          transform: progress >= 0.5 ? getTransform() : 'none',
        }}
      >
        {nextPage}
      </div>
      
      {/* Debug info - can be removed */}
      {process.env.NODE_ENV === 'development' && (
        <div className="absolute top-2 right-2 z-50 bg-black/70 text-white px-2 py-1 rounded text-xs">
          Frame: {frame}/{totalFrames} ({Math.round(progress * 100)}%)
        </div>
      )}
    </div>
  );
};

export default FrameAnimation;