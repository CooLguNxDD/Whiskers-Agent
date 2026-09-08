/**
 * ErrorBoundary — top-level React error boundary for uncaught render errors.
 * Renders a simple fallback UI instead of blanking the whole app.
 */

import { Component, type ErrorInfo, type ReactNode } from "react"

interface ErrorBoundaryProps {
  children: ReactNode
  fallback?: ReactNode
}

interface ErrorBoundaryState {
  hasError: boolean
  error: Error | null
}

/**
 * Class component boundary: catches render errors via componentDidCatch.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false, error: null }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("ErrorBoundary caught render error:", error, info.componentStack)
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback
      return (
        <div
          role="alert"
          style={{
            minHeight: "100vh",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 12,
            padding: 24,
            fontFamily: "var(--font-mono, ui-monospace, monospace)",
            background: "var(--bg)",
            color: "var(--fg)",
          }}
        >
          <div style={{ fontSize: 18, fontWeight: 600 }}>Something went wrong</div>
          <div style={{ fontSize: 13, opacity: 0.7, maxWidth: 480, textAlign: "center" }}>
            {this.state.error?.message || "An unexpected error occurred."}
          </div>
          <button
            type="button"
            className="ct-btn-primary"
            onClick={() => window.location.reload()}
            style={{ marginTop: 8 }}
          >
            Reload
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

export default ErrorBoundary
