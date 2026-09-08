/**
 * useLogout Hook
 *
 * Handles the execution and state cleanup for user logout.
 */
import { useNavigate } from "@tanstack/react-router"
import { useSessionStore } from "@/store"

/**
 * Custom hook to handle user logout.
 * Deduplicates logout logic across the application.
 */
export function useLogout() {
  const navigate = useNavigate()
  const dispatch = useSessionStore((s) => s.dispatch)
  const clearMcpState = useSessionStore((s) => s.clearMcpState)

  async function logout() {
    try {
      await fetch("/api/admin/public/logout", { method: "POST", credentials: "include" })
    } finally {
      dispatch({ type: "disconnect" })
      clearMcpState()
      void navigate({ to: "/login", search: { state: "", next: "" } })
    }
  }

  return { logout }
}

