/**
 * API Client
 *
 * Provides a standardized fetch wrapper for making authenticated requests to the backend.
 * Uses HttpOnly session cookies — no token storage in JS. Handles automatic refresh on 401
 * and global error handling via an event bus.
 */

import { getErrorMessage } from "@/utils/errors"
import { bus } from "@/events/bus"

// Singleton promise to coalesce concurrent 401-triggered refreshes and avoid "refresh avalanche".
let refreshPromise: Promise<boolean> | null = null
// Ensures a failed refresh cycle notifies `auth:expired` exactly once, even
// when many concurrent requests are awaiting the same refreshPromise.
let refreshFailureNotified = false

// Abort a request if it hangs past this budget (mirrors tryRefreshSession's 10s timeout).
const REQUEST_TIMEOUT_MS = 30000

// Combine a caller-supplied signal (if any) with a fresh per-fetch timeout signal.
function withTimeout(signal?: AbortSignal | null): AbortSignal {
  const timeout = AbortSignal.timeout(REQUEST_TIMEOUT_MS)
  return signal ? AbortSignal.any([signal, timeout]) : timeout
}

/**
 * Attempts to rotate the session by calling POST /admin/refresh (uses refresh cookie).
 * If a refresh is already in progress, concurrent callers await the same promise.
 */
export async function tryRefreshSession(): Promise<boolean> {
  if (refreshPromise) return refreshPromise
  refreshFailureNotified = false
  refreshPromise = fetch("/api/admin/public/refresh", {
    method: "POST",
    credentials: "include",
    signal: AbortSignal.timeout(10000),
  })
    .then((res) => res.ok)
    .catch(() => false)
    .then((ok) => {
      if (!ok && !refreshFailureNotified) {
        refreshFailureNotified = true
        bus.emit("auth:expired", undefined)
      }
      return ok
    })
    .finally(() => {
      refreshPromise = null
    })
  return refreshPromise
}

async function parseJsonSafe<T>(res: Response): Promise<T> {
  const ct = res.headers?.get?.("content-type") ?? ""
  // Only throw when a Content-Type is present and it's explicitly non-JSON
  // (no header = assume JSON, preserving backward compat with lean test mocks and some proxies)
  if (ct && !ct.includes("application/json")) {
    throw new Error(`Expected JSON but got ${res.status} ${res.statusText} (content-type: ${ct})`)
  }
  try {
    return await res.json() as T
  } catch (err) {
    throw new Error(
      `Failed to parse JSON response (${res.status} ${res.statusText})`,
      { cause: err },
    )
  }
}

/**
 * Base request function that uses credentials (cookies) and auto-refresh logic on 401.
 */
export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  }

  let res: Response
  try {
    res = await fetch(path, { ...options, headers, credentials: "include", signal: withTimeout(options.signal) })
  } catch (err) {
    throw new Error(`Network request failed: ${getErrorMessage(err)}`, { cause: err })
  }

  // Handle 401 Unauthorized by attempting to refresh the session cookie and retrying once
  if (res.status === 401) {
    const { pathname } = new URL(path, window.location.origin)
    const isAuthEndpoint = pathname === "/admin/public/refresh" || pathname === "/admin/public/login"
    if (!isAuthEndpoint) {
      const refreshed = await tryRefreshSession()
      if (refreshed) {
        let retry: Response
        try {
          retry = await fetch(path, { ...options, headers, credentials: "include", signal: withTimeout(options.signal) })
        } catch (err) {
          throw new Error(`Network request failed: ${getErrorMessage(err)}`, { cause: err })
        }
        if (retry.ok) return parseJsonSafe<T>(retry)
        // Refresh itself succeeded but this specific retry still failed for an
        // unrelated reason — surface it here since tryRefreshSession only
        // notifies on its own failure path.
        bus.emit("auth:expired", undefined)
        throw new Error("Unauthorized")
      }
      // Refresh failed: tryRefreshSession already emitted auth:expired exactly
      // once for every caller sharing this refresh cycle. Don't re-emit here.
      throw new Error("Unauthorized")
    }
    bus.emit("auth:expired", undefined)
    throw new Error("Unauthorized")
  }

  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`)
  }

  return parseJsonSafe<T>(res)
}

/** Verb-shaped convenience wrappers over `request<T>` (GET/POST/PUT/PATCH/DELETE). */
export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  delete: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "DELETE",
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),
}
