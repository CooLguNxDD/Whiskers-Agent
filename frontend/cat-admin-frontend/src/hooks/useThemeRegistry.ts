/**
 * useThemeRegistry Hook
 *
 * Accesses the resolved theme registry from ThemeContext.
 */
import { useContext } from "react"
import { ThemeContext, type ThemeContextValue } from "@/themes/theme-context"

/**
 * Accesses the theme registry from the ThemeContext.
 * Throws an error if used outside of a ThemeProvider.
 */
export function useThemeRegistry(): ThemeContextValue {
  const ctx = useContext(ThemeContext)
  if (!ctx) {
    throw new Error("useThemeRegistry must be used within a ThemeProvider")
  }
  return ctx
}
