/**
 * MethodTabs Component
 *
 * Two-way tablist letting the operator pick between OAuth and Direct Login
 * as the active Layer-2 connection method on the Connect screen.
 */
import { useRef, type FC, type KeyboardEvent } from "react"

export type ConnectMethod = "oauth" | "direct"

export interface MethodTabsProps {
  method: ConnectMethod
  onChange: (method: ConnectMethod) => void
  oauthOn: boolean
  directOn: boolean
  oauthEnabled: boolean
}

const TABS: Array<{ id: ConnectMethod; label: string; hint: string }> = [
  { id: "oauth", label: "OAuth", hint: "layer 2 · recommended" },
  { id: "direct", label: "Direct Login", hint: "layer 2 · user · token" },
]

/**
 * MethodTabs Component.
 * Renders the OAuth / Direct Login method tablist and per-method "on" pills.
 */
const MethodTabs: FC<MethodTabsProps> = ({ method, onChange, oauthOn, directOn, oauthEnabled }) => {
  const visibleTabs = oauthEnabled ? TABS : TABS.filter((t) => t.id === "direct")
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([])

  function activateAt(index: number) {
    const next = visibleTabs[index]
    if (!next) return
    onChange(next.id)
    tabRefs.current[index]?.focus()
  }

  function handleKeyDown(e: KeyboardEvent<HTMLButtonElement>, index: number) {
    const last = visibleTabs.length - 1
    if (e.key === "ArrowRight") {
      e.preventDefault()
      activateAt((index + 1) % visibleTabs.length)
    } else if (e.key === "ArrowLeft") {
      e.preventDefault()
      activateAt((index - 1 + visibleTabs.length) % visibleTabs.length)
    } else if (e.key === "Home") {
      e.preventDefault()
      activateAt(0)
    } else if (e.key === "End") {
      e.preventDefault()
      activateAt(last)
    }
  }

  return (
    <div
      role="tablist"
      aria-label="auth method"
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(${visibleTabs.length}, 1fr)`,
        padding: 3,
        gap: 3,
        background: "var(--bg-sunken)",
        border: "1px solid var(--hairline)",
        borderRadius: 12,
      }}
    >
      {visibleTabs.map((t, i) => {
        const active = method === t.id
        const on = t.id === "oauth" ? oauthOn : directOn
        return (
          <button
            key={t.id}
            ref={(el) => {
              tabRefs.current[i] = el
            }}
            type="button"
            role="tab"
            aria-selected={active}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(t.id)}
            onKeyDown={(e) => handleKeyDown(e, i)}
            style={{
              position: "relative",
              padding: "9px 12px",
              borderRadius: 9,
              background: active ? "var(--card)" : "transparent",
              border: active ? "1px solid var(--border)" : "1px solid transparent",
              boxShadow: active
                ? `0 1px 0 0 color-mix(in oklch, var(--bg) 60%, transparent), 0 0 0 1px color-mix(in oklch, var(--amber) 18%, transparent)`
                : "none",
              color: active ? "var(--fg)" : "var(--fg-muted)",
              cursor: "pointer",
              display: "flex",
              flexDirection: "column",
              alignItems: "flex-start",
              gap: 2,
              textAlign: "left",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8, width: "100%" }}>
              <span style={{ fontSize: 13, fontWeight: active ? 600 : 500, letterSpacing: "-0.005em" }}>
                {t.label}
              </span>
              {on && (
                <span
                  style={{
                    marginLeft: "auto",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 4,
                    padding: "1px 7px",
                    borderRadius: 999,
                    fontFamily: "var(--font-mono)",
                    fontSize: 9,
                    letterSpacing: "0.16em",
                    textTransform: "uppercase",
                    color: "var(--neon)",
                    background: "color-mix(in oklch, var(--neon) 10%, transparent)",
                    border: "1px solid color-mix(in oklch, var(--neon) 35%, var(--border))",
                  }}
                >
                  <span
                    style={{
                      width: 5,
                      height: 5,
                      borderRadius: 999,
                      background: "var(--neon)",
                      boxShadow: "0 0 6px var(--neon)",
                    }}
                  />
                  on
                </span>
              )}
            </div>
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 9.5,
                letterSpacing: "0.16em",
                textTransform: "uppercase",
                color: active ? "var(--amber)" : "var(--fg-subtle)",
              }}
            >
              {t.hint}
            </span>
          </button>
        )
      })}
    </div>
  )
}

export default MethodTabs
