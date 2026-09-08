import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

describe("admin.ts", () => {
  let originalFetch: typeof globalThis.fetch

  beforeEach(() => {
    originalFetch = globalThis.fetch
    vi.clearAllMocks()
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
  })

  describe("getAdminExists", () => {
    it('returns true when response is 200 application/json {"exists": true}', async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        headers: { get: () => "application/json" },
        json: vi.fn().mockResolvedValue({ exists: true }),
      } as unknown as Response)

      const { getAdminExists } = await import("../admin?t=" + Date.now())
      const result = await getAdminExists()

      expect(result).toBe(true)
      expect(globalThis.fetch).toHaveBeenCalledWith("/api/admin/public/exists", {
        credentials: "include",
      })
    })

    it('returns false when response is 200 application/json {"exists": false}', async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        headers: { get: () => "application/json" },
        json: vi.fn().mockResolvedValue({ exists: false }),
      } as unknown as Response)

      const { getAdminExists } = await import("../admin?t=" + Date.now() + "b")
      const result = await getAdminExists()

      expect(result).toBe(false)
    })

    it("returns null when response is 200 text/html (proxy fallback case)", async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        headers: { get: () => "text/html; charset=utf-8" },
        json: vi.fn(),
      } as unknown as Response)

      const { getAdminExists } = await import("../admin?t=" + Date.now() + "c")
      const result = await getAdminExists()

      expect(result).toBeNull()
    })

    it("returns null when response is not ok (e.g. 503)", async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        headers: { get: () => "application/json" },
        json: vi.fn(),
      } as unknown as Response)

      const { getAdminExists } = await import("../admin?t=" + Date.now() + "d")
      const result = await getAdminExists()

      expect(result).toBeNull()
    })

    it("returns null when fetch rejects (network error)", async () => {
      globalThis.fetch = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"))

      const { getAdminExists } = await import("../admin?t=" + Date.now() + "e")
      const result = await getAdminExists()

      expect(result).toBeNull()
    })
  })
})