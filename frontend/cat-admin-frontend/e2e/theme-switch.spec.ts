import { test, expect, type Page } from '@playwright/test'

/**
 * Mocks backend API calls required to reach /preferences as an authenticated user.
 * Mirrors the navigation.spec.ts mockBackend pattern (root-scoped regex routes).
 */
async function mockBackend(page: Page) {
  const rootApiRe = (suffix: string) => new RegExp(`://[^/]+${suffix}`)

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

  await page.route(rootApiRe('/api/admin/public/exists'), async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ exists: true }) })
  })

  await page.route(rootApiRe('/api/admin/public/login'), async (route) => {
    if (route.request().method() === 'POST') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ redirect: '/' }) })
    } else {
      await route.fallback()
    }
  })

  await page.route(rootApiRe('/api/admin/public/me'), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        subject: 'admin',
        scopes: ['*'],
        expires_at: Date.now() + 3600 * 1000,
        iat: Math.floor(Date.now() / 1000) - 60,
      }),
    })
  })

  await page.route(rootApiRe('/api/health'), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'healthy',
        instance_name: 'e2e-node',
        uptime_seconds: 100,
        plugins: { total: 0, enabled: 0 },
        tunnels: { total: 0, active: 0 },
        metrics: { success_rate: 100, p50_latency_ms: 0, p99_latency_ms: 0 },
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

  await page.route(rootApiRe('/api/plugins(\\?|$|$)'), async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ plugins: [], system_tier: 1, system_tier_name: 'LITE' }),
      })
    } else {
      await route.fallback()
    }
  })

  await page.route(rootApiRe('/api/logs/stream'), async (route) => {
    await route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
  })
}

async function login(page: Page) {
  await page.goto('/login')
  await page.fill('#username', 'admin')
  await page.fill('#password', 'admin-password')
  await page.click('button[type="submit"]')
  await expect(page).toHaveURL('/')
}

test.describe('Theme switching', () => {
  test('selecting a theme in the grid updates CSS vars and persists across reload', async ({ page }) => {
    await mockBackend(page)
    await login(page)
    await page.goto('/preferences')

    const bgBefore = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--bg').trim())

    // Switch to the "paper" theme via the registry-driven grid.
    await page.locator('.ct-theme', { hasText: 'Paper' }).click()

    await expect(async () => {
      const bg = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--bg').trim())
      expect(bg).not.toBe(bgBefore)
    }).toPass()

    const bgAfterSwitch = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--bg').trim())

    // Persisted choice survives a full reload.
    await page.reload()
    await expect(async () => {
      const bg = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--bg').trim())
      expect(bg).toBe(bgAfterSwitch)
    }).toPass()
  })

  test('custom theme dropdown shows empty state when no custom themes are registered', async ({ page }) => {
    await mockBackend(page)
    await login(page)
    await page.goto('/preferences')

    await expect(page.getByText(/No custom themes installed/)).toBeVisible()
  })
})
