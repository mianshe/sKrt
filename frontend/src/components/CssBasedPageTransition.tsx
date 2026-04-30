import React, { useEffect, useRef, useState } from "react";

interface CssBasedPageTransitionProps {
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
 * CSS-based page transition using CSS classes and transitions.
 * More reliable than complex state-managed animations.
 */
const CssBasedPageTransition: React.FC<CssBasedPageTransitionProps> = ({
  isPlaying,
  onComplete,
  currentPage,
  nextPage,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [animationStage, setAnimationStage] = useState<"idle" | "animating" | "complete">("idle");
  const timeoutRef = useRef<NodeJS.Timeout | null>(null);
  
  useEffect(() => {
    // Clear any existing timeouts
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
    
    if (isPlaying && animationStage === "idle") {
      setAnimationStage("animating");
      
      // Animation duration: 800ms
      timeoutRef.current = setTimeout(() => {
        setAnimationStage("complete");
        
        // Call onComplete after animation
        if (onComplete) {
          timeoutRef.current = setTimeout(() => {
            onComplete();
            // Reset after onComplete is called
            timeoutRef.current = setTimeout(() => {
              setAnimationStage("idle");
            }, 50);
          }, 50);
        }
      }, 800);
    }
    
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, [isPlaying, animationStage, onComplete]);
  
  // Reset if isPlaying becomes false during animation
  useEffect(() => {
    if (!isPlaying && animationStage !== "idle") {
      setAnimationStage("idle");
    }
  }, [isPlaying, animationStage]);
  
  const isAnimating = animationStage === "animating" || (isPlaying && animationStage === "complete");
  
  return (
    <div 
      ref={containerRef}
      className="relative w-full h-full"
    >
      {/* Current page */}
      <div 
        className={`absolute inset-0 transition-all duration-800 ease-in-out ${
          isAnimating 
            ? "opacity-0 scale-95 -translate-x-8 rotate-y-[-20deg]" 
            : "opacity-100 scale-100 translate-x-0 rotate-y-0"
        }`}
        style={{
          transformStyle: 'preserve-3d',
          backfaceVisibility: 'hidden',
        }}
      >
        {currentPage}
      </div>
      
      {/* Next page */}
      <div 
        className={`absolute inset-0 transition-all duration-800 ease-in-out ${
          isAnimating 
            ? "opacity-100 scale-100 translate-x-0 rotate-y-0" 
            : "opacity-0 scale-95 translate-x-8 rotate-y-20"
        }`}
        style={{
          transformStyle: 'preserve-3d',
          backfaceVisibility: 'hidden',
        }}
      >
        {nextPage}
      </div>
      
      {/* CSS styles for 3D transform */}
      <style>
        {`
          .rotate-y-20 { transform: rotateY(20deg); }
          .rotate-y-[-20deg] { transform: rotateY(-20deg); }
          .rotate-y-0 { transform: rotateY(0deg); }
          .duration-800 { transition-duration: 800ms; }
        `}
      </style>
    </div>
  );
};

export default CssBasedPageTransition;