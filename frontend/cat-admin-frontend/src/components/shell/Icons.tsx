/**
 * Icons Collection
 *
 * A set of reusable SVG icon components for the application interface.
 */

import type { SVGProps } from "react"

type IconProps = SVGProps<SVGSVGElement>

/**
 * CatMark icon component.
 */
export const CatMark = (p: IconProps) => (
  <svg viewBox="0 0 32 32" fill="none" {...p}>
    <path d="M7 12 L9.5 4 L12.5 11 Z" fill="currentColor" />
    <path d="M19.5 11 L22.5 4 L25 12 Z" fill="currentColor" />
    <path d="M9 9.5 L10 6.5 L11.2 9 Z" fill="oklch(0.18 0.018 45)" />
    <path d="M20.8 9 L22 6.5 L23 9.5 Z" fill="oklch(0.18 0.018 45)" />
    <circle cx="16" cy="18" r="10" stroke="currentColor" strokeWidth="1.7" />
    <circle cx="16" cy="18" r="7" fill="currentColor" opacity="0.12" />
    <ellipse cx="13" cy="17" rx="1.2" ry="1.6" fill="currentColor" />
    <ellipse cx="19" cy="17" rx="1.2" ry="1.6" fill="currentColor" />
    <path d="M9 19 L7 19 M9 20.5 L7.5 21 M23 19 L25 19 M23 20.5 L24.5 21" stroke="currentColor" strokeWidth="0.7" strokeLinecap="round" opacity="0.6" />
    <path d="M16 19.2 L15.2 20 L16 20.8 L16.8 20 Z" fill="currentColor" />
  </svg>
)

/**
 * Paw icon component.
 */
export const Paw = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="currentColor" {...p}>
    <ellipse cx="12" cy="16" rx="5" ry="4.2" />
    <ellipse cx="6" cy="11" rx="2.2" ry="2.6" />
    <ellipse cx="10" cy="7" rx="2" ry="2.6" />
    <ellipse cx="14" cy="7" rx="2" ry="2.6" />
    <ellipse cx="18" cy="11" rx="2.2" ry="2.6" />
  </svg>
)

/**
 * Bolt icon component.
 */
export const Bolt = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" {...p}>
    <path d="M13 2 4 14h7l-1 8 9-12h-7l1-8z" fill="currentColor" />
  </svg>
)

/**
 * Grid icon component.
 */
export const Grid = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" {...p}>
    <rect x="3.5" y="3.5" width="7" height="7" rx="1.5" />
    <rect x="13.5" y="3.5" width="7" height="7" rx="1.5" />
    <rect x="3.5" y="13.5" width="7" height="7" rx="1.5" />
    <rect x="13.5" y="13.5" width="7" height="7" rx="1.5" />
  </svg>
)

/**
 * Cube icon component.
 */
export const Cube = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" {...p}>
    <path d="M12 3 4 7v10l8 4 8-4V7l-8-4z" />
    <path d="M4 7l8 4 8-4M12 11v10" />
  </svg>
)

/**
 * Activity icon component.
 */
export const Activity = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M3 12h4l3-8 4 16 3-8h4" />
  </svg>
)

/**
 * Gear icon component.
 */
export const Gear = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h0a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v0a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
  </svg>
)

/**
 * Search icon component.
 */
export const Search = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" {...p}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.5-3.5" />
  </svg>
)

/**
 * Server icon component.
 */
export const Server = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" {...p}>
    <rect x="3" y="4" width="18" height="7" rx="1.5" />
    <rect x="3" y="13" width="18" height="7" rx="1.5" />
    <circle cx="7" cy="7.5" r="0.6" fill="currentColor" />
    <circle cx="7" cy="16.5" r="0.6" fill="currentColor" />
  </svg>
)

/**
 * Shield icon component.
 */
export const Shield = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M12 3 4 6v6c0 4.5 3.4 8.4 8 9 4.6-.6 8-4.5 8-9V6l-8-3z" />
  </svg>
)

/**
 * Capacity icon component.
 */
export const Capacity = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M3 17a9 9 0 0 1 18 0" />
    <path d="m12 17 5-6" />
    <circle cx="12" cy="17" r="1.4" fill="currentColor" stroke="none" />
  </svg>
)

/**
 * Chevron icon component.
 */
export const Chevron = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="m9 6 6 6-6 6" />
  </svg>
)

/**
 * ChevronDown icon component.
 */
export const ChevronDown = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="m6 9 6 6 6-6" />
  </svg>
)

/**
 * Plus icon component.
 */
export const Plus = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" {...p}>
    <path d="M12 5v14M5 12h14" />
  </svg>
)

/**
 * Check icon component.
 */
export const Check = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="m5 12 5 5L20 7" />
  </svg>
)

/**
 * Clock icon component.
 */
export const Clock = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5V12l3 2" />
  </svg>
)

/**
 * ArrowLeft icon component.
 */
export const ArrowLeft = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M15 6 9 12l6 6" />
  </svg>
)

/**
 * Copy icon component.
 */
export const Copy = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <rect x="8" y="8" width="12" height="12" rx="2" />
    <path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3" />
  </svg>
)

/**
 * Eye icon component.
 */
export const Eye = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" {...p}>
    <path d="M2.5 12C4 7.5 7.7 5 12 5s8 2.5 9.5 7c-1.5 4.5-5.2 7-9.5 7s-8-2.5-9.5-7z" />
    <circle cx="12" cy="12" r="2.6" />
  </svg>
)

/**
 * Bell icon component.
 */
export const Bell = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M6 8a6 6 0 0 1 12 0c0 6 2 7 2 7H4s2-1 2-7z" />
    <path d="M10 19a2 2 0 0 0 4 0" />
  </svg>
)

/**
 * Key icon component.
 */
export const Key = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <circle cx="8" cy="14" r="4" />
    <path d="M11 12.5 21 4l-2 2 1 1-2 2 1 1-2 2" />
  </svg>
)

/**
 * User icon component.
 */
export const User = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" {...p}>
    <circle cx="12" cy="8" r="4" />
    <path d="M3.5 20.5c1.5-4 4.7-6 8.5-6s7 2 8.5 6" />
  </svg>
)

/**
 * Logout icon component.
 */
export const Logout = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M16 17l5-5-5-5" />
    <path d="M21 12H10" />
    <path d="M14 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h8" />
  </svg>
)

/**
 * Moon icon component.
 */
export const Moon = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M21 13a8 8 0 1 1-9.5-9.5 6.5 6.5 0 0 0 9.5 9.5z" />
  </svg>
)

/**
 * Sun icon component.
 */
export const Sun = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4" />
  </svg>
)

/**
 * Sliders icon component.
 */
export const Sliders = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" {...p}>
    <path d="M4 6h8M16 6h4M4 12h4M12 12h8M4 18h12M20 18h0" />
    <circle cx="14" cy="6" r="2" />
    <circle cx="10" cy="12" r="2" />
    <circle cx="18" cy="18" r="2" />
  </svg>
)

/**
 * Lock icon component.
 */
export const Lock = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <rect x="4" y="11" width="16" height="10" rx="2" />
    <path d="M8 11V7a4 4 0 1 1 8 0v4" />
  </svg>
)

/**
 * Refresh icon component.
 */
export const Refresh = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M21 12a9 9 0 1 1-3-6.7L21 8" />
    <path d="M21 3v5h-5" />
  </svg>
)

/**
 * Database icon component.
 */
export const Database = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" {...p}>
    <ellipse cx="12" cy="5" rx="8" ry="3" />
    <path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5" />
    <path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
  </svg>
)

/**
 * Globe icon component.
 */
export const Globe = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M3.5 12h17M12 3.5a13 13 0 0 1 0 17M12 3.5a13 13 0 0 0 0 17" />
  </svg>
)

/**
 * Trash icon component.
 */
export const Trash = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" {...p}>
    <path d="M4 7h16M9 7V4h6v3M6 7l1 13a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-13" />
  </svg>
)

/**
 * Spark icon component.
 */
export const Spark = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6 8 8M16 16l2.4 2.4M5.6 18.4 8 16M16 8l2.4-2.4" />
  </svg>
)

/**
 * Flask icon component.
 */
export const Flask = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M9 3h6M10 3v6.5L4.5 19a1.5 1.5 0 0 0 1.3 2.3h12.4a1.5 1.5 0 0 0 1.3-2.3L14 9.5V3" />
    <path d="M7 15h10" />
  </svg>
)

/**
 * Chat icon component.
 */
export const Chat = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M21 12a8.5 8.5 0 0 1-12 7.7L4 21l1.3-4A8.5 8.5 0 1 1 21 12z" />
  </svg>
)

/**
 * Send icon component.
 */
export const Send = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M21 3 11 13M21 3l-7 18-4-8-8-4 18-6z" />
  </svg>
)

/**
 * Play icon component.
 */
export const Play = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="currentColor" {...p}>
    <path d="M7 5v14l12-7L7 5z" />
  </svg>
)

/**
 * Download icon component.
 */
export const Download = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M12 3v12M6 11l6 6 6-6M4 21h16" />
  </svg>
)

/**
 * X icon component.
 */
export const X = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" {...p}>
    <path d="M6 6l12 12M18 6L6 18" />
  </svg>
)

/**
 * Pause icon component.
 */
export const Pause = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="currentColor" {...p}>
    <rect x="6" y="4" width="4" height="16" rx="1" />
    <rect x="14" y="4" width="4" height="16" rx="1" />
  </svg>
)

/**
 * Filter icon component.
 */
export const Filter = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z" />
  </svg>
)

