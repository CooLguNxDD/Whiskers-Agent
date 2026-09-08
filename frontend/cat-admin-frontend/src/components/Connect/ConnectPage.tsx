import { useEffect } from "react"
import { useNavigate } from "@tanstack/react-router"
import { useShallow } from "zustand/react/shallow"
import { usePreferencesStore, selectThemeAttrs, useSessionStore, useMcpState } from "@/store"
import { usePluginsQuery, usePluginHealthQuery } from "@/hooks/usePlugins"
import ConnectScreen from "@/components/Connect/ConnectScreen"
import { Route } from "@/routes/connect"

/**
 * Layer 2 OAuth connection page, driven by `plugin_id` and `provider` search params.
 * Renders the ct-themed full-page ConnectScreen outside the AppShell.
 */
export function ConnectPage() {
  const { oauth_success, plugin_id, provider, state: urlState } = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  const mcpState = useMcpState()
  const setMcpState = useSessionStore((s) => s.setMcpState)

  useEffect(() => {
    if (urlState) {
      setMcpState(urlState)
      void navigate({ search: (prev) => ({ ...prev, state: "" }), replace: true })
    }
  }, [urlState, setMcpState, navigate])

  const state = mcpState || urlState || ""
  const themeAttrs = usePreferencesStore(useShallow(selectThemeAttrs))
  const { data: pluginsData } = usePluginsQuery()
  const { data: health } = usePluginHealthQuery(plugin_id)
  const plugin = pluginsData?.plugins.find((p) => p.id === plugin_id)

  const layer2OauthEnabled = plugin?.layer2_oauth_enabled ?? true
  const oauthConnected = oauth_success !== "" || plugin?.oauth_status?.[provider] === "connected"
  const directConnected = health?.credentials_present ?? false
  const hasReturnState = !!state

  return (
    <div className="ct-root ct-login" {...themeAttrs}>
      <ConnectScreen
        pluginId={plugin_id}
        provider={provider}
        layer2OauthEnabled={layer2OauthEnabled}
        oauthConnected={oauthConnected}
        directConnected={directConnected}
        hasReturnState={hasReturnState}
        state={state}
        onOauthRevoked={() => {
          void navigate({
            to: "/connect",
            search: { plugin_id, provider, oauth_success: "", state: urlState || "" },
            replace: true,
          })
        }}
      />
    </div>
  )
}
