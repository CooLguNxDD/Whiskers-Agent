/**
 * AppShell Component
 *
 * The primary layout wrapper for the admin dashboard.
 * Manages the sidebar, top navigation, main content area, and global live log terminal.
 */

import type { ReactNode } from "react"
import { useEffect } from "react"
import { useRouter } from "@tanstack/react-router"
import { useShallow } from "zustand/react/shallow"
import { useAuthState, usePreferencesStore, selectThemeAttrs } from "@/store"
import { useLogout } from "@/hooks/useLogout"
import { useCatalogQuery } from "@/hooks/useCatalog"
import Sidebar from "./Sidebar"
import TopBar from "./TopBar"
import LiveLogs from "./LiveLogs"

interface McpReturn {
  client: string
  returnUrl: string
}

interface AppShellProps {
  active: string
  children: ReactNode
  mcpReturn?: McpReturn | null
}

const NAV_TO_PATH: Record<string, string> = {
  console:    "/",
  plugins:    "/plugins",
  proxies:    "/proxies",
  terminal:   "/terminal",
  playground: "/playground",
  analytics:  "/analytics",
  config:     "/config",
  "api-keys": "/api-keys",
  preferences:"/preferences",
  "reset-password": "/login",
}

/**
 * Renders the application shell with side navigation and top bar.
 */
export default function AppShell({ active, children, mcpReturn }: AppShellProps) {
  const router = useRouter()
  const { logout } = useLogout()
  const themeAttrs = usePreferencesStore(useShallow(selectThemeAttrs))
  const { status } = useAuthState()
  const connected = status === "connected"
  // Prefetch full host+plugin OperationCatalog for callCatalogOp path resolution
  useCatalogQuery()

  function handleNav(id: string) {
    const path = NAV_TO_PATH[id]
    if (path) void router.navigate({ to: path })
  }

  useEffect(() => {
    const ids = ["console", "plugins", "proxies", "terminal", "playground", "analytics", "config", "api-keys"]
    function onKey(e: KeyboardEvent) {
      if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return
      const t = e.target as HTMLElement | null
      if (t?.closest("input, textarea, select, [contenteditable=true]")) return
      if (e.key === "k" || e.key === "K") return
      if (e.key === ",") {
        e.preventDefault()
        handleNav("preferences")
        return
      }
      const n = Number(e.key)
      if (n >= 1 && n <= ids.length) {
        e.preventDefault()
        handleNav(ids[n - 1])
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [router])

  return (
    <div className="ct-root ct-app" {...themeAttrs}>
      <Sidebar
        active={active}
        onNav={handleNav}
        connected={connected}
        mcpReturn={mcpReturn}
        onReconnect={() =>
          void router.navigate({
            to: "/login",
            search: { state: "", next: router.state.location.pathname },
          })
        }
      />
      <TopBar onNav={handleNav} onLogout={logout} />
      <main className={"ct-main" + (active === "terminal" ? " ts-main" : "")}>{children}</main>
      <LiveLogs />
    </div>
  )
}
