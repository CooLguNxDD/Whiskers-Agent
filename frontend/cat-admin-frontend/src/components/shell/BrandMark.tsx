/**
 * BrandMark Component
 *
 * Renders the application logo (CatMark) with configurable sizing.
 */

import { CatMark } from "./Icons"

interface BrandMarkProps {
  size?: number
  className?: string
}

/**
 * Renders the brand logo icon.
 */
export default function BrandMark({ size = 34, className }: BrandMarkProps) {
  return (
    <div
      className={`ct-brand-mark ${className ?? ""}`}
      style={{ width: size, height: size }}
    >
      <CatMark width={size * 0.7} height={size * 0.7} />
    </div>
  )
}
