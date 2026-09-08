import { createFileRoute } from "@tanstack/react-router"
import { ConfigPage } from "@/components/config/ConfigPage"

/** `/config` route: `?section=` deep-links a settings pane; `returnTo=/terminal` is the TOTP hop. */
export const Route = createFileRoute("/config")({
  validateSearch: (s: Record<string, unknown>) => ({
    section: typeof s.section === "string" ? s.section : undefined,
    returnTo: s.returnTo === "/terminal" ? "/terminal" as const : undefined,
  }),
  component: function ConfigRoute() {
    const search = Route.useSearch()
    return <ConfigPage section={search.section} returnTo={search.returnTo} />
  },
})
