import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

/**
 * Merges Tailwind class names, resolving conflicting utility classes in favor of the last one.
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
