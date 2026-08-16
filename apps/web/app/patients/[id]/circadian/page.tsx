import Link from 'next/link'

import { LiveDial } from '@/components/engine/LiveDial'
import { PhaseOffsetBadge } from '@/components/engine/PhaseOffsetBadge'
import { AdaptiveSchedulePanel } from '@/components/schedule/AdaptiveSchedulePanel'
import { IngestionFeed } from '@/components/sync/IngestionFeed'
import { WearableStatusPanel } from '@/components/sync/WearableStatusPanel'
import { AppShell, DemoNotice } from '@/components/ui/AppShell'
import { getCurrentSchedule, getIngestionFeed, getPhase, getWearableStatus } from '@/lib/api'
import { toDialWindows, toSleepSegments } from '@/lib/dial-adapters'
import {
  SAMPLE_TIMEZONE,
  sampleFeed,
  samplePhase,
  sampleSchedule,
  sampleSleepSegments,
  sampleWearables,
} from '@/lib/sample-patient'

/**
 * The core screen. Dial on the left, schedule on the right.
 *
 * Both halves render the same underlying object from different angles: the
 * dial shows where in the biological cycle each window sits, the panel shows
 * the same windows as a list a clinician can act on. Neither is a summary of
 * the other.
 */

export const dynamic = 'force-dynamic'

export default async function CircadianPage({ params }: { params: { id: string } }) {
  const [phaseResponse, scheduleResponse, wearableResponse, feedResponse] = await Promise.all([
    getPhase(params.id),
    getCurrentSchedule(params.id),
    getWearableStatus(params.id),
    getIngestionFeed(params.id, 20),
  ])

  // Services unreachable means demonstration data, said out loud rather than
  // rendered as if it were this patient's record.
  const live = phaseResponse.data !== null && scheduleResponse.data !== null
  const phase = phaseResponse.data ?? samplePhase
  const schedule = scheduleResponse.data ?? sampleSchedule
  const wearables = wearableResponse.data ?? sampleWearables
  const feed = feedResponse.data ?? sampleFeed

  const timeZone = live ? schedule.timezone : SAMPLE_TIMEZONE
  const sleepSegments = live ? toSleepSegments(schedule) : sampleSleepSegments
  const drugWindows = toDialWindows(schedule, timeZone)

  return (
    <AppShell
      patientLabel={params.id}
      actions={
        <Link href={`/patients/${params.id}/schedule`} className="action">
          Schedule detail
        </Link>
      }
    >
      {!live && <DemoNotice className="mb-6" />}

      <div className="grid gap-6 xl:grid-cols-2">
        <section className="panel">
          <header className="panel-header">
            <span className="label">Circadian clock</span>
            <span className="label">{timeZone}</span>
          </header>
          <div className="flex justify-center px-4 py-6">
            <LiveDial
              phaseOffsetMin={phase.phase_offset_min}
              sleepSegments={sleepSegments}
              drugWindows={drugWindows}
              markers={
                phase.dlmo_estimate
                  ? [{ hour: dlmoHour(phase.dlmo_estimate, timeZone), label: 'DLMO', kind: 'dlmo' }]
                  : []
              }
              timeZone={timeZone}
              size={560}
            />
          </div>
        </section>

        <AdaptiveSchedulePanel schedule={schedule} className="min-h-[520px]" />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-3">
        <PhaseOffsetBadge phase={phase} />
        <WearableStatusPanel links={wearables} />
        <IngestionFeed events={feed} limit={6} />
      </div>
    </AppShell>
  )
}

function dlmoHour(iso: string, timeZone: string): number {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return 0
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(date)
  const read = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? '0')
  return (read('hour') % 24) + read('minute') / 60
}
