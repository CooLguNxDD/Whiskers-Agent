import { useEffect, useRef, useState } from "react"
import {
  createRootRoute,
  Outlet,
  useNavigate,
  useRouterState,
} from "@tanstack/react-router"
import { useSessionStore } from "@/store"
import { useTotpStatusQuery } from "@/hooks/useTotp"
import RevokeModal from "@/components/RevokeModal/RevokeModal"

/** Root app shell: session gate, revoke modal, and nested route outlet. */
export function RootLayout() {
    const navigate = useNavigate()
    const pathname = useRouterState({ select: (s) => s.location.pathname })
    const pathnameRef = useRef(pathname)
    useEffect(() => {
      pathnameRef.current = pathname
    }, [pathname])

    const dispatch = useSessionStore((s) => s.dispatch)
    const markAuthProbeDone = useSessionStore((s) => s.markAuthProbeDone)
    const [checking, setChecking] = useState(
      pathname !== "/login" && pathname !== "/signup",
    )

    const { data: totpData } = useTotpStatusQuery()

    useEffect(() => {
      if (pathnameRef.current === "/login" || pathnameRef.current === "/signup") {
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setChecking(false)
        markAuthProbeDone()
        return
      }

      const controller = new AbortController()

      async function checkAuth() {
        try {
          const res = await fetch("/api/admin/public/me", {
            credentials: "include",
            signal: controller.signal,
          })
          if (!res.ok) throw new Error("Not auth")
          const data = (await res.json()) as {
            subject: string | null
            scopes: string[]
            expires_at: number | null
            iat?: number | null
          }
          dispatch({
            type: "success",
            subject: data.subject ?? null,
            scopes: data.scopes,
            expiresAt: data.expires_at,
            iat: data.iat ?? null,
          })
        } catch (err: unknown) {
          if ((err as { name?: string })?.name === "AbortError") return
          dispatch({ type: "fail" })
          void navigate({
            to: "/login",
            search: { state: "", next: pathnameRef.current },
            replace: true,
          })
        } finally {
          if (!controller.signal.aborted) {
            markAuthProbeDone()
            setChecking(false)
          }
        }
      }

      checkAuth()
      return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    const status = useSessionStore((s) => s.status)
    const totpProvisioned = totpData?.totp_provisioned

    useEffect(() => {
      if (status === "connected" && totpProvisioned === false && pathname === "/terminal") {
        void navigate({
          to: "/config",
          search: { section: "TOTP Setup", returnTo: "/terminal" },
          replace: true,
        })
      }
    }, [status, totpProvisioned, pathname, navigate])

    if (checking) {
      return (
        <div className="ct-root ct-login">
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--fg-muted)", letterSpacing: "0.12em" }}>
            loading…
          </span>
        </div>
      )
    }

    return (
      <div className="h-full text-foreground antialiased">
        <Outlet />
        <RevokeModal />
      </div>
    )
  }

/** Root route: mounts `RootLayout` (session gate + revoke modal + outlet) above every other route. */
// eslint-disable-next-line react-refresh/only-export-components
export const Route = createRootRoute({
  component: RootLayout,
})
