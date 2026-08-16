import type { ScheduleEntry } from '@croniixx/shared-types'
import { ANCHOR_LABELS, DRUG_CLASS_LABELS } from '@croniixx/shared-types'

import { formatHour, formatOffset, isoToLocalHour } from '@/lib/dial-geometry'

/**
 * One dose in detail: the window, what it is anchored to, and why.
 *
 * The anchor is stated because it is the reasoning. A window at 07:40 is a
 * result; "wake plus forty minutes" is the rule that produced it, and it is
 * the part a clinician can agree or disagree with.
 */

export interface DrugTimingCardProps {
  entry: ScheduleEntry
  timeZone: string
  className?: string
}

const STATUS_COLOR = {
  optimal: '#10B981',
  acceptable: '#0EA5E9',
  suboptimal: '#F59E0B',
  contraindicated: '#EF4444',
} as const

export function DrugTimingCard({ entry, timeZone, className }: DrugTimingCardProps) {
  const color = STATUS_COLOR[entry.window.status]
  const start = isoToLocalHour(entry.window.start, timeZone)
  const end = isoToLocalHour(entry.window.end, timeZone)
  const target = isoToLocalHour(entry.window.target, timeZone)

  return (
    <article className={`panel ${className ?? ''}`}>
      <header className="panel-header">
        <span className="flex items-center gap-2">
          <span aria-hidden className="inline-block h-3 w-1" style={{ backgroundColor: color }} />
          <span className="text-sm text-ink">{entry.display_name}</span>
        </span>
        <span className="label" style={{ color }}>
          {entry.window.status}
        </span>
      </header>

      <div className="grid grid-cols-3 border-b border-line">
        <Metric label="Opens" value={formatHour(start)} />
        <Metric label="Target" value={formatHour(target)} accent borderLeft />
        <Metric label="Closes" value={formatHour(end)} borderLeft />
      </div>

      <dl className="divide-y divide-line">
        <Row label="Class" value={DRUG_CLASS_LABELS[entry.drug_class]} />
        <Row label="Dose" value={`${entry.dose_amount} ${entry.dose_unit}`} />
        <Row
          label="Anchor"
          value={`${ANCHOR_LABELS[entry.window.anchor]} ${formatOffset(entry.window.anchor_offset_min)}`}
        />
        <Row label="Span" value={`${entry.window.duration_min} min`} />
        {entry.drift_from_conventional_min !== null && (
          <Row
            label="Against printed"
            value={formatOffset(entry.drift_from_conventional_min)}
            accent
          />
        )}
        <Row label="Confidence" value={entry.confidence.toFixed(2)} />
      </dl>

      {entry.window.rationale && (
        <p className="border-t border-line px-4 py-3 text-sm leading-relaxed text-ink-muted">
          {entry.window.rationale}
        </p>
      )}

      {entry.avoid_windows.length > 0 && (
        <div className="border-t border-contra/40 bg-contra/5 px-4 py-2">
          <span className="label text-contra">Avoid</span>
          <ul className="mt-1">
            {entry.avoid_windows.map((window) => (
              <li key={window.start} className="font-mono text-micro text-contra">
                {formatHour(isoToLocalHour(window.start, timeZone))} to{' '}
                {formatHour(isoToLocalHour(window.end, timeZone))}
              </li>
            ))}
          </ul>
        </div>
      )}
    </article>
  )
}

function Metric({
  label,
  value,
  accent,
  borderLeft,
}: {
  label: string
  value: string
  accent?: boolean
  borderLeft?: boolean
}) {
  return (
    <div className={`px-4 py-3 ${borderLeft ? 'border-l border-line' : ''}`}>
      <span className="label block">{label}</span>
      <span className={`value mt-1 block text-lg ${accent ? 'text-circadian' : ''}`}>{value}</span>
    </div>
  )
}

function Row({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-4 px-4 py-2">
      <dt className="label">{label}</dt>
      <dd className={`value text-sm ${accent ? 'text-circadian' : ''}`}>{value}</dd>
    </div>
  )
}
