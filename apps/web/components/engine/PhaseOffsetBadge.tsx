import type { PhaseEstimate } from '@croniixx/shared-types'

import { formatOffset } from '@/lib/dial-geometry'

/**
 * The phase offset, stated plainly.
 *
 * A chronobiologist reads the number and the sign and knows the patient's
 * situation. Everything else on this badge is provenance: how confident the
 * estimate is, what produced it, and how old it is. Those matter because an
 * offset from a thin fortnight of data and an offset from a validated model
 * on complete data are different objects that happen to look the same.
 */

export interface PhaseOffsetBadgeProps {
  phase: Pick<
    PhaseEstimate,
    | 'phase_offset_min'
    | 'direction'
    | 'confidence'
    | 'method_version'
    | 'coefficient_source'
    | 'computed_at'
    | 'dlmo_estimate'
  >
  size?: 'compact' | 'full'
  className?: string
}

const DIRECTION_NOTE: Record<PhaseEstimate['direction'], string> = {
  delayed: 'Biological night starts later than the calendar day',
  advanced: 'Biological night starts earlier than the calendar day',
  aligned: 'Within half an hour of the population reference',
}

export function PhaseOffsetBadge({ phase, size = 'full', className }: PhaseOffsetBadgeProps) {
  const offset = formatOffset(phase.phase_offset_min)
  const validated = phase.coefficient_source === 'private_validated'
  const accent =
    phase.direction === 'aligned' ? 'text-ink' : 'text-circadian'

  if (size === 'compact') {
    return (
      <span
        className={`inline-flex items-baseline gap-2 border border-line px-2 py-1 ${className ?? ''}`}
      >
        <span className={`num font-mono text-sm ${accent}`}>{offset}</span>
        <span className="label">{phase.direction}</span>
      </span>
    )
  }

  return (
    <div className={`panel ${className ?? ''}`}>
      <div className="panel-header">
        <span className="label">Circadian phase offset</span>
        <span className="label">{validated ? 'Validated' : 'Reference'}</span>
      </div>

      <div className="px-4 py-5">
        <div className="flex items-baseline gap-3">
          <span className={`num font-mono text-5xl leading-none ${accent}`}>{offset}</span>
          <span className="font-mono text-micro uppercase tracking-label text-ink-muted">
            {phase.direction}
          </span>
        </div>

        <p className="mt-3 max-w-sm text-sm leading-relaxed text-ink-muted">
          {DIRECTION_NOTE[phase.direction]}
        </p>
      </div>

      <dl className="grid grid-cols-2 border-t border-line">
        <Cell label="Confidence" value={phase.confidence.toFixed(2)} />
        <Cell
          label="DLMO"
          value={phase.dlmo_estimate ? shortTime(phase.dlmo_estimate) : 'not estimated'}
          borderLeft
        />
        <Cell label="Method" value={phase.method_version} borderTop />
        <Cell label="Computed" value={shortTime(phase.computed_at)} borderTop borderLeft />
      </dl>

      {!validated && (
        <p className="border-t border-drift/40 bg-drift/5 px-4 py-2 font-mono text-micro text-drift">
          Reference coefficients. Not for clinical dosing decisions.
        </p>
      )}
    </div>
  )
}

function Cell({
  label,
  value,
  borderTop,
  borderLeft,
}: {
  label: string
  value: string
  borderTop?: boolean
  borderLeft?: boolean
}) {
  return (
    <div
      className={`px-4 py-3 ${borderTop ? 'border-t border-line' : ''} ${
        borderLeft ? 'border-l border-line' : ''
      }`}
    >
      <dt className="label">{label}</dt>
      <dd className="value mt-1 truncate text-sm" title={value}>
        {value}
      </dd>
    </div>
  )
}

function shortTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toISOString().slice(5, 16).replace('T', ' ')
}
