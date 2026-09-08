import { useNavigate } from "@tanstack/react-router"
import AppShell from "@/components/shell/AppShell"
import LlmRagSection from "@/components/config/LlmRagSection"
import GraphModelSection from "@/components/config/GraphModelSection"
import StepModelSection from "@/components/config/StepModelSection"
import ModelRoleSection from "@/components/config/ModelRoleSection"
import PluginGateSection from "@/components/config/PluginGateSection"
import TotpSetupSection from "@/components/config/TotpSetupSection"
import { useTotpStatusQuery } from "@/hooks/useTotp"
import { useGatewayConfigQuery, useSaveGatewayConfigMutation } from "@/hooks/useConfig"

type Section = "Server" | "LLM & RAG" | "Graph Models" | "Step Models" | "Model Roles" | "Plugin Gates" | "Security" | "TOTP Setup" | "Plugin defaults" | "Gateway" | "Danger zone"

const SECTIONS: Section[] = ["Server", "LLM & RAG", "Graph Models", "Step Models", "Model Roles", "Plugin Gates", "Security", "TOTP Setup", "Plugin defaults", "Gateway", "Danger zone"]

/**
 * Search parameters for deep linking into specific config page sections and navigation returns.
 */
export type ConfigPageSearch = {
  section?: string
  returnTo?: "/terminal"
}

/**
 * System Config page body. Kept out of the route module so TanStack only
 * exports `Route` from `routes/config.tsx`.
 */
export function ConfigPage({ section: searchSection, returnTo }: ConfigPageSearch = {}) {
  const navigate = useNavigate()
  const { data: totpData } = useTotpStatusQuery()
  const totpProvisioned = totpData?.totp_provisioned

  const section = (SECTIONS.includes(searchSection as Section) ? searchSection : "Server") as Section

  const setSection = (s: Section) => {
    void navigate({ to: "/config", search: { section: s, returnTo }, replace: true })
  }

  const { data: gw, isPending: gwPending } = useGatewayConfigQuery()
  const { mutate: saveGw, isPending: gwSaving } = useSaveGatewayConfigMutation()
  const gwEnabled = gw?.run_graph_unified ?? false

  return (
    <AppShell active="config">
      <div className="ct-page-head">
        <div>
          <div className="ct-page-title">System Config</div>
          <div className="ct-page-sub">Server-wide configuration. Changes apply to all plugins.</div>
        </div>
      </div>

      {totpProvisioned === false && (
        <div style={{ display: "flex", gap: 12, padding: "12px 16px", background: "var(--warn-soft)", color: "var(--warn)", border: "1px solid var(--warn)", borderRadius: "var(--radius)", marginBottom: 20, fontSize: 14 }}>
          <span>⚠️</span>
          <div>
            <strong>Terminal access requires TOTP.</strong> Configure a Time-based One-Time Password below to use the Terminal page.
          </div>
        </div>
      )}

      <div className="ct-config-grid">
        <nav className="ct-config-side">
          {SECTIONS.map((s) => (
            <a
              key={s}
              onClick={() => setSection(s)}
              className={section === s ? "is-active" : ""}
              style={{ cursor: "pointer" }}
            >
              {s}
            </a>
          ))}
        </nav>

        <div>
          {section === "Server" && (
            <div className="ct-form-section">
              <h3>Server</h3>
              <p className="sub">Runtime and transport configuration.</p>
              <div className="ct-form-row">
                <div>
                  <div className="ct-field">
                    <label>MCP Server URL</label>
                    <input className="ct-input" defaultValue="http://localhost:10000" readOnly />
                  </div>
                </div>
                <div>
                  <div className="ct-field">
                    <label>Transport Mode</label>
                    <input className="ct-input" defaultValue="HTTP" readOnly />
                  </div>
                </div>
              </div>
              <div className="ct-form-row">
                <div>
                  <div className="ct-field">
                    <label>Host</label>
                    <input className="ct-input" defaultValue="0.0.0.0" readOnly />
                  </div>
                </div>
                <div>
                  <div className="ct-field">
                    <label>Port</label>
                    <input className="ct-input" defaultValue="10000" readOnly />
                  </div>
                </div>
              </div>
            </div>
          )}

          {section === "LLM & RAG" && <LlmRagSection />}
          {section === "Graph Models" && <GraphModelSection />}
          {section === "Step Models" && <StepModelSection />}
          {section === "Model Roles" && <ModelRoleSection />}
          {section === "Plugin Gates" && <PluginGateSection />}

          {section === "Security" && (
            <div className="ct-form-section">
              <h3>Security</h3>
              <p className="sub">OAuth and authentication settings.</p>
              <div className="ct-checkbox-row">
                <div>
                  <div className="label">OAuth Layer 1 (Inbound)</div>
                  <div className="desc">Require RS256 JWT for all MCP connections. Active when OAUTH_ENABLED=true.</div>
                </div>
                <button className="ct-switch is-on" aria-label="OAuth Layer 1" role="switch" aria-checked={true} />
              </div>
              <div className="ct-checkbox-row">
                <div>
                  <div className="label">OAuth Layer 2 (Outbound)</div>
                  <div className="desc">Per-plugin external OAuth relay via VaultService.</div>
                </div>
                <button className="ct-switch is-on" aria-label="OAuth Layer 2" role="switch" aria-checked={true} />
              </div>
            </div>
          )}

          {section === "TOTP Setup" && <TotpSetupSection />}

          {section === "Plugin defaults" && (
            <div className="ct-form-section">
              <h3>Plugin Defaults</h3>
              <p className="sub">Default tier and pagination limits.</p>
              <div className="ct-form-row">
                <div>
                  <div className="ct-field">
                    <label>System Tier</label>
                    <input className="ct-input" defaultValue="free" readOnly />
                  </div>
                </div>
                <div>
                  <div className="ct-field">
                    <label>Default Page Size</label>
                    <input className="ct-input" defaultValue="20" readOnly />
                  </div>
                </div>
              </div>
            </div>
          )}

          {section === "Gateway" && (
            <div className="ct-form-section">
              <h3>Gateway Mode</h3>
              <p className="sub">When enabled, only run_graph (+ discover/auth) is visible to MCP clients. All other tools are hidden from direct discovery but remain fully usable via run_graph. Changes apply live.</p>
              <div className="ct-checkbox-row">
                <div>
                  <div className="label">run_graph_unified</div>
                  <div className="desc">Hide all non-allowlisted tools from MCP list_tools/call_tool. Hidden tools stay reachable internally via run_graph. (persisted, live)</div>
                </div>
                <button
                  className={"ct-switch " + (gwEnabled ? "is-on" : "")}
                  disabled={gwPending || gwSaving}
                  onClick={() => saveGw({ run_graph_unified: !gwEnabled })}
                  role="switch"
                  aria-checked={gwEnabled}
                  aria-label="Toggle gateway unified mode"
                />
              </div>
            </div>
          )}

          {section === "Danger zone" && (
            <div className="ct-form-section is-danger">
              <h3>Danger Zone</h3>
              <p className="sub">Irreversible actions. The cat will be sad.</p>
              <div className="ct-checkbox-row">
                <div>
                  <div className="label" style={{ color: "var(--danger)" }}>Reset all plugin credentials</div>
                  <div className="desc">Wipes the VaultService store. All Layer-2 tokens will be revoked.</div>
                </div>
                <button className="ct-btn-danger">Reset vault</button>
              </div>
            </div>
          )}
        </div>
      </div>
      <div style={{ height: 12 }} />
    </AppShell>
  )
}
