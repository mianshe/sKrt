import React, { useEffect, useState } from "react";

interface MinimalPageSwitchProps {
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
 * Minimal page switch: simple crossfade with guaranteed completion.
 * Uses CSS transitions and simple state management.
 */
const MinimalPageSwitch: React.FC<MinimalPageSwitchProps> = ({
  isPlaying,
  onComplete,
  currentPage,
  nextPage,
}) => {
  const [showNext, setShowNext] = useState(false);
  
  useEffect(() => {
    let timeoutId: NodeJS.Timeout | null = null;
    
    if (isPlaying) {
      // Start showing next page after short delay
      timeoutId = setTimeout(() => {
        setShowNext(true);
        
        // Call onComplete after animation
        if (onComplete) {
          timeoutId = setTimeout(() => {
            onComplete();
          }, 600); // Wait for fade transition
        }
      }, 100);
    } else {
      setShowNext(false);
    }
    
    return () => {
      if (timeoutId) {
        clearTimeout(timeoutId);
      }
    };
  }, [isPlaying, onComplete]);
  
  return (
    <div className="relative w-full h-full">
      {/* Current page - fades out */}
      <div 
        className={`absolute inset-0 transition-opacity duration-500 ease-in-out ${
          showNext ? 'opacity-0' : 'opacity-100'
        }`}
      >
        {currentPage}
      </div>
      
      {/* Next page - fades in */}
      <div 
        className={`absolute inset-0 transition-opacity duration-500 ease-in-out ${
          showNext ? 'opacity-100' : 'opacity-0'
        }`}
      >
        {nextPage}
      </div>
    </div>
  );
};

export default MinimalPageSwitch;