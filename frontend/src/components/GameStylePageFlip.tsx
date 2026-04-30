import React, { useState, useEffect, useRef } from "react";
import { motion } from "framer-motion";

interface GameStylePageFlipProps {
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
  /** Whether to show interactive hints */
  showHints?: boolean;
}

/**
 * Game-style page flip animation: camera faces page content directly,
 * page flips smoothly like in interactive games or e-book readers.
 * Simple, direct, and focused on the content.
 */
const GameStylePageFlip: React.FC<GameStylePageFlipProps> = ({
  isPlaying,
  onComplete,
  duration = 1.2,
  currentPage,
  nextPage,
  showHints = true,
}) => {
  const [isFlipping, setIsFlipping] = useState(false);
  const animationRef = useRef<NodeJS.Timeout | null>(null);
  
  useEffect(() => {
    if (isPlaying && !isFlipping) {
      setIsFlipping(true);
      
      if (onComplete) {
        animationRef.current = setTimeout(onComplete, duration * 1000);
      }
    } else if (!isPlaying && isFlipping) {
      setIsFlipping(false);
    }
    
    return () => {
      if (animationRef.current) {
        clearTimeout(animationRef.current);
      }
    };
  }, [isPlaying, isFlipping, duration, onComplete]);
  
  // Static view: just show current page with next page hidden behind
  if (!isPlaying) {
    return (
      <div className="relative w-full h-full">
        {/* Next page hidden behind current page */}
        <div className="absolute inset-0 z-0 opacity-0">
          {nextPage}
        </div>
        {/* Current page on top */}
        <div className="relative z-10">
          {currentPage}
          {/* Interactive hint for desktop */}
          {showHints && (
            <div className="absolute bottom-4 right-4 flex items-center gap-2 text-sm text-gray-600 opacity-70">
              <span>点击"解析页"翻到下一页</span>
              <div className="w-0 h-0 border-l-[8px] border-r-[8px] border-b-[12px] border-l-transparent border-r-transparent border-b-gray-500" />
            </div>
          )}
        </div>
      </div>
    );
  }
  
  return (
    <div className="relative w-full h-full perspective-1500">
      {/* Next page (knowledge) - revealed as current page flips away */}
      <motion.div
        className="absolute inset-0 z-0"
        initial={{ opacity: 0, rotateY: 90, scale: 0.98 }}
        animate={
          isFlipping
            ? {
                opacity: [0, 0, 0, 0.1, 0.3, 0.6, 0.9, 1, 1],
                rotateY: [90, 75, 60, 45, 30, 15, 0, 0, 0],
                scale: [0.98, 0.985, 0.99, 0.995, 1, 1, 1, 1, 1],
              }
            : {}
        }
        transition={{
          duration,
          ease: "easeOut",
          times: [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 0.9, 1],
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
        initial={{ rotateY: 0, scale: 1, opacity: 1 }}
        animate={
          isFlipping
            ? {
                rotateY: [0, -15, -30, -60, -90, -120, -150, -180, -200],
                scale: [1, 1, 0.99, 0.98, 0.97, 0.96, 0.95, 0.94, 0.93],
                opacity: [1, 1, 1, 0.95, 0.9, 0.8, 0.6, 0.3, 0],
                x: [0, -5, -10, -15, -20, -25, -30, -35, -40],
              }
            : {}
        }
        transition={{
          duration,
          ease: [0.25, 0.1, 0.2, 1], // Smooth game-like easing
          times: [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.65, 0.8, 1],
        }}
        style={{
          transformStyle: "preserve-3d",
          backfaceVisibility: "hidden",
        }}
      >
        {currentPage}
        
        {/* Page curl effect on the flipping edge */}
        {isFlipping && (
          <motion.div
            className="absolute top-0 left-0 h-full origin-right"
            animate={{
              scaleX: [0, 0.05, 0.1, 0.15, 0.2, 0.15, 0.1, 0.05, 0],
              opacity: [0, 0.2, 0.4, 0.6, 0.8, 0.6, 0.4, 0.2, 0],
              width: [0, 4, 8, 12, 16, 12, 8, 4, 0],
            }}
            transition={{ duration }}
            style={{
              background: "linear-gradient(90deg, rgba(0,0,0,0.2) 0%, rgba(0,0,0,0.4) 50%, rgba(0,0,0,0.2) 100%)",
              boxShadow: "inset 2px 0 6px rgba(255,255,255,0.2)",
            }}
          />
        )}
      </motion.div>
      
      {/* Game-style flip shadow */}
      {isFlipping && (
        <motion.div
          className="absolute inset-0 z-5"
          animate={{
            opacity: [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.4, 0.2, 0],
          }}
          transition={{ duration }}
          style={{
            background: "radial-gradient(circle at 30% 50%, rgba(0,0,0,0.3) 0%, transparent 60%)",
            filter: "blur(10px)",
          }}
        />
      )}
      
      {/* Interactive game hint during animation */}
      {showHints && isFlipping && (
        <motion.div
          className="absolute bottom-8 left-1/2 transform -translate-x-1/2 z-20"
          initial={{ opacity: 0, y: 20 }}
          animate={{
            opacity: [0, 1, 1, 1, 1, 1, 0.8, 0.5, 0],
            y: [20, 0, 0, 0, 0, 0, 0, 0, -10],
          }}
          transition={{ duration }}
        >
          <div className="flex items-center gap-3 px-4 py-2 bg-black/70 rounded-full text-white text-sm">
            <div className="flex items-center gap-1">
              <div className="w-2 h-2 bg-white rounded-full animate-pulse" />
              <div className="w-2 h-2 bg-white rounded-full animate-pulse delay-100" />
              <div className="w-2 h-2 bg-white rounded-full animate-pulse delay-200" />
            </div>
            <span>翻页中...</span>
            <div className="w-0 h-0 border-l-[10px] border-r-[10px] border-b-[14px] border-l-transparent border-r-transparent border-b-white ml-2" />
          </div>
        </motion.div>
      )}
    </div>
  );
};

export default GameStylePageFlip;