'use client'

import { useMemo } from 'react'
import type { AdaptiveSchedule, ScheduleEntry, WindowStatus } from '@croniixx/shared-types'
import { ANCHOR_LABELS, DRUG_CLASS_LABELS } from '@croniixx/shared-types'

import { formatOffset, isoToLocalHour, formatHour } from '@/lib/dial-geometry'

/**
 * Today's regimen as a vertical timeline.
 *
 * One row per dose, in chronological order: drug, window open, duration, and
 * a status bar. The right hand column carries the number that makes the
 * argument, which is how far the biological window sits from the time a
 * printed schedule would have given.
 */

const STATUS_COLOR: Record<WindowStatus, string> = {
  optimal: '#10B981',
  acceptable: '#0EA5E9',
  suboptimal: '#F59E0B',
  contraindicated: '#EF4444',
}

export interface AdaptiveSchedulePanelProps {
  schedule: AdaptiveSchedule
  selectedEntryId?: string | null
  onSelectEntry?: (entryId: string) => void
  className?: string
}

export function AdaptiveSchedulePanel({
  schedule,
  selectedEntryId = null,
  onSelectEntry,
  className,
}: AdaptiveSchedulePanelProps) {
  const entries = useMemo(
    () =>
      [...schedule.entries].sort(
        (a, b) => new Date(a.window.target).getTime() - new Date(b.window.target).getTime(),
      ),
    [schedule.entries],
  )

  return (
    <section className={`panel flex flex-col ${className ?? ''}`}>
      <header className="panel-header">
        <div className="flex items-baseline gap-3">
          <span className="label">Adaptive schedule</span>
          <span className="value text-micro">{schedule.timezone}</span>
        </div>
        <span className="label">
          {entries.length} {entries.length === 1 ? 'dose' : 'doses'}
        </span>
      </header>

      {schedule.meta.provisional && (
        <p className="border-b border-drift/40 bg-drift/5 px-4 py-2 font-mono text-micro text-drift">
          Provisional schedule. {schedule.meta.warnings[0] ?? ''}
        </p>
      )}

      <div className="grid grid-cols-[1fr_auto_auto_auto] gap-x-4 border-b border-line px-4 py-2">
        <span className="label">Agent</span>
        <span className="label text-right">Window</span>
        <span className="label text-right">Span</span>
        <span className="label text-right">Shift</span>
      </div>

      {entries.length === 0 ? (
        <p className="px-4 py-8 text-sm text-ink-muted">
          No doses fall inside the current schedule horizon.
        </p>
      ) : (
        <ol className="flex-1 overflow-y-auto">
          {entries.map((entry) => (
            <ScheduleRow
              key={entry.entry_id}
              entry={entry}
              timeZone={schedule.timezone}
              selected={entry.entry_id === selectedEntryId}
              onSelect={onSelectEntry}
            />
          ))}
        </ol>
      )}

      <footer className="flex items-center justify-between border-t border-line px-4 py-2">
        <span className="label">Generated</span>
        <span className="value text-micro">
          {new Date(schedule.generated_at).toISOString().slice(0, 16).replace('T', ' ')}
        </span>
      </footer>
    </section>
  )
}

function ScheduleRow({
  entry,
  timeZone,
  selected,
  onSelect,
}: {
  entry: ScheduleEntry
  timeZone: string
  selected: boolean
  onSelect?: (entryId: string) => void
}) {
  const status = entry.window.status
  const color = STATUS_COLOR[status]
  const startHour = isoToLocalHour(entry.window.start, timeZone)
  const targetHour = isoToLocalHour(entry.window.target, timeZone)
  const drift = entry.drift_from_conventional_min

  return (
    <li>
      <button
        type="button"
        onClick={onSelect ? () => onSelect(entry.entry_id) : undefined}
        className={`grid w-full grid-cols-[1fr_auto_auto_auto] items-center gap-x-4 border-b border-line px-4 py-3 text-left transition-colors ${
          selected ? 'bg-surface-raised' : 'hover:bg-surface-raised/60'
        }`}
      >
        <span className="flex min-w-0 items-center gap-3">
          {/* The status bar is the only colour in the row, so a row scan reads
              as a column of window states rather than as decorated text. */}
          <span
            aria-hidden
            className="h-8 w-1 shrink-0"
            style={{ backgroundColor: color }}
          />
          <span className="min-w-0">
            <span className="block truncate text-sm text-ink">{entry.display_name}</span>
            <span className="block truncate font-mono text-micro text-ink-faint">
              {DRUG_CLASS_LABELS[entry.drug_class]} · {entry.dose_amount}
              {entry.dose_unit} · {ANCHOR_LABELS[entry.window.anchor]}
            </span>
          </span>
        </span>

        <span className="text-right">
          <span className="value block text-sm">{formatHour(targetHour)}</span>
          <span className="block font-mono text-micro text-ink-faint">
            opens {formatHour(startHour)}
          </span>
        </span>

        <span className="value text-right text-sm">{entry.window.duration_min}m</span>

        <span className="text-right">
          {drift === null ? (
            <span className="font-mono text-micro text-ink-faint">n/a</span>
          ) : (
            <span
              className="value block text-sm"
              style={{ color: Math.abs(drift) >= 60 ? '#7C3AED' : '#8A96B2' }}
              title="Distance from the time a printed schedule would have given"
            >
              {formatOffset(drift)}
            </span>
          )}
        </span>
      </button>
    </li>
  )
}
