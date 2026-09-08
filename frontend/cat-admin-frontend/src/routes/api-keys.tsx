import { createFileRoute } from "@tanstack/react-router"
import { ApiKeysPage } from "@/components/ApiKeys"

/** `/api-keys` route: mounts `ApiKeysPage` (list/create/revoke keys + the scope configurator view). */
export const Route = createFileRoute("/api-keys")({
  component: ApiKeysPage,
})
