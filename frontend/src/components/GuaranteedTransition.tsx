import React, { useEffect, useState } from "react";

interface GuaranteedTransitionProps {
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
 * Guaranteed transition: Uses simple CSS opacity transitions
 * with guaranteed onComplete callback using setTimeout.
 * No complex state management, just CSS classes.
 */
const GuaranteedTransition: React.FC<GuaranteedTransitionProps> = ({
  isPlaying,
  onComplete,
  currentPage,
  nextPage,
}) => {
  const [stage, setStage] = useState<"current" | "transitioning" | "next">("current");
  
  useEffect(() => {
    let timeoutId: NodeJS.Timeout | null = null;
    
    if (isPlaying && stage === "current") {
      // Start transition
      setStage("transitioning");
      
      // After 300ms, show next page
      timeoutId = setTimeout(() => {
        setStage("next");
        
        // Call onComplete after animation completes
        if (onComplete) {
          timeoutId = setTimeout(() => {
            onComplete();
          }, 300);
        }
      }, 300);
    } else if (!isPlaying) {
      // Reset to current page
      setStage("current");
    }
    
    return () => {
      if (timeoutId) {
        clearTimeout(timeoutId);
      }
    };
  }, [isPlaying, stage, onComplete]);
  
  return (
    <div className="relative w-full h-full">
      {/* Current page */}
      <div 
        className={`absolute inset-0 transition-opacity duration-300 ease-in-out ${
          stage === "current" ? "opacity-100" : "opacity-0"
        }`}
        style={{ pointerEvents: stage === "current" ? "auto" : "none" }}
      >
        {currentPage}
      </div>
      
      {/* Next page */}
      <div 
        className={`absolute inset-0 transition-opacity duration-300 ease-in-out ${
          stage === "next" ? "opacity-100" : "opacity-0"
        }`}
        style={{ pointerEvents: stage === "next" ? "auto" : "none" }}
      >
        {nextPage}
      </div>
      
      {/* Transitioning state - show next page but still fading */}
      <div 
        className={`absolute inset-0 transition-opacity duration-300 ease-in-out ${
          stage === "transitioning" ? "opacity-100" : "opacity-0"
        }`}
        style={{ pointerEvents: "none" }}
      >
        {nextPage}
      </div>
    </div>
  );
};

export default GuaranteedTransition;