import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

import StatsStrip from '../StatsStrip'

vi.mock('@/hooks/usePlugins')

import { usePluginsQuery, useSystemTier } from '@/hooks/usePlugins'

describe('StatsStrip', () => {
  it('renders total/enabled counts from plugin list', () => {
    vi.mocked(usePluginsQuery).mockReturnValue({
      data: {
        plugins: [{ enabled: true }, { enabled: true }, { enabled: false }],
        system_tier_name: 'PRO',
      },
      isPending: false,
    } as unknown as ReturnType<typeof usePluginsQuery>)

    vi.mocked(useSystemTier).mockReturnValue('PRO')

    render(<StatsStrip />)

    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(screen.getByText('PRO')).toBeInTheDocument()
    expect(screen.getByText('Total')).toBeInTheDocument()
    expect(screen.getByText('Enabled')).toBeInTheDocument()
    expect(screen.getByText('Tier')).toBeInTheDocument()
  })

  it('falls back to LITE tier and zero counts when no data', () => {
    vi.mocked(usePluginsQuery).mockReturnValue({
      data: undefined,
      isPending: false,
    } as unknown as ReturnType<typeof usePluginsQuery>)

    vi.mocked(useSystemTier).mockReturnValue(undefined)

    render(<StatsStrip />)

    const zeros = screen.getAllByText('0')
    expect(zeros).toHaveLength(2)
    expect(screen.getByText('LITE')).toBeInTheDocument()
  })

  it('renders skeleton while pending', () => {
    vi.mocked(usePluginsQuery).mockReturnValue({
      data: undefined,
      isPending: true,
    } as unknown as ReturnType<typeof usePluginsQuery>)

    render(<StatsStrip />)

    expect(screen.queryByText('Total')).not.toBeInTheDocument()
    expect(screen.queryByText('Enabled')).not.toBeInTheDocument()
    expect(screen.queryByText('Tier')).not.toBeInTheDocument()
  })
})
