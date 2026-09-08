import { describe, it, expect, vi, beforeEach } from "vitest"
import { updateProxyDescription } from "../proxies"
import * as catalogRuntime from "../catalogRuntime"

vi.mock("../catalogRuntime", () => ({
  callCatalogOp: vi.fn(),
}))

describe("proxies API", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe("updateProxyDescription", () => {
    it("includes customDescription and workspaceLabel in payload when both provided", async () => {
      vi.mocked(catalogRuntime.callCatalogOp).mockResolvedValue({ status: "ok" })

      await updateProxyDescription("proxy1", "Custom desc", "dev-ws")

      expect(catalogRuntime.callCatalogOp).toHaveBeenCalledWith(
        "api.proxy",
        "update_proxy",
        {
          name: "proxy1",
          customDescription: "Custom desc",
          workspaceLabel: "dev-ws",
        }
      )
    })

    it("includes customDescription: null in payload when customDescription is explicitly null", async () => {
      vi.mocked(catalogRuntime.callCatalogOp).mockResolvedValue({ status: "ok" })

      await updateProxyDescription("proxy1", null, "dev-ws")

      expect(catalogRuntime.callCatalogOp).toHaveBeenCalledWith(
        "api.proxy",
        "update_proxy",
        {
          name: "proxy1",
          customDescription: null,
          workspaceLabel: "dev-ws",
        }
      )
    })

    it("omits customDescription key from payload when customDescription is undefined (workspaceLabel-only update)", async () => {
      vi.mocked(catalogRuntime.callCatalogOp).mockResolvedValue({ status: "ok" })

      await updateProxyDescription("proxy1", undefined, "dev-ws")

      expect(catalogRuntime.callCatalogOp).toHaveBeenCalledWith(
        "api.proxy",
        "update_proxy",
        {
          name: "proxy1",
          workspaceLabel: "dev-ws",
        }
      )
    })

    it("omits workspaceLabel key from payload when workspaceLabel is undefined", async () => {
      vi.mocked(catalogRuntime.callCatalogOp).mockResolvedValue({ status: "ok" })

      await updateProxyDescription("proxy1", "Custom desc")

      expect(catalogRuntime.callCatalogOp).toHaveBeenCalledWith(
        "api.proxy",
        "update_proxy",
        {
          name: "proxy1",
          customDescription: "Custom desc",
        }
      )
    })
  })
})
