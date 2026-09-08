/**
 * buildApiKeyColumns — column definitions for the API Keys dashboard Table.
 * Builds sortable Table columns with scope badges and action handlers.
 */

import { Key as KeyIcon, Copy, Trash2 } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Chevron } from "@/components/shell/Icons"
import type { Column } from "@/components/Table/useSort"
import type { ApiKey } from "@/api/apiKeys"
import ScopeBadges from "./ScopeBadges"

interface BuildApiKeyColumnsArgs {
  copiedPrefixId: string | null
  onCopyPrefix: (prefix: string, keyId: string) => void
  onConfigureScopes: (key: ApiKey) => void
  onRotate: (key: ApiKey) => void
  onDelete: (key: ApiKey) => void
}

/** Build column definitions for the API Keys Table. */
export function buildApiKeyColumns({
  copiedPrefixId,
  onCopyPrefix,
  onConfigureScopes,
  onRotate,
  onDelete,
}: BuildApiKeyColumnsArgs): Column<ApiKey>[] {
  return [
    {
      key: "icon",
      header: "",
      width: "44px",
      truncate: false,
      render: () => (
        <div className="ct-key-ico">
          <KeyIcon className="size-5" />
        </div>
      )
    },
    {
      key: "name",
      header: "Name",
      width: "1.3fr",
      sortable: true,
      sortValue: (k: ApiKey) => k.name,
      truncate: false,
      render: (k: ApiKey) => (
        <div className="ct-key-name">
          {k.name}
          <Badge
            className={`text-[9px] px-1.5 py-0.5 rounded font-mono ${
              k.status === "active" ? "ct-pill is-ok" : "ct-pill is-err"
            }`}
          >
            {k.status}
          </Badge>
        </div>
      )
    },
    {
      key: "prefix",
      header: "Prefix",
      width: "160px",
      truncate: false,
      render: (k: ApiKey) => (
        <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {k.prefix}…
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              onCopyPrefix(k.prefix, k.key_id)
            }}
            onKeyDown={(e) => e.stopPropagation()}
            className="text-muted-foreground hover:text-foreground p-1 transition-colors cursor-pointer bg-transparent border-0 focus:outline-none"
            title="Copy Prefix"
            style={{ display: "inline-flex", padding: 0 }}
          >
            <Copy className="size-3" style={{ opacity: copiedPrefixId === k.key_id ? 1 : 0.6 }} />
          </button>
        </span>
      )
    },
    {
      key: "created",
      header: "Created",
      width: "110px",
      sortable: true,
      defaultDir: "desc" as const,
      sortValue: (k: ApiKey) => (k.created_at ? new Date(k.created_at).getTime() : 0),
      render: (k: ApiKey) => (k.created_at ? new Date(k.created_at).toLocaleDateString() : "Unknown")
    },
    {
      key: "expires",
      header: "Expires",
      width: "110px",
      render: (k: ApiKey) => (k.expires_at ? new Date(k.expires_at).toLocaleDateString() : "Never")
    },
    {
      key: "last_used",
      header: "Last Used",
      width: "110px",
      sortable: true,
      defaultDir: "desc" as const,
      sortValue: (k: ApiKey) => (k.last_used_at ? new Date(k.last_used_at).getTime() : 0),
      render: (k: ApiKey) => (k.last_used_at ? new Date(k.last_used_at).toLocaleDateString() : "Never")
    },
    {
      key: "scopes",
      header: "Scopes",
      width: "1fr",
      truncate: false,
      render: (k: ApiKey) => <ScopeBadges scopes={k.scopes} />
    },
    {
      key: "actions",
      header: "Actions",
      width: "210px",
      align: "right" as const,
      truncate: false,
      render: (k: ApiKey) => (
        <div
          style={{ display: "flex", gap: 8, justifyContent: "flex-end", alignItems: "center" }}
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            onClick={() => onConfigureScopes(k)}
            className="text-xs font-semibold border border-border hover:border-[color-mix(in_oklch,var(--amber)_35%,var(--hairline))] rounded-lg px-2.5 py-1 transition-colors cursor-pointer bg-transparent text-foreground"
          >
            Configure Scopes
          </button>
          {k.status === "active" && (
            <button
              type="button"
              onClick={() => onRotate(k)}
              className="text-xs font-semibold border border-border hover:border-[color-mix(in_oklch,var(--amber)_35%,var(--hairline))] rounded-lg px-2.5 py-1 transition-colors cursor-pointer bg-transparent text-foreground"
            >
              Rotate
            </button>
          )}
          <button
            type="button"
            onClick={() => onDelete(k)}
            className="text-muted-foreground hover:text-[var(--danger)] p-1.5 transition-colors cursor-pointer bg-transparent border-0 focus:outline-none"
            title="Delete Key"
          >
            <Trash2 className="size-4" />
          </button>
        </div>
      )
    },
    {
      key: "chevron",
      header: "",
      width: "24px",
      truncate: false,
      render: () => <Chevron width="14" height="14" className="ct-chev" />
    }
  ]
}
