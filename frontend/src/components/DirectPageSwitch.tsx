import React, { useEffect } from "react";
import { motion } from "framer-motion";

interface DirectPageSwitchProps {
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
 * Direct page switch: simple crossfade with optional 3D flip effect.
 * Guaranteed to complete animation and trigger onComplete.
 */
const DirectPageSwitch: React.FC<DirectPageSwitchProps> = ({
  isPlaying,
  onComplete,
  currentPage,
  nextPage,
}) => {
  // Call onComplete after animation
  useEffect(() => {
    let timeoutId: NodeJS.Timeout;
    
    if (isPlaying && onComplete) {
      timeoutId = setTimeout(() => {
        onComplete();
      }, 800); // 800ms animation duration
    }
    
    return () => {
      if (timeoutId) {
        clearTimeout(timeoutId);
      }
    };
  }, [isPlaying, onComplete]);
  
  if (!isPlaying) {
    return <div className="relative w-full h-full">{currentPage}</div>;
  }
  
  return (
    <div className="relative w-full h-full">
      {/* Current page fading out */}
      <motion.div
        className="absolute inset-0"
        initial={{ opacity: 1, scale: 1, rotateY: 0 }}
        animate={{
          opacity: 0,
          scale: 0.95,
          rotateY: -30,
          transition: {
            duration: 0.8,
            ease: "easeInOut"
          }
        }}
      >
        {currentPage}
      </motion.div>
      
      {/* Next page fading in */}
      <motion.div
        className="absolute inset-0"
        initial={{ opacity: 0, scale: 0.95, rotateY: 30 }}
        animate={{
          opacity: 1,
          scale: 1,
          rotateY: 0,
          transition: {
            duration: 0.8,
            ease: "easeInOut",
            delay: 0.1
          }
        }}
      >
        {nextPage}
      </motion.div>
    </div>
  );
};

export default DirectPageSwitch;