import { createFileRoute } from "@tanstack/react-router"
import { LoginPage } from "@/components/login/LoginPage"

/**
 * Route definition for /login.
 * Registers the LoginPage component for the /login route.
 */
export const Route = createFileRoute("/login")({
  validateSearch: (s: Record<string, unknown>) => ({
    state: typeof s.state === "string" && s.state.length <= 500 ? s.state : "",
    next: typeof s.next === "string" ? s.next : "",
  }),
  component: LoginPage,
})
