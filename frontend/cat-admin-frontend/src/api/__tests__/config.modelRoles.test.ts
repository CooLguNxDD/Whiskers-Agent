/**
 * Model Role Config API Unit Tests
 *
 * Asserts catalog op -> HTTP path resolution for model-role config endpoints.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { api } from "../client"
import { setCatalogSnapshot, invalidateCatalogClient } from "../catalogRuntime"
import type { CatalogOperation } from "../catalog"
import {
  getModelRoles,
  saveModelRole,
  saveEffortMap,
  deleteModelRole,
  type ModelRolesConfig,
  type ModelRoleEntry,
} from "../config"

vi.mock("../client", () => {
  return {
    api: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      delete: vi.fn(),
    },
  }
})

function hostOp(operationId: string, method: string, path: string): CatalogOperation {
  return {
    plugin_id: "api.config",
    operation_id: operationId,
    description: operationId,
    input_schema: { type: "object", properties: {}, additionalProperties: true },
    access: method === "GET" ? "read" : "write",
    required_scopes: [],
    visibility: "authenticated",
    version: "1",
    tags: ["config", "host"],
    http: {
      method,
      path_template: path,
      auth_policy: "session_gated",
    },
    mcp: null,
    ui: null,
    is_fast_path: false,
    descriptor_hash: operationId,
  }
}

const OPS: CatalogOperation[] = [
  hostOp("api_get_model_roles", "GET", "/api/config/session_gated/model-roles"),
  hostOp("api_save_model_role", "POST", "/api/config/session_gated/model-roles"),
  hostOp("api_delete_model_role", "DELETE", "/api/config/session_gated/model-roles/{role_id}"),
]

describe("model role config API client", () => {
  const mockRole: ModelRoleEntry = {
    role_id: "triage",
    description: "Classify user turn.",
    ladder: [{ selector: "core", max_attempts: 1, timeout_s: null }],
    validate: null,
    entry_conditions: [],
    escalate_on_exception: true,
    escalate_on_invalid: true,
    terminal_fallback: "ctx_llm",
    owner: "core",
    source: "core",
    preview: [{ selector: "core", resolved_model: "claude-sonnet-5" }],
  }

  const mockConfig: ModelRolesConfig = {
    roles: [mockRole],
    effort_map: { low: "fast", medium: "balanced", high: "strongest", max: "strongest" },
    pool_names: ["core"],
    aliases: ["core", "strongest"],
    efforts: ["low", "medium", "high", "max"],
    node_roles: { triage: ["triage"] },
  }

  beforeEach(() => {
    vi.clearAllMocks()
    setCatalogSnapshot({ revision: 1, etag: '"t"', operations: OPS })
  })

  afterEach(() => {
    invalidateCatalogClient()
  })

  describe("getModelRoles", () => {
    it("GETs model-roles via catalog HTTP exposure", async () => {
      vi.mocked(api.get).mockResolvedValue(mockConfig)
      const result = await getModelRoles()
      expect(api.get).toHaveBeenCalledWith("/api/config/session_gated/model-roles")
      expect(result).toEqual(mockConfig)
    })
  })

  describe("saveModelRole", () => {
    it("POSTs {role_id, spec} via catalog HTTP exposure", async () => {
      const mockResponse = { ok: true, role: mockRole }
      vi.mocked(api.post).mockResolvedValue(mockResponse)

      const result = await saveModelRole("triage", {
        role_id: "triage",
        description: "Classify user turn.",
        ladder: [{ selector: "core", max_attempts: 1, timeout_s: null }],
        validate: null,
        entry_conditions: [],
        escalate_on_exception: true,
        escalate_on_invalid: true,
        terminal_fallback: "ctx_llm",
      })

      expect(api.post).toHaveBeenCalledWith(
        "/api/config/session_gated/model-roles",
        expect.objectContaining({ role_id: "triage", spec: expect.objectContaining({ role_id: "triage" }) }),
      )
      expect(result).toEqual(mockResponse)
    })
  })

  describe("saveEffortMap", () => {
    it("POSTs {effort_map} via catalog HTTP exposure", async () => {
      const mockResponse = { ok: true, effort_map: { low: "fast" } }
      vi.mocked(api.post).mockResolvedValue(mockResponse)

      const result = await saveEffortMap({ low: "fast" })

      expect(api.post).toHaveBeenCalledWith(
        "/api/config/session_gated/model-roles",
        { effort_map: { low: "fast" } },
      )
      expect(result).toEqual(mockResponse)
    })
  })

  describe("deleteModelRole", () => {
    it("DELETEs the filled {role_id} path", async () => {
      const mockResponse = { ok: true, role: null }
      vi.mocked(api.delete).mockResolvedValue(mockResponse)

      const result = await deleteModelRole("triage")

      expect(api.delete).toHaveBeenCalledWith("/api/config/session_gated/model-roles/triage")
      expect(result).toEqual(mockResponse)
    })
  })
})
