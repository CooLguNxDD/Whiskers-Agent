import { test, expect, type Page } from '@playwright/test'

const rootApiRe = (suffix: string) => new RegExp(`://[^/]+${suffix}`)

const summaryBody = {
  kpi: {
    total_calls: 1200,
    success_rate: 99.2,
    p50_latency: 24,
    p99_latency: 142,
    active_sessions: 3,
  },
  graph_kpi: {
    total_calls: 18,
    success_rate: 100,
    p50_latency: 400,
    p99_latency: 900,
  },
  series: {
    core: Array.from({ length: 24 }, (_, i) => i + 1),
    extensions: Array.from({ length: 24 }, () => 2),
    other: Array.from({ length: 24 }, () => 1),
    mcp: Array.from({ length: 24 }, (_, i) => (i === 12 ? 80 : 20)),
    graph: Array.from({ length: 24 }, (_, i) => (i === 12 ? 6 : 1)),
  },
  top_tools: [
    { name: "run_graph", calls: 40, trend: [1, 2, 3, 4], p99: "12ms" },
    { name: "web_search", calls: 22, trend: [2, 2, 3, 3], p99: "30ms" },
  ],
  recent_errors: [
    { code: "429", src: "search_plugin", msg: "rate limit exceeded", when: "5m ago" },
  ],
  top_models: [{ name: "claude-sonnet", calls: 90, pct: 90 }],
}

async function mockAnalyticsBackend(page: Page) {
  await page.route(/:\/\/[^/]+\/api\//, async (route) => {
    const method = route.request().method()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: method === 'GET' ? '{}' : '{"ok":true}',
    })
  })

  await page.route(rootApiRe('/api/admin/public/exists'), async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ exists: true }) })
  })
  await page.route(rootApiRe('/api/admin/public/login'), async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ redirect: '/' }) })
  })
  await page.route(rootApiRe('/api/admin/public/me'), async (route) => {
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
  })
  await page.route(rootApiRe('/api/admin/public/refresh'), async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) })
  })
  await page.route(rootApiRe('/api/health'), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'healthy',
        instance_name: 'e2e-node',
        uptime_seconds: 3600,
        plugins: { total: 1, enabled: 1 },
        tunnels: { total: 1, active: 1 },
        metrics: { success_rate: 100, p50_latency_ms: 12, p99_latency_ms: 40 },
        system_tier: 1,
        system_tier_name: 'LITE',
        checks: { registry: true, db: true, telemetry: true, proxies: true },
      }),
    })
  })
  await page.route(rootApiRe('/api/terminal/session_gated/totp/status'), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ totp_provisioned: true, status: 'ok', password_provisioned: true }),
    })
  })
  await page.route(rootApiRe('/api/logs/stream'), async (route) => {
    await route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
  })
  await page.route(rootApiRe('/api/plugins(\\?|$|$)'), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ plugins: [], system_tier: 1, system_tier_name: 'LITE' }),
    })
  })
  await page.route(rootApiRe('/api/catalog/session_gated(\\?|$|$)'), async (route) => {
    if (route.request().url().includes('/execute')) {
      await route.fallback()
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ revision: 1, etag: 'mock', operations: [] }),
    })
  })
  await page.route(rootApiRe('/api/catalog/session_gated/execute'), async (route) => {
    const raw = route.request().postData() ?? '{}'
    let body: { plugin_id?: string; operation_id?: string } = {}
    try {
      body = JSON.parse(raw) as { plugin_id?: string; operation_id?: string }
    } catch {
      body = {}
    }
    if (body.operation_id === 'api_analytics_summary') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(summaryBody) })
      return
    }
    if (body.operation_id === 'portfolio_ask_turns') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'ok',
          count: 1,
          turns: [
            {
              run_id: 'run-1',
              question: 'how does the tank work?',
              intent: 'focus_fish',
              ok: true,
              latency_ms: 80,
              visitor_session_id: 'vis-1',
              created_at: '2026-08-27T15:00:00Z',
            },
          ],
        }),
      })
      return
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })
}

test.describe('Analytics', () => {
  test('tabs, series toggles, and chart tooltip', async ({ page }) => {
    await mockAnalyticsBackend(page)
    await page.goto('/login')
    await page.fill('#username', 'admin')
    await page.fill('#password', 'admin-password')
    await page.click('button[type="submit"]')
    await expect(page).toHaveURL('/')

    await page.goto('/analytics')
    await expect(page.locator('.ct-page-title')).toContainText('Analytics')
    await expect(page.getByText('Primary product axis')).toBeVisible()

    const mcpToggle = page.getByRole('button', { name: 'MCP Calls' })
    const graphToggle = page.getByRole('button', { name: 'Graph Runs' })
    await expect(mcpToggle).toHaveAttribute('aria-pressed', 'true')
    await graphToggle.click()
    await expect(graphToggle).toHaveAttribute('aria-pressed', 'false')
    await mcpToggle.click()
    await expect(mcpToggle).toHaveAttribute('aria-pressed', 'true')

    const chart = page.locator('[data-slot="chart"]').first()
    await chart.hover({ position: { x: 180, y: 80 } })

    await page.getByRole('tab', { name: /Tools & Traffic/i }).click()
    await expect(page).toHaveURL(/tab=tools/)
    await expect(page.getByText('Tool Operations')).toBeVisible()

    await page.getByRole('tab', { name: /Ask Turns/i }).click()
    await expect(page).toHaveURL(/tab=ask_turns/)
    await expect(page.getByText('Visitor Ask Turns')).toBeVisible()

    await page.getByRole('tab', { name: /Errors & Health/i }).click()
    await expect(page).toHaveURL(/tab=errors/)
    await expect(page.getByText('Error Diagnostics & Trace Log')).toBeVisible()
  })
})
