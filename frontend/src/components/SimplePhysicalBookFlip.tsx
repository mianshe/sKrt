import React, { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";

interface SimplePhysicalBookFlipProps {
  isPlaying: boolean;
  onComplete?: () => void;
  duration?: number;
  frontPage: React.ReactNode;
  backPage: React.ReactNode;
  showShadows?: boolean;
}

/**
 * Physical page flip model:
 * - Page 2 sits underneath Page 1.
 * - Page 1 is lifted from the right-side strip and flipped toward the left.
 */
const SimplePhysicalBookFlip: React.FC<SimplePhysicalBookFlipProps> = ({
  isPlaying,
  onComplete,
  duration = 1.9,
  frontPage,
  backPage,
  showShadows = true,
}) => {
  const [isFlipping, setIsFlipping] = useState(false);
  const timerRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    if (isPlaying) {
      setIsFlipping(true);
      if (onComplete) {
        timerRef.current = setTimeout(onComplete, duration * 1000);
      }
      return;
    }
    setIsFlipping(false);
  }, [duration, isPlaying, onComplete]);

  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    },
    []
  );

  return (
    <div className="relative h-full w-full overflow-hidden rounded-[20px]">
      <motion.div
        className="absolute inset-0 z-0"
        initial={{ opacity: 0 }}
        animate={
          isFlipping
            ? { opacity: [0, 0.02, 0.08, 0.16, 0.3, 0.5, 0.72, 0.88, 1, 1] }
            : { opacity: 0 }
        }
        transition={{ duration, ease: "linear" }}
      >
        {backPage}
      </motion.div>

      {showShadows && isFlipping && (
        <motion.div
          className="absolute inset-y-0 right-0 z-[5] bg-gradient-to-l from-black/30 via-black/15 to-transparent"
          initial={{ width: 0, opacity: 0 }}
          animate={{
            width: [0, 8, 18, 34, 56, 86, 124, 168, 220, 280],
            opacity: [0, 0.06, 0.12, 0.18, 0.24, 0.3, 0.33, 0.28, 0.18, 0.05],
          }}
          transition={{ duration, ease: "linear" }}
        />
      )}

      <motion.div
        className="absolute inset-0 z-10"
        style={{ transformOrigin: "right center", transformStyle: "preserve-3d" }}
        initial={{ rotateY: 0, rotateZ: 0, x: 0, y: 0, opacity: 1, scale: 1 }}
        animate={
          isFlipping
            ? {
                rotateY: [0, -6, -14, -24, -38, -56, -78, -104, -136, -168],
                rotateZ: [0, -0.4, -0.8, -1.2, -1.6, -1.8, -1.5, -1.1, -0.6, 0],
                x: [0, -4, -10, -18, -30, -44, -62, -84, -110, -138],
                y: [0, -1, -2, -4, -6, -8, -10, -12, -13, -14],
                scale: [1, 1, 0.998, 0.996, 0.992, 0.986, 0.978, 0.968, 0.956, 0.942],
                opacity: [1, 1, 0.995, 0.985, 0.96, 0.9, 0.78, 0.56, 0.28, 0.06],
              }
            : { rotateY: 0, rotateZ: 0, x: 0, y: 0, opacity: 1, scale: 1 }
        }
        transition={{ duration, ease: "linear" }}
      >
        {frontPage}
      </motion.div>

      <motion.div
        className="pointer-events-none absolute inset-y-0 right-0 z-[15] w-[16px] bg-gradient-to-l from-[#332d26]/55 via-[#4a4136]/35 to-transparent"
        initial={{ opacity: 0.4, x: 0 }}
        animate={
          isFlipping
            ? {
                x: [0, -2, -6, -12, -20, -30, -44, -60, -80, -102],
                width: [16, 18, 20, 22, 24, 24, 22, 20, 18, 14],
                opacity: [0.42, 0.5, 0.58, 0.64, 0.68, 0.66, 0.58, 0.48, 0.34, 0.2],
              }
            : { x: 0, width: 16, opacity: 0.4 }
        }
        transition={{ duration, ease: "linear" }}
      />
    </div>
  );
};

export default SimplePhysicalBookFlip;
