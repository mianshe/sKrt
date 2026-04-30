import React, { useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";

interface SimpleFlipTransitionProps {
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
 * Simple, reliable transition using AnimatePresence for guaranteed state management.
 * Uses fade-out + 3D flip for the old page, fade-in for the new page.
 */
const SimpleFlipTransition: React.FC<SimpleFlipTransitionProps> = ({
  isPlaying,
  onComplete,
  currentPage,
  nextPage,
}) => {
  // Call onComplete after animation duration (0.8s exit + 0.1s delay = 0.9s total)
  useEffect(() => {
    let timeoutId: NodeJS.Timeout;
    
    if (isPlaying && onComplete) {
      // Total animation time: exit duration (0.8s) + enter delay (0.1s) = 0.9s
      // Add a small buffer to ensure animation is complete
      timeoutId = setTimeout(() => {
        onComplete();
      }, 950); // 950ms = 0.95s
    }
    
    return () => {
      if (timeoutId) {
        clearTimeout(timeoutId);
      }
    };
  }, [isPlaying, onComplete]);
  
  return (
    <div className="relative w-full h-full">
      <AnimatePresence mode="wait">
        {/* Show current page when NOT playing */}
        {!isPlaying && (
          <motion.div
            key="current"
            className="absolute inset-0"
            initial={{ opacity: 0, scale: 0.98 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ 
              opacity: 0,
              rotateY: -90,
              x: -50,
              transition: { 
                duration: 0.8,
                ease: "easeInOut" 
              }
            }}
            transition={{ duration: 0.3 }}
          >
            {currentPage}
          </motion.div>
        )}

        {/* Show next page when playing */}
        {isPlaying && (
          <motion.div
            key="next"
            className="absolute inset-0"
            initial={{ 
              opacity: 0,
              rotateY: 90,
              x: 50,
              scale: 0.95 
            }}
            animate={{ 
              opacity: 1,
              rotateY: 0,
              x: 0,
              scale: 1,
              transition: { 
                duration: 0.8,
                ease: "easeInOut",
                delay: 0.1 
              }
            }}
          >
            {nextPage}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

export default SimpleFlipTransition;