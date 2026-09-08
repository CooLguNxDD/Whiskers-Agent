import type { ErrorComponentProps } from "@tanstack/react-router"
import type { FC } from "react"

/**
 * RouteErrorFallback — route-pane-scoped fallback component for TanStack Router.
 * Reuses ErrorBoundary visual styling but renders inside the route pane instead of blanking the whole app.
 */
export const RouteErrorFallback: FC<ErrorComponentProps> = ({ error, reset }) => {
  return (
    <div
      role="alert"
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 12,
        padding: 32,
        fontFamily: "var(--font-mono, ui-monospace, monospace)",
        color: "var(--fg)",
        minHeight: "300px",
        width: "100%",
      }}
    >
      <div style={{ fontSize: 16, fontWeight: 600, color: "var(--danger)" }}>
        Something went wrong in this view
      </div>
      <div style={{ fontSize: 12, opacity: 0.7, maxWidth: 480, textAlign: "center", whiteSpace: "pre-wrap" }}>
        {error instanceof Error ? error.message : String(error || "An unexpected error occurred.")}
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        {reset && (
          <button
            type="button"
            className="ct-btn-primary"
            onClick={() => reset()}
          >
            Retry
          </button>
        )}
        <button
          type="button"
          className="ct-btn-ghost"
          onClick={() => window.location.reload()}
        >
          Reload Page
        </button>
      </div>
    </div>
  )
}

export default RouteErrorFallback
