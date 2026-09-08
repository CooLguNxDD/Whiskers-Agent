/**
 * ThemeProvider Component
 *
 * Manages theme registry context and applies CSS variables for selected themes.
 * Listens to preference changes in Zustand and injects corresponding variables to root element.
 */

import { useEffect, type ReactNode } from "react"
import { usePreferencesStore } from "@/store"
import { themeRegistry, DEFAULT_THEME_ID } from "@/themes/registry"
import { ThemeContext } from "@/themes/theme-context"

/**
 * Renders the theme provider component wrapping ReactNode children.
 * Runs an effect to apply CSS variables from the selected theme to the root element.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const themeId = usePreferencesStore((s) => s.theme)

  useEffect(() => {
    // Falls back to DEFAULT_THEME_ID if the requested theme doesn't exist in registry
    const def = themeRegistry[themeId] ?? themeRegistry[DEFAULT_THEME_ID]
    if (!def) return
    const root = document.documentElement
    root.setAttribute("data-light", def.isLight ? "true" : "false")
    // Injects individual theme variables as CSS properties on root
    for (const [key, value] of Object.entries(def.vars)) {
      root.style.setProperty(`--${key}`, value)
    }
  }, [themeId])

  return (
    <ThemeContext.Provider value={{ registry: themeRegistry }}>
      {children}
    </ThemeContext.Provider>
  )
}
