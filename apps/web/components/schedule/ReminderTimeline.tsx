import type { AdaptiveSchedule, WindowStatus } from '@croniixx/shared-types'

import { isoToLocalHour } from '@/lib/dial-geometry'

/**
 * A linear twenty four hour strip of the same schedule the dial shows.
 *
 * The dial answers "where in the biological cycle". This answers "how much of
 * the day is spoken for", which is the question a patient on eight agents
 * actually asks. Same data, different question, so both earn their place.
 */

const STATUS_COLOR: Record<WindowStatus, string> = {
  optimal: '#10B981',
  acceptable: '#0EA5E9',
  suboptimal: '#F59E0B',
  contraindicated: '#EF4444',
}

export interface ReminderTimelineProps {
  schedule: AdaptiveSchedule
  className?: string
}

export function ReminderTimeline({ schedule, className }: ReminderTimelineProps) {
  const rows = schedule.entries.map((entry) => {
    const start = isoToLocalHour(entry.window.start, schedule.timezone)
    const end = isoToLocalHour(entry.window.end, schedule.timezone)
    const target = isoToLocalHour(entry.window.target, schedule.timezone)
    // A window crossing midnight is clipped at the strip edge rather than
    // wrapped, because a bar that reappears on the left reads as a second dose.
    const span = end > start ? end - start : 24 - start
    return { entry, start, span, target }
  })

  return (
    <section className={`panel ${className ?? ''}`}>
      <header className="panel-header">
        <span className="label">Twenty four hour view</span>
        <span className="label">{schedule.timezone}</span>
      </header>

      <div className="px-4 pb-4 pt-3">
        <div className="relative mb-2 flex justify-between">
          {[0, 6, 12, 18, 24].map((hour) => (
            <span key={hour} className="font-mono text-micro text-ink-faint">
              {String(hour % 24).padStart(2, '0')}
            </span>
          ))}
        </div>

        <div className="space-y-1.5">
          {rows.map(({ entry, start, span, target }) => (
            <div key={entry.entry_id} className="grid grid-cols-[9rem_1fr] items-center gap-3">
              <span className="truncate text-micro text-ink-muted" title={entry.display_name}>
                {entry.display_name}
              </span>
              <div className="relative h-4 border border-line bg-base">
                {[6, 12, 18].map((hour) => (
                  <span
                    key={hour}
                    aria-hidden
                    className="absolute top-0 h-full w-px bg-line"
                    style={{ left: `${(hour / 24) * 100}%` }}
                  />
                ))}
                <span
                  className="absolute top-0 h-full"
                  style={{
                    left: `${(start / 24) * 100}%`,
                    width: `${(span / 24) * 100}%`,
                    backgroundColor: STATUS_COLOR[entry.window.status],
                    opacity: 0.65,
                  }}
                  title={`${entry.display_name} window`}
                />
                <span
                  className="absolute top-0 h-full w-px bg-ink"
                  style={{ left: `${(target / 24) * 100}%` }}
                  title="Target"
                />
              </div>
            </div>
          ))}
        </div>

        {rows.length === 0 && (
          <p className="py-6 text-sm text-ink-muted">Nothing scheduled in this horizon.</p>
        )}
      </div>
    </section>
  )
}
