import { createFileRoute } from "@tanstack/react-router"
import AppShell from "@/components/shell/AppShell"
import { Check, Bell } from "@/components/shell/Icons"
import { usePreferencesStore, type Accent, type Density } from "@/store"
import { useThemeRegistry } from "@/hooks/useThemeRegistry"
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select"

// Swatch preview colors come from ct-theme.css's --accent-* tokens — same single source
// of truth the live [data-accent] overrides read from, so picker and applied color never drift apart.
const ACCENTS: Accent[] = ["amber", "pink", "neon", "cyan", "violet"]


/**
 * Preferences route configuration.
 */
export const Route = createFileRoute("/preferences")({
  component: function PreferencesPage() {
    const theme = usePreferencesStore((s) => s.theme)
    const accent = usePreferencesStore((s) => s.accent)
    const density = usePreferencesStore((s) => s.density)
    const notifs = usePreferencesStore((s) => s.notifications)
    const setTheme = usePreferencesStore((s) => s.setTheme)
    const setAccent = usePreferencesStore((s) => s.setAccent)
    const setDensity = usePreferencesStore((s) => s.setDensity)
    const toggleNotification = usePreferencesStore((s) => s.toggleNotification)

    const { registry } = useThemeRegistry()
    const defaultThemes = Object.values(registry).filter((t) => t.default)
    const customThemes = Object.values(registry).filter((t) => !t.default)

    return (
      <AppShell active="preferences">
        <div className="ct-page-head">
          <div>
            <div className="ct-page-title">Preferences</div>
            <div className="ct-page-sub">Appearance and notifications.</div>
          </div>
        </div>

        {/* Appearance */}
        <div className="ct-section" style={{ marginBottom: 16 }}>
          <div className="ct-section-head">
            <div>
              <div className="ct-section-title">Appearance</div>
              <div className="ct-section-sub">Surface theme, accent colour, density.</div>
            </div>
          </div>
          <div style={{ padding: "20px 22px" }}>
            <div className="ct-eyebrow" style={{ marginBottom: 10 }}>Surface</div>
            <div className="ct-theme-grid" style={{ marginBottom: 20 }}>
              {defaultThemes.map((themeDef) => {
                const bg = themeDef.vars.bg
                const card = themeDef.vars.card
                const accentColor = themeDef.vars.amber // Swatch accent dot uses vars.amber
                return (
                  <button key={themeDef.id} className={"ct-theme" + (theme === themeDef.id ? " is-active" : "")} onClick={() => setTheme(themeDef.id)}>
                    <div className="ct-theme-swatch" style={{ background: bg }}>
                      <div style={{ position: "absolute", top: 10, left: 10, right: 10, height: 18, borderRadius: 4, background: card }} />
                      <div style={{ position: "absolute", bottom: 10, left: 10, width: 40, height: 6, borderRadius: 3, background: accentColor }} />
                    </div>
                    <div className="ct-theme-check"><Check width="10" height="10" /></div>
                    <div className="ct-theme-name">{themeDef.label}</div>
                    <div className="ct-theme-sub">{themeDef.description}</div>
                  </button>
                )
              })}
            </div>

            <div className="ct-eyebrow" style={{ marginBottom: 10, marginTop: 16 }}>Custom theme</div>
            {customThemes.length > 0 ? (
              <Select value={customThemes.some((t) => t.id === theme) ? theme : undefined} onValueChange={(v) => setTheme(v)}>
                <SelectTrigger style={{ width: 240 }}>
                  <SelectValue placeholder="Choose a custom theme…" />
                </SelectTrigger>
                <SelectContent>
                  {customThemes.map((t) => (
                    <SelectItem key={t.id} value={t.id}>
                      {t.label}{t.description ? ` — ${t.description}` : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <div className="ct-page-sub">No custom themes installed. Drop a `*.theme.json` file into `src/themes/` to add one.</div>
            )}

            <div className="ct-eyebrow" style={{ marginBottom: 10 }}>Accent</div>
            <div style={{ display: "flex", gap: 10, marginBottom: 20 }}>
              {ACCENTS.map((a) => {
                const color = `var(--accent-${a})`
                return (
                  <button
                    key={a}
                    onClick={() => setAccent(a)}
                    title={a}
                    style={{
                      width: 32, height: 32, borderRadius: "50%",
                      background: color,
                      border: accent === a ? "2.5px solid var(--fg)" : "2.5px solid transparent",
                      outline: accent === a ? `2px solid ${color}` : "none",
                      outlineOffset: 2,
                      boxShadow: accent === a ? `0 0 12px ${color}` : "none",
                      cursor: "pointer",
                    }}
                  />
                )
              })}
            </div>

            <div className="ct-eyebrow" style={{ marginBottom: 10 }}>Density</div>
            <div style={{ display: "flex", gap: 8 }}>
              {(["comfortable", "compact"] as Density[]).map((d) => (
                <button key={d} className={"ct-chip" + (density === d ? " is-active" : "")} onClick={() => setDensity(d)}>{d}</button>
              ))}
            </div>
          </div>
        </div>

        {/* Notifications */}
        <div className="ct-section" style={{ marginBottom: 16 }}>
          <div className="ct-section-head">
            <div>
              <div className="ct-section-title" style={{ gap: 8 }}>
                <Bell width="14" height="14" style={{ color: "var(--amber)" }} />
                Notifications
              </div>
              <div className="ct-section-sub">Choose what events trigger alerts.</div>
            </div>
          </div>
          <div>
            {([
              { key: "errors" as const, label: "Error alerts",  desc: "Plugin errors, rate-limit warnings, and 5xx responses." },
              { key: "health" as const, label: "Health events", desc: "Plugin status changes, restart events, heartbeat failures." },
              { key: "auth"   as const, label: "Auth events",   desc: "New MCP client registrations and token expirations." },
              { key: "digest" as const, label: "Daily digest",  desc: "Summary email: top tools, error rate, active sessions." },
            ] as const).map(({ key, label, desc }) => (
              <div key={key} className="ct-checkbox-row" style={{ padding: "14px 22px" }}>
                <div style={{ flex: 1 }}>
                  <div className="label">{label}</div>
                  <div className="desc">{desc}</div>
                </div>
                <button
                  className={"ct-switch " + (notifs[key] ? "is-on" : "")}
                  role="switch"
                  aria-checked={notifs[key]}
                  onClick={() => toggleNotification(key)}
                  aria-label={label}
                />
              </div>
            ))}
          </div>
        </div>

        <div style={{ height: 12 }} />
      </AppShell>
    )
  },
})
