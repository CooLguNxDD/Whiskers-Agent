/**
 * SuccessBanner Component
 *
 * Full-width banner shown at the top of the Connect screen once the active
 * method (OAuth or Direct Login) is authorized, with an optional "return to
 * MCP client" call to action and a dismiss control.
 */
import type { FC } from "react"
import { Check, X } from "@/components/shell/Icons"
import type { ProviderMeta } from "./providers"

export interface SuccessBannerProps {
  isOauth: boolean
  providerMeta: ProviderMeta
  pluginId: string
  hasReturnState: boolean
  finishHref: string
  onDismiss: () => void
}

/**
 * SuccessBanner Component.
 * Renders the slide-in "authorization complete" / "credentials sealed" status bar.
 */
const SuccessBanner: FC<SuccessBannerProps> = ({
  isOauth,
  providerMeta,
  pluginId,
  hasReturnState,
  finishHref,
  onDismiss,
}) => {
  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        right: 0,
        display: "flex",
        alignItems: "center",
        gap: 14,
        padding: "12px 20px 12px 18px",
        background:
          "linear-gradient(90deg, color-mix(in oklch, var(--neon) 18%, var(--bg-sunken)) 0%, color-mix(in oklch, var(--neon) 7%, var(--bg-sunken)) 55%, var(--bg-sunken) 100%)",
        borderBottom: "1px solid color-mix(in oklch, var(--neon) 35%, var(--border))",
        boxShadow:
          "0 1px 0 0 color-mix(in oklch, var(--neon) 18%, transparent), 0 14px 40px -22px color-mix(in oklch, var(--neon) 70%, transparent)",
        animation: "ct-banner-in 360ms cubic-bezier(.2,.7,.2,1) both",
        zIndex: 3,
      }}
    >
      <div
        style={{
          position: "relative",
          width: 28,
          height: 28,
          borderRadius: 999,
          background: "color-mix(in oklch, var(--neon) 22%, var(--bg))",
          border: "1px solid color-mix(in oklch, var(--neon) 55%, var(--border))",
          color: "var(--neon)",
          display: "grid",
          placeItems: "center",
          flex: "0 0 auto",
          animation: "ct-banner-pulse 2.4s ease-in-out infinite",
        }}
      >
        <Check width="14" height="14" />
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            fontFamily: "var(--font-mono)",
            fontSize: 10.5,
            letterSpacing: "0.18em",
            textTransform: "uppercase",
            color: "var(--neon)",
          }}
        >
          <span>{isOauth ? "authorization complete" : "credentials sealed"}</span>
          <span
            style={{
              padding: "1px 7px",
              borderRadius: 999,
              fontSize: 9.5,
              letterSpacing: "0.16em",
              border: "1px solid color-mix(in oklch, var(--neon) 35%, var(--border))",
              color: "var(--fg-muted)",
              background: "color-mix(in oklch, var(--neon) 8%, transparent)",
            }}
          >
            200 OK
          </span>
        </div>
        <div
          style={{
            fontSize: 13,
            color: "var(--fg)",
            letterSpacing: 0,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          <span style={{ color: "var(--fg)" }}>{providerMeta.name}</span>
          <span style={{ color: "var(--fg-subtle)" }}>
            {" "}
            {isOauth ? "token issued to" : "credentials sealed for"}{" "}
          </span>
          <span style={{ fontFamily: "var(--font-mono)", color: "var(--amber)" }}>{pluginId}</span>
          {isOauth && providerMeta.scopes.length > 0 && (
            <span style={{ color: "var(--fg-subtle)" }}>
              {" "}
              · {providerMeta.scopes.length} scope{providerMeta.scopes.length === 1 ? "" : "s"} granted
            </span>
          )}
        </div>
      </div>

      <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10, flex: "0 0 auto" }}>
        {hasReturnState && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "6px 10px",
              borderRadius: 999,
              border: "1px dashed color-mix(in oklch, var(--neon) 30%, var(--hairline))",
              background: "var(--bg)",
              fontFamily: "var(--font-mono)",
              fontSize: 10.5,
              color: "var(--fg-muted)",
              letterSpacing: "0.04em",
              maxWidth: 280,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: 999,
                background: "var(--neon)",
                boxShadow: "0 0 8px var(--neon)",
                flex: "0 0 auto",
              }}
            />
            <span style={{ color: "var(--fg-subtle)" }}>→</span>
            <span>{finishHref}</span>
          </div>
        )}

        {hasReturnState && (
          <a
            href={finishHref}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "7px 12px",
              borderRadius: 8,
              background: "var(--neon)",
              color: "var(--bg)",
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: "0.10em",
              textTransform: "uppercase",
              border: "1px solid color-mix(in oklch, var(--neon) 60%, var(--bg))",
              boxShadow:
                "0 0 0 1px color-mix(in oklch, var(--neon) 35%, transparent), 0 6px 16px -6px color-mix(in oklch, var(--neon) 80%, transparent)",
              whiteSpace: "nowrap",
            }}
          >
            return to MCP client
            <span aria-hidden="true">→</span>
          </a>
        )}

        <button
          type="button"
          onClick={onDismiss}
          aria-label="dismiss"
          style={{
            width: 26,
            height: 26,
            borderRadius: 8,
            display: "grid",
            placeItems: "center",
            background: "transparent",
            border: "1px solid var(--hairline)",
            color: "var(--fg-subtle)",
            cursor: "pointer",
            flex: "0 0 auto",
          }}
        >
          <X width="11" height="11" />
        </button>
      </div>
    </div>
  )
}

export default SuccessBanner
