/**
 * DisconnectedState Component
 *
 * Renders a fallback view when the admin session is not active.
 */

import type { FC } from "react"
import { cn } from "@/lib/utils"
import { motion } from "framer-motion"
import { useMotionConfig } from "@/hooks/useMotion"
import { Button } from "@/components/ui/button"

export interface DisconnectedStateProps {
  className?: string
}

/**
 * Renders the disconnected state view with a login redirect button.
 */
const DisconnectedState: FC<DisconnectedStateProps> = ({ className }) => {
  const { fadeUp } = useMotionConfig()

  return (
    <motion.div
      className={cn(
        "flex min-h-[60vh] flex-col items-center justify-center gap-6 text-center",
        className,
      )}
      {...fadeUp}
    >
      <div className="space-y-2">
        <h2 className="text-xl font-semibold tracking-tight">Not connected</h2>
        <p className="max-w-sm text-sm text-muted-foreground">
          Your session has expired. Please log in to continue.
        </p>
      </div>

      <Button onClick={() => { window.location.href = "/login" }} size="lg">
        Go to Login
      </Button>
    </motion.div>
  )
}

/**
 * DisconnectedState Component.
 * Renders the UI and handles state for the DisconnectedState feature.
 */
export default DisconnectedState
