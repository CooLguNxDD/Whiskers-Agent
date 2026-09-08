import { describe, it, expect } from "vitest"
import type { PluginTool } from "@/api/plugins"
import {
  deriveState,
  statePatch,
  derivePermMode,
  permModePatch,
  groupTools,
  aggregateState,
  aggregatePermMode
} from "../toolState"

describe("toolState helper functions", () => {
  describe("deriveState", () => {
    it("should return disabled if not enabled", () => {
      const tool: PluginTool = {
        name: "test",
        description: "",
        is_enabled: false,
        is_hidden: false,
      }
      expect(deriveState(tool)).toBe("disabled")

      const toolHidden: PluginTool = {
        name: "test",
        description: "",
        is_enabled: false,
        is_hidden: true,
      }
      expect(deriveState(toolHidden)).toBe("disabled")
    })

    it("should return hidden if enabled and hidden", () => {
      const tool: PluginTool = {
        name: "test",
        description: "",
        is_enabled: true,
        is_hidden: true,
      }
      expect(deriveState(tool)).toBe("hidden")
    })

    it("should return enabled if enabled and not hidden", () => {
      const tool: PluginTool = {
        name: "test",
        description: "",
        is_enabled: true,
        is_hidden: false,
      }
      expect(deriveState(tool)).toBe("enabled")
    })
  })

  describe("statePatch", () => {
    it("should return correct enabled/hidden pairs", () => {
      expect(statePatch("enabled")).toEqual({ enable: true, hide: false })
      expect(statePatch("hidden")).toEqual({ enable: true, hide: true })
      expect(statePatch("disabled")).toEqual({ enable: false, hide: false })
    })
  })

  describe("derivePermMode", () => {
    it("should return approval for null/undefined policy", () => {
      expect(derivePermMode(null)).toBe("approval")
      expect(derivePermMode(undefined)).toBe("approval")
    })

    it("should return auto for require_confirmation = false and allow_read/write = true", () => {
      expect(derivePermMode({ allow_read: true, allow_write: true, require_confirmation: false })).toBe("auto")
    })

    it("should return approval for require_confirmation = true and allow_read/write = true", () => {
      expect(derivePermMode({ allow_read: true, allow_write: true, require_confirmation: true })).toBe("approval")
    })

    it("should return custom for other values", () => {
      expect(derivePermMode({ allow_read: false, allow_write: true, require_confirmation: true })).toBe("custom")
      expect(derivePermMode({ allow_read: true, allow_write: false, require_confirmation: false })).toBe("custom")
    })
  })

  describe("permModePatch", () => {
    it("should return correct patch objects or null", () => {
      expect(permModePatch("auto")).toEqual({ allow_read: true, allow_write: true, require_confirmation: false })
      expect(permModePatch("approval")).toEqual({ allow_read: true, allow_write: true, require_confirmation: true })
      expect(permModePatch("custom")).toBeNull()
    })
  })

  describe("groupTools", () => {
    it("should group tools by category and access, stable-sorted", () => {
      const tools: PluginTool[] = [
        { name: "tool_c", description: "", is_enabled: true, is_hidden: false, group: "messages", access: "write" },
        { name: "tool_a", description: "", is_enabled: true, is_hidden: false, group: "general", access: "read" },
        { name: "tool_b", description: "", is_enabled: true, is_hidden: false, group: "records", access: "read" },
        { name: "tool_d", description: "", is_enabled: true, is_hidden: false, group: "messages", access: "read" },
        { name: "tool_e", description: "", is_enabled: true, is_hidden: false, group: "records", access: "read" },
      ]

      const grouped = groupTools(tools)

      // general group is first, then alphabetical (messages, records)
      expect(grouped.length).toBe(3)
      expect(grouped[0].group).toBe("general")
      expect(grouped[1].group).toBe("messages")
      expect(grouped[2].group).toBe("records")

      // messages subgroups: "read" (tool_d) and "write" (tool_c)
      const msgGroup = grouped[1]
      expect(msgGroup.subgroups.length).toBe(2)
      expect(msgGroup.subgroups[0].access).toBe("read")
      expect(msgGroup.subgroups[0].tools.map(t => t.name)).toEqual(["tool_d"])
      expect(msgGroup.subgroups[1].access).toBe("write")
      expect(msgGroup.subgroups[1].tools.map(t => t.name)).toEqual(["tool_c"])

      // records subgroups: "read" (tool_b, tool_e sorted)
      const recordGroup = grouped[2]
      expect(recordGroup.subgroups.length).toBe(1)
      expect(recordGroup.subgroups[0].access).toBe("read")
      expect(recordGroup.subgroups[0].tools.map(t => t.name)).toEqual(["tool_b", "tool_e"])
    })

    it("should default tools with undefined access to the read subgroup", () => {
      const tools: PluginTool[] = [
        { name: "tool_undefined", description: "", is_enabled: true, is_hidden: false, group: "general" },
        { name: "tool_write", description: "", is_enabled: true, is_hidden: false, group: "general", access: "write" },
      ]

      const grouped = groupTools(tools)

      expect(grouped.length).toBe(1)
      const genGroup = grouped[0]
      expect(genGroup.subgroups.length).toBe(2)
      expect(genGroup.subgroups[0].access).toBe("read")
      expect(genGroup.subgroups[0].tools.map(t => t.name)).toEqual(["tool_undefined"])
      expect(genGroup.subgroups[1].access).toBe("write")
      expect(genGroup.subgroups[1].tools.map(t => t.name)).toEqual(["tool_write"])
    })
  })

  describe("aggregateState", () => {
    it("should return the state if all tools share it, otherwise mixed", () => {
      const tEnabled: PluginTool = { name: "1", description: "", is_enabled: true, is_hidden: false }
      const tHidden: PluginTool = { name: "2", description: "", is_enabled: true, is_hidden: true }
      const tDisabled: PluginTool = { name: "3", description: "", is_enabled: false, is_hidden: false }

      expect(aggregateState([tEnabled, tEnabled])).toBe("enabled")
      expect(aggregateState([tHidden, tHidden])).toBe("hidden")
      expect(aggregateState([tDisabled, tDisabled])).toBe("disabled")
      expect(aggregateState([tEnabled, tHidden])).toBe("mixed")
      expect(aggregateState([])).toBe("disabled")
    })
  })

  describe("aggregatePermMode", () => {
    it("should return the mode if all tools share it, otherwise mixed", () => {
      const tAuto: PluginTool = { name: "1", description: "", is_enabled: true, is_hidden: false, permission: { allow_read: true, allow_write: true, require_confirmation: false } }
      const tApprove: PluginTool = { name: "2", description: "", is_enabled: true, is_hidden: false, permission: { allow_read: true, allow_write: true, require_confirmation: true } }
      const tCustom: PluginTool = { name: "3", description: "", is_enabled: true, is_hidden: false, permission: { allow_read: false, allow_write: true, require_confirmation: true } }

      expect(aggregatePermMode([tAuto, tAuto])).toBe("auto")
      expect(aggregatePermMode([tApprove, tApprove])).toBe("approval")
      expect(aggregatePermMode([tCustom, tCustom])).toBe("custom")
      expect(aggregatePermMode([tAuto, tApprove])).toBe("mixed")
      expect(aggregatePermMode([])).toBe("approval")
    })
  })
})
