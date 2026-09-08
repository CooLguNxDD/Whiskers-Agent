import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'

import { useTogglePluginMutation } from '@/hooks/usePlugins'
import { enablePlugin, disablePlugin } from '@/api/plugins'
import { disablePluginRoutes } from '@/api/routes'
import { bus } from '@/events/bus'

// Mock API modules (enable/disable specific fns; preserve other exports via importOriginal)
vi.mock('@/api/plugins', async (importOriginal) => {
  const mod = await importOriginal<typeof import('@/api/plugins')>()
  return {
    ...mod,
    enablePlugin: vi.fn(),
    disablePlugin: vi.fn(),
  }
})

vi.mock('@/api/routes', async (importOriginal) => {
  const mod = await importOriginal<typeof import('@/api/routes')>()
  return {
    ...mod,
    disablePluginRoutes: vi.fn(),
  }
})

vi.mock('@/events/bus', () => ({
  bus: { emit: vi.fn() },
}))

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  })
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children)
  return { queryClient, wrapper }
}

const samplePlugin = {
  id: 'p1',
  name: 'Test Plugin',
  version: '1.0.0',
  tier: 'free' as const,
  enabled: false,
  description: 'A test plugin',
}

const sampleResponse = {
  plugins: [samplePlugin],
  system_tier: 0,
  system_tier_name: 'LITE',
}

describe('useTogglePluginMutation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('optimistically updates the plugins cache before the mutation resolves', async () => {
    const { queryClient, wrapper } = createWrapper()

    // Pre-seed cache
    queryClient.setQueryData(['plugins'], sampleResponse)

    // Never-resolving promise so mutation does not settle
    const neverPromise = new Promise<never>(() => {
      /* never resolves */
    })
    vi.mocked(enablePlugin).mockReturnValue(neverPromise as unknown as ReturnType<typeof enablePlugin>)

    const { result } = renderHook(() => useTogglePluginMutation(), { wrapper })

    act(() => {
      result.current.mutate({ id: 'p1', enabled: true })
    })

    // Flush microtasks so async onMutate (await cancel + set) completes
    await act(async () => {})

    const data = queryClient.getQueryData<typeof sampleResponse>(['plugins'])
    expect(data?.plugins?.[0]?.enabled).toBe(true)
  })

  it('rolls back the cache on mutation error', async () => {
    const { queryClient, wrapper } = createWrapper()

    // Pre-seed cache with disabled
    queryClient.setQueryData(['plugins'], {
      plugins: [{ ...samplePlugin, enabled: false }],
      system_tier: 0,
      system_tier_name: 'LITE',
    })

    vi.mocked(enablePlugin).mockRejectedValue(new Error('enable failed'))

    const { result } = renderHook(() => useTogglePluginMutation(), { wrapper })

    act(() => {
      result.current.mutate({ id: 'p1', enabled: true })
    })

    await waitFor(() => expect(result.current.isError).toBe(true))

    const data = queryClient.getQueryData<typeof sampleResponse>(['plugins'])
    expect(data?.plugins?.[0]?.enabled).toBe(false)
  })

  it('emits plugin:toggled and calls disablePluginRoutes on a successful disable', async () => {
    const { queryClient, wrapper } = createWrapper()

    // Seed with enabled
    queryClient.setQueryData(['plugins'], {
      plugins: [{ ...samplePlugin, enabled: true }],
      system_tier: 0,
      system_tier_name: 'LITE',
    })

    vi.mocked(disablePlugin).mockResolvedValue({ ...samplePlugin, enabled: false } as unknown as Awaited<ReturnType<typeof disablePlugin>>)
    vi.mocked(disablePluginRoutes).mockResolvedValue({} as unknown as Awaited<ReturnType<typeof disablePluginRoutes>>)

    const { result } = renderHook(() => useTogglePluginMutation(), { wrapper })

    act(() => {
      result.current.mutate({ id: 'p1', enabled: false })
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(vi.mocked(bus.emit)).toHaveBeenCalledWith('plugin:toggled', {
      id: 'p1',
      enabled: false,
    })
    expect(vi.mocked(disablePluginRoutes)).toHaveBeenCalledWith('p1')
  })
})
