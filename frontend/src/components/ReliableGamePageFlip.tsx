import React, { useState, useEffect, useRef } from "react";
import { motion } from "framer-motion";

interface ReliableGamePageFlipProps {
  /** Whether the animation is currently playing */
  isPlaying: boolean;
  /** Callback when animation completes */
  onComplete?: () => void;
  /** Total duration in seconds */
  duration?: number;
  /** Content for the current page (upload page) */
  currentPage: React.ReactNode;
  /** Content for the next page (knowledge page) */
  nextPage: React.ReactNode;
}

/**
 * Reliable game-style page flip: guaranteed to complete animation
 * before unmounting or triggering onComplete.
 * Uses internal state to manage animation lifecycle.
 */
const ReliableGamePageFlip: React.FC<ReliableGamePageFlipProps> = ({
  isPlaying,
  onComplete,
  duration = 1.2,
  currentPage,
  nextPage,
}) => {
  const [internalPlaying, setInternalPlaying] = useState(false);
  const [animationCompleted, setAnimationCompleted] = useState(false);
  const animationRef = useRef<NodeJS.Timeout | null>(null);
  
  // Effect to start animation when isPlaying becomes true
  useEffect(() => {
    if (isPlaying && !internalPlaying) {
      setInternalPlaying(true);
      setAnimationCompleted(false);
      
      // Clear any existing timeouts
      if (animationRef.current) {
        clearTimeout(animationRef.current);
      }
      
      // Set timeout for animation completion
      animationRef.current = setTimeout(() => {
        setAnimationCompleted(true);
        setInternalPlaying(false);
        
        // Call onComplete after animation finishes
        if (onComplete) {
          onComplete();
        }
      }, duration * 1000);
    }
    
    return () => {
      if (animationRef.current) {
        clearTimeout(animationRef.current);
      }
    };
  }, [isPlaying, internalPlaying, duration, onComplete]);
  
  // If not playing and animation not completed, show static current page
  if (!internalPlaying && !animationCompleted) {
    return (
      <div className="relative w-full h-full">
        {/* Current page on top */}
        <div className="relative z-10">
          {currentPage}
          {/* Simple corner hint */}
          <div className="absolute top-4 right-4 opacity-70">
            <div className="flex items-center gap-1 text-xs text-gray-500">
              <span>翻页</span>
              <div className="w-0 h-0 border-l-[6px] border-r-[6px] border-t-[10px] border-l-transparent border-r-transparent border-t-gray-400 transform rotate-180" />
            </div>
          </div>
        </div>
      </div>
    );
  }
  
  // Animation in progress or completed but not yet unmounted
  return (
    <div className="relative w-full h-full perspective-1200">
      {/* Next page (knowledge) - fades in as current page flips away */}
      <motion.div
        className="absolute inset-0 z-0"
        key="next-page"
        initial={{ opacity: 0, scale: 0.98, rotateY: 15 }}
        animate={{
          opacity: animationCompleted ? 1 : [0, 0, 0.1, 0.3, 0.6, 0.85, 1, 1, 1],
          scale: animationCompleted ? 1 : [0.98, 0.985, 0.99, 1, 1, 1, 1, 1, 1],
          rotateY: animationCompleted ? 0 : [15, 10, 5, 0, 0, 0, 0, 0, 0],
        }}
        transition={{
          duration: animationCompleted ? 0 : duration,
          ease: "easeOut",
          times: [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.65, 0.85, 1],
        }}
        style={{
          transformStyle: "preserve-3d",
          backfaceVisibility: "hidden",
        }}
      >
        {nextPage}
      </motion.div>
      
      {/* Current page (upload) - flips away to the left */}
      <motion.div
        className="absolute inset-0 z-10 origin-center"
        key="current-page"
        initial={{ rotateY: 0, opacity: 1 }}
        animate={{
          rotateY: animationCompleted ? -220 : [0, -20, -45, -70, -100, -130, -160, -190, -220],
          opacity: animationCompleted ? 0 : [1, 1, 1, 0.95, 0.85, 0.7, 0.4, 0.1, 0],
          x: animationCompleted ? -64 : [0, -8, -16, -24, -32, -40, -48, -56, -64],
          scale: animationCompleted ? 0.92 : [1, 0.99, 0.98, 0.97, 0.96, 0.95, 0.94, 0.93, 0.92],
        }}
        transition={{
          duration: animationCompleted ? 0 : duration,
          ease: [0.22, 0.68, 0.22, 1],
          times: [0, 0.1, 0.2, 0.3, 0.4, 0.55, 0.7, 0.85, 1],
        }}
        style={{
          transformStyle: "preserve-3d",
          backfaceVisibility: "hidden",
        }}
      >
        {currentPage}
        
        {/* Subtle page edge highlight */}
        {!animationCompleted && (
          <motion.div
            className="absolute top-0 left-0 h-full origin-right"
            animate={{
              opacity: [0, 0.3, 0.5, 0.7, 0.8, 0.7, 0.5, 0.3, 0],
              width: [0, 4, 8, 12, 16, 12, 8, 4, 0],
            }}
            transition={{ duration }}
            style={{
              background: "linear-gradient(90deg, rgba(255,255,255,0.3) 0%, rgba(255,255,255,0.6) 50%, rgba(255,255,255,0.3) 100%)",
              boxShadow: "inset 1px 0 4px rgba(0,0,0,0.1)",
            }}
          />
        )}
      </motion.div>
      
      {/* Gentle ambient shadow during flip */}
      {!animationCompleted && (
        <motion.div
          className="absolute inset-0 z-5"
          animate={{
            opacity: [0, 0.08, 0.15, 0.2, 0.25, 0.2, 0.15, 0.08, 0],
          }}
          transition={{ duration }}
          style={{
            background: "radial-gradient(circle at 40% 50%, rgba(0,0,0,0.2) 0%, transparent 70%)",
            filter: "blur(8px)",
          }}
        />
      )}
      
      {/* Simple progress indicator */}
      {!animationCompleted && (
        <motion.div
          className="absolute bottom-8 left-1/2 transform -translate-x-1/2 z-20"
          initial={{ opacity: 0, y: 10 }}
          animate={{
            opacity: [0, 0.7, 0.9, 0.9, 0.9, 0.8, 0.6, 0.3, 0],
            y: [10, 0, 0, 0, 0, 0, 0, 0, -5],
          }}
          transition={{ duration }}
        >
          <div className="flex items-center gap-2 px-3 py-1 bg-black/60 rounded-full text-white text-xs">
            <div className="flex gap-0.5">
              <div className="w-1.5 h-1.5 bg-white rounded-full animate-pulse" />
              <div className="w-1.5 h-1.5 bg-white rounded-full animate-pulse delay-150" />
              <div className="w-1.5 h-1.5 bg-white rounded-full animate-pulse delay-300" />
            </div>
            <span>翻页中</span>
          </div>
        </motion.div>
      )}
    </div>
  );
};

export default ReliableGamePageFlip;