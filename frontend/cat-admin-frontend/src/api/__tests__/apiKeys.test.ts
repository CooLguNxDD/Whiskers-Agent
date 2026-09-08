/**
 * API Keys Unit Tests
 *
 * Asserts catalog op identity and that HTTP exposures hit the correct paths.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { api } from "../client"
import {
  setCatalogSnapshot,
  invalidateCatalogClient,
} from "../catalogRuntime"
import type { CatalogOperation } from "../catalog"
import {
  listApiKeys,
  createApiKey,
  revokeApiKey,
  deleteApiKey,
  updateApiKeyScopes,
  listScopePresets,
  createScopePreset,
  deleteScopePreset,
  getScopeVocabulary,
  type ApiKey,
  type CreateApiKeyResponse,
  type ScopePreset,
} from "../apiKeys"

vi.mock("../client", () => {
  return {
    api: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      delete: vi.fn(),
      patch: vi.fn(),
    },
  }
})

function hostOp(
  operationId: string,
  method: string,
  path: string,
): CatalogOperation {
  return {
    plugin_id: "api.apikeys",
    operation_id: operationId,
    description: operationId,
    input_schema: { type: "object", properties: {}, additionalProperties: true },
    access: method === "GET" ? "read" : "write",
    required_scopes: [],
    visibility: "authenticated",
    version: "1",
    tags: ["apikeys", "host"],
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

const HOST_OPS: CatalogOperation[] = [
  hostOp("api_list_api_keys", "GET", "/api/auth/session_gated/api-keys"),
  hostOp("api_create_api_key", "POST", "/api/auth/session_gated/api-keys"),
  hostOp(
    "api_revoke_api_key",
    "POST",
    "/api/auth/session_gated/api-keys/{key_id}/revoke",
  ),
  hostOp(
    "api_delete_api_key",
    "DELETE",
    "/api/auth/session_gated/api-keys/{key_id}",
  ),
  hostOp(
    "api_update_api_key_scopes",
    "PUT",
    "/api/auth/session_gated/api-keys/{key_id}/scopes",
  ),
  hostOp("api_list_scope_presets", "GET", "/api/auth/session_gated/api-key-presets"),
  hostOp("api_create_scope_preset", "POST", "/api/auth/session_gated/api-key-presets"),
  hostOp(
    "api_delete_scope_preset",
    "DELETE",
    "/api/auth/session_gated/api-key-presets/{preset_id}",
  ),
  hostOp(
    "api_get_scope_vocabulary",
    "GET",
    "/api/auth/session_gated/scope-vocabulary",
  ),
]

describe("apiKeys API Client", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setCatalogSnapshot({ revision: 1, etag: '"t"', operations: HOST_OPS })
  })

  afterEach(() => {
    invalidateCatalogClient()
  })

  describe("listApiKeys", () => {
    it("calls GET api-keys via catalog HTTP exposure and returns .keys", async () => {
      const mockKeys: ApiKey[] = [
        {
          key_id: "ak_1",
          name: "test key",
          prefix: "whiskers_",
          status: "active",
          expires_at: null,
          last_used_at: null,
          created_at: "2026-06-20T00:00:00Z",
          revoked_at: null,
          scopes: null,
        },
      ]
      vi.mocked(api.get).mockResolvedValue({ keys: mockKeys })

      const result = await listApiKeys()

      expect(api.get).toHaveBeenCalledWith("/api/auth/session_gated/api-keys")
      expect(result).toEqual(mockKeys)
    })
  })

  describe("createApiKey", () => {
    it("POSTs create body via catalog path", async () => {
      const mockResponse: CreateApiKeyResponse = {
        key_id: "ak_2",
        token: "whiskers_token_123",
        prefix: "whiskers_",
        name: "ci",
        expires_at: "2026-06-21T00:00:00Z",
      }
      vi.mocked(api.post).mockResolvedValue(mockResponse)

      const result = await createApiKey("ci", 3600)

      expect(api.post).toHaveBeenCalledWith("/api/auth/session_gated/api-keys", {
        name: "ci",
        expires_in: 3600,
      })
      expect(result).toEqual(mockResponse)
    })
  })

  describe("revokeApiKey", () => {
    it("fills key_id path param", async () => {
      const mockResponse: CreateApiKeyResponse = {
        key_id: "ak_3",
        token: "whiskers_new",
        prefix: "whiskers_",
        name: "rotated",
        expires_at: null,
      }
      vi.mocked(api.post).mockResolvedValue(mockResponse)

      await revokeApiKey("ak_old")

      expect(api.post).toHaveBeenCalledWith(
        "/api/auth/session_gated/api-keys/ak_old/revoke",
        {},
      )
    })
  })

  describe("deleteApiKey", () => {
    it("DELETEs filled path", async () => {
      vi.mocked(api.delete).mockResolvedValue({ ok: true })
      await deleteApiKey("ak_x")
      expect(api.delete).toHaveBeenCalledWith(
        "/api/auth/session_gated/api-keys/ak_x",
      )
    })
  })

  describe("updateApiKeyScopes", () => {
    it("PUTs scopes with path param", async () => {
      vi.mocked(api.put).mockResolvedValue({ ok: true })
      await updateApiKeyScopes("ak_1", ["admin"])
      expect(api.put).toHaveBeenCalledWith(
        "/api/auth/session_gated/api-keys/ak_1/scopes",
        { scopes: ["admin"] },
      )
    })
  })

  describe("listScopePresets", () => {
    it("returns .presets array", async () => {
      const presets: ScopePreset[] = [
        {
          id: "p1",
          name: "dev",
          scopes: ["plugin:x"],
          created_at: "2026-01-01T00:00:00Z",
        },
      ]
      vi.mocked(api.get).mockResolvedValue({ presets })
      const result = await listScopePresets()
      expect(api.get).toHaveBeenCalledWith(
        "/api/auth/session_gated/api-key-presets",
      )
      expect(result).toEqual(presets)
    })
  })

  describe("createScopePreset", () => {
    it("POSTs name+scopes", async () => {
      const preset: ScopePreset = {
        id: "p2",
        name: "ops",
        scopes: null,
        created_at: "2026-01-01T00:00:00Z",
      }
      vi.mocked(api.post).mockResolvedValue(preset)
      await createScopePreset("ops", null)
      expect(api.post).toHaveBeenCalledWith(
        "/api/auth/session_gated/api-key-presets",
        { name: "ops", scopes: null },
      )
    })
  })

  describe("deleteScopePreset", () => {
    it("fills preset_id", async () => {
      vi.mocked(api.delete).mockResolvedValue({ ok: true })
      await deleteScopePreset("p1")
      expect(api.delete).toHaveBeenCalledWith(
        "/api/auth/session_gated/api-key-presets/p1",
      )
    })
  })

  describe("getScopeVocabulary", () => {
    it("GETs scope-vocabulary", async () => {
      vi.mocked(api.get).mockResolvedValue({ global_scopes: ["admin"] })
      const result = await getScopeVocabulary()
      expect(api.get).toHaveBeenCalledWith(
        "/api/auth/session_gated/scope-vocabulary",
      )
      expect(result.global_scopes).toEqual(["admin"])
    })
  })
})
