import type { FC } from "react"
import { StateToggle } from "../StateToggle"
import { PermissionSelect } from "../PermissionSelect"
import type { UnifiedState, PermMode } from "@/lib/toolState"

export interface BatchToolActionBarProps {
  stateValue: UnifiedState | "mixed"
  permModeValue: PermMode | "mixed"
  onStateChange: (nextState: UnifiedState) => void
  onPermModeChange: (nextMode: PermMode) => void
}

/**
 * Action bar for batch toggling state and permissions on a group of tools.
 */
export const BatchToolActionBar: FC<BatchToolActionBarProps> = ({
  stateValue,
  permModeValue,
  onStateChange,
  onPermModeChange,
}) => {
  return (
    <div
      style={{ display: "flex", alignItems: "center", gap: 12 }}
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => e.stopPropagation()}
    >
      <StateToggle
        value={stateValue}
        onChange={onStateChange}
        isGroup={true}
      />
      <PermissionSelect
        mode={permModeValue}
        perm={null}
        onModeChange={onPermModeChange}
        onPermPatch={() => {}}
      />
    </div>
  )
}
