/**
 * ScopeBadges — pure display of an API key's scope list.
 */

import { Badge } from "@/components/ui/badge"

interface ScopeBadgesProps {
  scopes: string[] | null
  className?: string
}

function chipColor(scope: string) {
  if (scope.startsWith("terminal:")) return "ct-scope-chip is-terminal"
  if (scope.startsWith("sandbox:"))  return "ct-scope-chip is-sandbox"
  if (scope.startsWith("plugin:"))   return "ct-scope-chip is-plugin"
  if (scope.startsWith("group:"))    return "ct-scope-chip is-group"
  return "ct-scope-chip"
}

/**
 * Renders a list of badges for visual representation of assigned scopes.
 */
export function ScopeBadges({ scopes, className = "" }: ScopeBadgesProps) {
  if (scopes === null) {
    return (
      <Badge className={`ct-scope-chip is-legacy ${className}`}>
        All Access
      </Badge>
    )
  }
  if (scopes.length === 0) {
    return (
      <Badge className={`ct-scope-chip is-deny ${className}`}>
        Deny All
      </Badge>
    )
  }
  const visible = scopes.slice(0, 3)
  const overflow = scopes.length - 3
  return (
    <div className={`flex flex-wrap gap-1 items-center ${className}`}>
      {visible.map((s) => (
        <span key={s} className={chipColor(s)}>
          {s}
        </span>
      ))}
      {overflow > 0 && (
        <span className="text-[10px] text-muted-foreground font-mono">+{overflow} more</span>
      )}
    </div>
  )
}

export default ScopeBadges
