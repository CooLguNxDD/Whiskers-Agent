/**
 * Step Model Policy API Unit Tests
 *
 * Asserts catalog op → HTTP path resolution for step-model policy endpoints.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { api } from "../client"
import {
  setCatalogSnapshot,
  invalidateCatalogClient,
} from "../catalogRuntime"
import type { CatalogOperation } from "../catalog"
import {
  getStepModelPolicy,
  saveStepModelPolicy,
  type StepModelConfig,
  type StepModelPolicy,
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

const OPS: CatalogOperation[] = [
  {
    plugin_id: "api.config",
    operation_id: "api_get_step_models",
    description: "get step models",
    input_schema: {},
    access: "read",
    required_scopes: [],
    visibility: "authenticated",
    version: "1",
    tags: ["config", "host"],
    http: {
      method: "GET",
      path_template: "/api/config/session_gated/step-models",
      auth_policy: "session_gated",
    },
    mcp: null,
    ui: null,
    is_fast_path: false,
    descriptor_hash: "get",
  },
  {
    plugin_id: "api.config",
    operation_id: "api_save_step_models",
    description: "save step models",
    input_schema: {},
    access: "write",
    required_scopes: [],
    visibility: "authenticated",
    version: "1",
    tags: ["config", "host"],
    http: {
      method: "POST",
      path_template: "/api/config/session_gated/step-models",
      auth_policy: "session_gated",
    },
    mcp: null,
    ui: null,
    is_fast_path: false,
    descriptor_hash: "save",
  },
]

describe("step model policy API client", () => {
  const mockPolicy: StepModelPolicy = {
    strategy: "strength",
    task_type_map: { search: "fast-chat" },
    op_overrides: { "proxy_notion.search": "core" },
    parallel_enabled: true,
    fanout_concurrency: 5,
  }

  const mockConfig: StepModelConfig = {
    policy: mockPolicy,
    pool_names: ["fast-chat", "core"],
  }

  beforeEach(() => {
    vi.clearAllMocks()
    setCatalogSnapshot({ revision: 1, etag: '"t"', operations: OPS })
  })

  afterEach(() => {
    invalidateCatalogClient()
  })

  describe("getStepModelPolicy", () => {
    it("GETs step-models via catalog HTTP exposure", async () => {
      vi.mocked(api.get).mockResolvedValue(mockConfig)

      const result = await getStepModelPolicy()

      expect(api.get).toHaveBeenCalledWith("/api/config/session_gated/step-models")
      expect(result).toEqual(mockConfig)
    })
  })

  describe("saveStepModelPolicy", () => {
    it("POSTs patch via catalog HTTP exposure", async () => {
      const patch = { strategy: "task_type" as const }
      const mockResponse = {
        ok: true,
        policy: { ...mockPolicy, strategy: "task_type" as const },
      }
      vi.mocked(api.post).mockResolvedValue(mockResponse)

      const result = await saveStepModelPolicy(patch)

      expect(api.post).toHaveBeenCalledWith(
        "/api/config/session_gated/step-models",
        patch,
      )
      expect(result).toEqual(mockResponse)
    })
  })
})
