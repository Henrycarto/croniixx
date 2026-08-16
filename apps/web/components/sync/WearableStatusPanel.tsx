import type { WearableLinkStatus } from '@croniixx/shared-types'
import { PROVIDER_LABELS, STALE_AFTER_SECONDS } from '@croniixx/shared-types'

import { DeviceBadge } from './DeviceBadge'

/**
 * Connected wearables and how recently each one delivered.
 *
 * Staleness is the number that matters. A phase estimate is only as current as
 * the data under it, and a ring left on a bedside table for two days will keep
 * producing a confident looking offset that describes last week.
 */

export interface WearableStatusPanelProps {
  links: WearableLinkStatus[]
  className?: string
}

export function WearableStatusPanel({ links, className }: WearableStatusPanelProps) {
  const active = links.filter((link) => link.active)
  const stale = active.filter(
    (link) => link.staleness_s === null || link.staleness_s > STALE_AFTER_SECONDS,
  )

  return (
    <section className={`panel ${className ?? ''}`}>
      <header className="panel-header">
        <span className="label">Wearable ingestion</span>
        <span className="label">
          {active.length} active{stale.length > 0 ? ` · ${stale.length} stale` : ''}
        </span>
      </header>

      {links.length === 0 ? (
        <p className="px-4 py-8 text-sm text-ink-muted">
          No devices connected. A circadian profile needs at least one wearable reporting
          sleep and heart rate variability.
        </p>
      ) : (
        <ul>
          {links.map((link) => {
            const isStale =
              link.staleness_s === null || link.staleness_s > STALE_AFTER_SECONDS
            return (
              <li
                key={link.terra_user_id}
                className="flex items-center justify-between gap-4 border-b border-line px-4 py-3 last:border-b-0"
              >
                <div className="flex min-w-0 flex-col gap-2">
                  <DeviceBadge
                    provider={link.provider}
                    active={link.active}
                    stale={isStale}
                  />
                  <span className="font-mono text-micro text-ink-faint">
                    {link.terra_user_id.slice(0, 18)}
                  </span>
                </div>

                <div className="text-right">
                  <span className="label block">Last payload</span>
                  <span
                    className="value block text-sm"
                    style={{ color: isStale ? '#F59E0B' : '#E6EAF4' }}
                  >
                    {formatStaleness(link.staleness_s)}
                  </span>
                  <span className="block font-mono text-micro text-ink-faint">
                    {link.active ? PROVIDER_LABELS[link.provider] : 'disconnected'}
                  </span>
                </div>
              </li>
            )
          })}
        </ul>
      )}

      {stale.length > 0 && (
        <p className="border-t border-drift/40 bg-drift/5 px-4 py-2 font-mono text-micro text-drift">
          {stale.length === 1 ? 'One device has' : `${stale.length} devices have`} gone quiet.
          The phase estimate is running on older data than it appears.
        </p>
      )}
    </section>
  )
}

function formatStaleness(seconds: number | null): string {
  if (seconds === null) return 'never'
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}
