import Link from 'next/link'

import { DrugTimingCard } from '@/components/engine/DrugTimingCard'
import { AdaptiveSchedulePanel } from '@/components/schedule/AdaptiveSchedulePanel'
import { ReminderTimeline } from '@/components/schedule/ReminderTimeline'
import { AppShell, DemoNotice } from '@/components/ui/AppShell'
import { getCurrentSchedule } from '@/lib/api'
import { sampleSchedule } from '@/lib/sample-patient'

export const dynamic = 'force-dynamic'

export default async function SchedulePage({ params }: { params: { id: string } }) {
  const response = await getCurrentSchedule(params.id)
  const live = response.data !== null
  const schedule = response.data ?? sampleSchedule

  return (
    <AppShell
      patientLabel={params.id}
      actions={
        <Link href={`/patients/${params.id}/circadian`} className="action">
          Circadian view
        </Link>
      }
    >
      {!live && <DemoNotice className="mb-6" />}

      <div className="grid gap-6 xl:grid-cols-[1fr_1.2fr]">
        <div className="space-y-6">
          <AdaptiveSchedulePanel schedule={schedule} />
          <ReminderTimeline schedule={schedule} />
        </div>

        <div className="space-y-6">
          <section className="panel">
            <header className="panel-header">
              <span className="label">Schedule provenance</span>
              <span className="label">v{schedule.schedule_version}</span>
            </header>
            <dl className="divide-y divide-line">
              <Row label="Schedule id" value={schedule.schedule_id} />
              <Row label="Supersedes" value={schedule.supersedes ?? 'first for this patient'} />
              <Row label="Method" value={schedule.meta.method_version} />
              <Row
                label="Coefficients"
                value={
                  schedule.meta.coefficient_source === 'private_validated'
                    ? 'validated'
                    : 'reference'
                }
              />
              <Row
                label="Profile completeness"
                value={schedule.meta.profile_completeness.toFixed(2)}
              />
              <Row label="Phase confidence" value={schedule.meta.phase_confidence.toFixed(2)} />
              <Row label="Valid until" value={schedule.valid_until.slice(0, 16).replace('T', ' ')} />
            </dl>

            {schedule.meta.warnings.length > 0 && (
              <ul className="border-t border-line px-4 py-3">
                {schedule.meta.warnings.map((warning) => (
                  <li key={warning} className="py-1 font-mono text-micro leading-relaxed text-drift">
                    {warning}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <div className="grid gap-6 md:grid-cols-2">
            {schedule.entries.map((entry) => (
              <DrugTimingCard
                key={entry.entry_id}
                entry={entry}
                timeZone={schedule.timezone}
              />
            ))}
          </div>
        </div>
      </div>
    </AppShell>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-6 px-4 py-2">
      <dt className="label shrink-0">{label}</dt>
      <dd className="value truncate text-right text-sm" title={value}>
        {value}
      </dd>
    </div>
  )
}
