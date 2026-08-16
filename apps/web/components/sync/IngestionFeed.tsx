import type { IngestionEvent } from '@croniixx/shared-types'
import { PROVIDER_LABELS } from '@croniixx/shared-types'

/**
 * Recent payloads from the ingestion loop.
 *
 * Warnings are shown inline rather than collapsed. A note that a device could
 * not stage sleep, or that rMSSD was derived rather than measured, changes how
 * much weight the phase estimate deserves, and burying it behind a click means
 * nobody sees it.
 */

export interface IngestionFeedProps {
  events: IngestionEvent[]
  limit?: number
  className?: string
}

export function IngestionFeed({ events, limit = 12, className }: IngestionFeedProps) {
  const visible = events.slice(0, limit)

  return (
    <section className={`panel ${className ?? ''}`}>
      <header className="panel-header">
        <span className="label">Ingestion feed</span>
        <span className="label">{events.length} events</span>
      </header>

      {visible.length === 0 ? (
        <p className="px-4 py-8 text-sm text-ink-muted">
          No payloads received yet. Terra pushes data as devices sync, which for most rings
          is a few times a day.
        </p>
      ) : (
        <ol>
          {visible.map((event, index) => (
            <li
              key={`${event.at}-${index}`}
              className="border-b border-line px-4 py-2.5 last:border-b-0"
            >
              <div className="flex items-baseline justify-between gap-4">
                <span className="flex items-baseline gap-2">
                  <span
                    aria-hidden
                    className="inline-block h-1.5 w-1.5"
                    style={{ backgroundColor: '#0EA5E9' }}
                  />
                  <span className="font-mono text-micro text-stream">
                    {PROVIDER_LABELS[event.provider]}
                  </span>
                  <span className="font-mono text-micro text-ink-faint">
                    {event.payload_type}
                  </span>
                </span>
                <span className="value text-micro text-ink-faint">{clock(event.at)}</span>
              </div>

              <div className="mt-1 flex gap-4 pl-3.5">
                <span className="font-mono text-micro text-ink-muted">
                  {event.samples} samples
                </span>
                {event.sleep_sessions > 0 && (
                  <span className="font-mono text-micro text-ink-muted">
                    {event.sleep_sessions} sleep
                  </span>
                )}
              </div>

              {event.warnings.length > 0 && (
                <ul className="mt-1 pl-3.5">
                  {event.warnings.map((warning) => (
                    <li key={warning} className="font-mono text-micro leading-relaxed text-drift">
                      {warning}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

function clock(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toISOString().slice(11, 16)
}
