/**
 * AuthPill Component
 *
 * Displays the current authentication status of the MCP server connection.
 * Shows different states (disconnected, authorizing, connected) with appropriate
 * colors and animations, and provides a logout action when connected.
 */

import type { FC } from "react"
import { cn } from "@/lib/utils"
import { motion, AnimatePresence } from "framer-motion"
import { useAuthState } from "@/store"
import { useMotionConfig } from "@/hooks/useMotion"
import { useLogout } from "@/hooks/useLogout"

export interface AuthPillProps {
  className?: string
}

const statusLabel: Record<string, string> = {
  disconnected: "Disconnected",
  authorizing: "Authorizing…",
  connected: "Connected",
}

const statusColor: Record<string, string> = {
  disconnected: "bg-muted text-muted-foreground",
  authorizing: "bg-yellow-500/20 text-yellow-400",
  connected: "bg-green-500/20 text-green-400",
}

/**
 * AuthPill component that renders a status indicator pill.
 */
const AuthPill: FC<AuthPillProps> = ({ className }) => {
  const { status, subject } = useAuthState()
  const { logout } = useLogout()
  const { fadeUp } = useMotionConfig()

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={status}
        className={cn(
          "inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-medium",
          statusColor[status],
          className,
        )}
        {...fadeUp}
      >
        <span
          className={cn(
            "h-1.5 w-1.5 rounded-full",
            status === "connected" && "bg-green-400",
            status === "authorizing" && "bg-yellow-400 animate-pulse",
            status === "disconnected" && "bg-muted-foreground",
          )}
        />
        <span>{status === "connected" && subject ? subject : statusLabel[status]}</span>
        {status === "connected" && (
          <button
            type="button"
            onClick={logout}
            className="ml-1 underline underline-offset-2 hover:no-underline"
          >
            Logout
          </button>
        )}
      </motion.div>
    </AnimatePresence>
  )
}

/**
 * AuthPill Component.
 * Renders the UI and handles state for the AuthPill feature.
 */
export default AuthPill
