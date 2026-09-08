/**
 * Theme Context
 *
 * Holds the resolved theme registry for consumers (e.g. the theme selector UI).
 * Split into its own module so ThemeProvider.tsx and useThemeRegistry.ts can each
 * stay component-only / hook-only for Vite fast-refresh.
 */
import { createContext } from "react"
import type { ThemeDef } from "./registry"

export interface ThemeContextValue {
  registry: Record<string, ThemeDef>
}

/** React context carrying the resolved theme registry; `null` outside a `ThemeProvider`. See file header. */
export const ThemeContext = createContext<ThemeContextValue | null>(null)
