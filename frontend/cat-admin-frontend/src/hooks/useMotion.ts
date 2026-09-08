/**
 * useMotion Hook
 *
 * Provides framer-motion configurations that respect user's reduced motion preferences.
 * Includes a pre-defined fadeUp animation configuration for consistent transitions.
 */
import { useReducedMotion } from "framer-motion"

export interface MotionConfig {
  reduced: boolean
  fadeUp: {
    initial: { opacity: number; y: number }
    animate: { opacity: number; y: number }
    exit: { opacity: number; y: number }
    transition: { duration: number; ease: [number, number, number, number] }
  }
}

/**
 * Returns a motion configuration object adjusted for accessibility settings.
 */
export function useMotionConfig(): MotionConfig {
  const reduced = useReducedMotion() ?? false

  return {
    reduced,
    fadeUp: {
      initial: { opacity: 0, y: reduced ? 0 : 8 },
      animate: { opacity: 1, y: 0 },
      exit: { opacity: 0, y: reduced ? 0 : -4 },
      transition: { duration: reduced ? 0 : 0.2, ease: [0.16, 1, 0.3, 1] },
    },
  }
}
