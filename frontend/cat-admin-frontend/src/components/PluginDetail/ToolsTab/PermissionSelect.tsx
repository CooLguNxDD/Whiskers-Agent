import { cn } from "@/lib/utils"
import type { ToolPermission } from "@/api/tools"
import type { PermModeOrMixed } from "@/lib/toolState"

interface PermissionSelectProps {
  mode: PermModeOrMixed
  perm: ToolPermission | null | undefined
  onModeChange: (mode: "auto" | "approval" | "custom") => void
  onPermPatch: (patch: Partial<ToolPermission>) => void
  disabled?: boolean
}

/**
 * Permission policy selector (Auto/Approval/Custom) + per-tool R/W/C toggles.
 */
export function PermissionSelect({
  mode,
  perm,
  onModeChange,
  onPermPatch,
  disabled = false,
}: PermissionSelectProps) {
  const p = perm || {}
  const isGroup = perm === null || perm === undefined

  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
      <select
        className="ct-input"
        disabled={disabled}
        value={mode}
        onChange={(e) => onModeChange(e.target.value as "auto" | "approval" | "custom")}
        style={{
          width: "auto",
          padding: "2px 6px",
          height: 24,
          fontSize: 11,
          background: "var(--bg-input)",
          border: "1px solid var(--border)",
          borderRadius: 4,
          color: "var(--fg-default)",
        }}
      >
        {mode === "mixed" && <option value="mixed">Mixed</option>}
        {mode === "custom" && <option value="custom" disabled hidden>Custom</option>}
        <option value="auto">Auto-Allow</option>
        <option value="approval">Need Approval</option>
      </select>

      {!isGroup && (
        <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
          <button
            type="button"
            title="Allow Read"
            disabled={disabled}
            className={cn("ct-perm-btn", { "is-active": p.allow_read !== false })}
            aria-pressed={p.allow_read !== false}
            style={{ fontSize: 9, padding: "1px 4px", minWidth: 18 }}
            onClick={() => onPermPatch({ allow_read: p.allow_read === false })}
          >
            R
          </button>
          <button
            type="button"
            title="Allow Write"
            disabled={disabled}
            className={cn("ct-perm-btn", { "is-active": p.allow_write !== false })}
            aria-pressed={p.allow_write !== false}
            style={{ fontSize: 9, padding: "1px 4px", minWidth: 18 }}
            onClick={() => onPermPatch({ allow_write: p.allow_write === false })}
          >
            W
          </button>
          <button
            type="button"
            title="Require Confirmation"
            disabled={disabled}
            className={cn("ct-perm-btn", { "is-active": p.require_confirmation !== false })}
            aria-pressed={p.require_confirmation !== false}
            style={{ fontSize: 9, padding: "1px 4px", minWidth: 18 }}
            onClick={() => onPermPatch({ require_confirmation: p.require_confirmation === false })}
          >
            C
          </button>
        </div>
      )}
    </div>
  )
}
