import { test, expect, type Page } from '@playwright/test'
import { PLUGIN_IDS } from '../src/constants/plugins'

/**
 * Mocks all backend API calls required for navigation tests using page.route.
 * Sets up stateful plugin fixtures and auth flows.
 * Use { authenticated: false } for the unauthenticated redirect test.
 */
async function mockBackend(page: Page, { authenticated = true }: { authenticated?: boolean } = {}) {
  // Stateful plugins (updated by toggle handlers; returned by list)
  const pluginsState = [
    {
      id: PLUGIN_IDS.TERMINAL_RELAY,
      name: 'Terminal Relay Plugin',
      version: '1.2.0',
      tier: 'free' as const,
      enabled: true,
      description: 'Terminal relay and remote execution tools',
    },
    {
      id: 'jules_plugin',
      name: 'Jules CI',
      version: '0.8.5',
      tier: 'pro' as const,
      enabled: false,
      description: 'Code review automation',
    },
  ]
  const system = { system_tier: 1, system_tier_name: 'LITE' }

  // Helper: regex that matches ONLY root-level backend API calls (/api/..., /admin/...), never /src/api/... or Vite assets
  const rootApiRe = (suffix: string) => new RegExp(`://[^/]+${suffix}`)

  // Catch-alls registered FIRST: Playwright resolves page.route handlers LIFO
  // (most-recently-registered wins), so register broad fallbacks before the
  // specific handlers below, letting the specific ones take precedence.
  await page.route(/:\/\/[^/]+\/api\//, async (route) => {
    const method = route.request().method()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: method === 'GET' ? '{}' : '{"ok":true}',
    })
  })
  await page.route(/:\/\/[^/]+\/admin\//, async (route) => {
    const method = route.request().method()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: method === 'GET' ? '{}' : '{"ok":true}',
    })
  })


  // /api/catalog
  await page.route(rootApiRe('/api/catalog'), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        revision: 1,
        etag: "mock",
        operations: [
          {
            plugin_id: "api.plugins",
            operation_id: "api_list_plugins",
            http: { method: "GET", path_template: "/api/plugins" }
          },
          {
            plugin_id: "api.plugins",
            operation_id: "api_enable_plugin",
            http: { method: "POST", path_template: "/api/plugins/{plugin_id}/enable" }
          },
          {
            plugin_id: "api.plugins",
            operation_id: "api_disable_plugin",
            http: { method: "DELETE", path_template: "/api/plugins/{plugin_id}/enable" }
          }
        ]
      })
    })
  })

  // /api/admin/public/exists — always true so login does not redirect to signup
  await page.route(rootApiRe('/api/admin/public/exists'), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ exists: true }),
    })
  })

  // /api/admin/public/login — success with redirect for full nav
  await page.route(rootApiRe('/api/admin/public/login'), async (route) => {
    if (route.request().method() === 'POST') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ redirect: '/' }),
      })
    } else {
      await route.fallback()
    }
  })

  // /api/admin/public/me — root auth guard (drives unauth redirect to /login)
  await page.route(rootApiRe('/api/admin/public/me'), async (route) => {
    if (authenticated) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          subject: 'admin',
          scopes: ['*'],
          expires_at: Date.now() + 3600 * 1000,
          iat: Math.floor(Date.now() / 1000) - 840,
        }),
      })
    } else {
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'unauthorized' }),
      })
    }
  })

  // GET /api/health — shell Live strip + node pill
  await page.route(rootApiRe('/api/health'), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'healthy',
        instance_name: 'e2e-node',
        uptime_seconds: 3600,
        plugins: { total: pluginsState.length, enabled: pluginsState.filter((p) => p.enabled).length },
        tunnels: { total: 1, active: 1 },
        metrics: { success_rate: 100, p50_latency_ms: 12, p99_latency_ms: 40 },
        system_tier: system.system_tier,
        system_tier_name: system.system_tier_name,
        checks: { registry: true, db: true, telemetry: true, proxies: true },
      }),
    })
  })

  // /api/admin/public/refresh — client may call on 401 paths
  await page.route(rootApiRe('/api/admin/public/refresh'), async (route) => {
    if (route.request().method() === 'POST') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true }),
      })
    } else {
      await route.fallback()
    }
  })

  // totp status (called by root after successful /api/admin/public/me)
  await page.route(rootApiRe('/api/terminal/session_gated/totp/status'), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ totp_provisioned: true, status: 'ok', password_provisioned: true }),
    })
  })

  // enable/disable — specific first
  await page.route(rootApiRe('/api/plugins/[^/]+/enable'), async (route) => {
    const url = route.request().url()
    const method = route.request().method()
    const match = url.match(/\/api\/plugins\/([^/]+)\/enable/)
    const id = match ? decodeURIComponent(match[1]) : ''
    const plugin = pluginsState.find((p) => p.id === id)
    if (!plugin) {
      await route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: 'not found' }) })
      return
    }
    if (method === 'POST') {
      plugin.enabled = true
    } else if (method === 'DELETE') {
      plugin.enabled = false
    } else {
      await route.fallback()
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ...plugin }),
    })
  })

  // GET /api/plugins (dashboard) — returns current stateful list
  await page.route(rootApiRe('/api/plugins(\\?|$|$)'), async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ plugins: pluginsState, ...system }),
      })
    } else {
      await route.fallback()
    }
  })

  // Playground tool registry (McpMode + useToolRegistry on /playground)
  await page.route(rootApiRe('/api/playground/tools'), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tools: [] }),
    })
  })

  // Chat sessions list (McpMode mount)
  await page.route(rootApiRe('/api/playground/chat/sessions'), async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ sessions: [] }),
      })
    } else {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    }
  })

  // MCP token (may be fetched by mcpClient on connect paths)
  await page.route(rootApiRe('/api/playground/mcp-token'), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ access_token: '', resource: '' }),
    })
  })

  // Live logs SSE (AppShell always mounts LiveLogs with awake=true)
  await page.route(rootApiRe('/api/logs/stream'), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: '',
    })
  })
}

test.describe('Operator Dashboard Navigation', () => {
  test('should redirect unauthenticated users to login', async ({ page }) => {
    await mockBackend(page, { authenticated: false })
    await page.goto('/')
    await expect(page).toHaveURL(/\/login/)
  })

  test('should carry login credentials and access the dashboard', async ({ page }) => {
    await mockBackend(page)
    await page.goto('/login')

    // Fill credentials form using robust ID selectors
    await page.fill('#username', 'admin') // username
    await page.fill('#password', 'admin-password') // password
    await page.click('button[type="submit"]')

    // Should successfully redirect to dashboard (full window.location after login)
    await expect(page).toHaveURL('/')
    await expect(page.locator('.ct-page-title')).toContainText('Console')
  })

  test('should navigate to playground and configuration sections', async ({ page }) => {
    await mockBackend(page)
    // Authenticate
    await page.goto('/login')
    await page.fill('#username', 'admin')
    await page.fill('#password', 'admin-password')
    await page.click('button[type="submit"]')

    // Navigate to Playground via the sidebar navigation button
    await page.click('button:has-text("Playground")')
    await expect(page).toHaveURL(/\/playground/)

    // Check tabs in Playground (MCP, Tool Test, Group Test) using exact name match to avoid strict mode violations
    await expect(page.locator('text=MCP').first()).toBeVisible()
    await expect(page.locator('text=Tool Test').first()).toBeVisible()
  })

  })
