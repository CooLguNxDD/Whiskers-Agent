/**
 * Checks whether an admin account has been created.
 * Returns null when the result cannot be determined (network error, non-JSON response, non-OK status).
 */
export async function getAdminExists(): Promise<boolean | null> {
  try {
    const res = await fetch("/api/admin/public/exists", {
      credentials: "include",
    })

    if (!res.ok) return null

    const ct = res.headers?.get?.("content-type") ?? ""
    if (ct && !ct.includes("application/json")) return null

    const data = (await res.json()) as { exists: boolean }
    return data.exists
  } catch {
    return null
  }
}