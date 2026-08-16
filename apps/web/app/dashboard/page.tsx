import Link from 'next/link'

import { IngestionFeed } from '@/components/sync/IngestionFeed'
import { WearableStatusPanel } from '@/components/sync/WearableStatusPanel'
import { AppShell, DemoNotice } from '@/components/ui/AppShell'
import { getMethodInfo, getQueueStats } from '@/lib/api'
import { sampleFeed, sampleWearables } from '@/lib/sample-patient'

/**
 * Service state.
 *
 * The first thing this screen answers is which coefficient set is running,
 * because every clinical number elsewhere in the tool depends on it and a
 * validated deployment and a reference one look identical otherwise.
 */

export const dynamic = 'force-dynamic'

export default async function DashboardPage() {
  const [method, queue] = await Promise.all([getMethodInfo(), getQueueStats()])
  const live = method.data !== null

  const validated = method.data?.clinically_validated ?? false

  return (
    <AppShell>
      {!live && <DemoNotice className="mb-6" />}

      <section className="panel mb-6">
        <header className="panel-header">
          <span className="label">Engine mode</span>
          <span className="label" style={{ color: validated ? '#10B981' : '#F59E0B' }}>
            {validated ? 'Validated coefficients' : 'Reference coefficients'}
          </span>
        </header>

        <div className="grid md:grid-cols-3">
          <Stat
            label="Phase method"
            value={method.data?.method_version ?? 'reference-midsleep-1.0'}
          />
          <Stat
            label="Coefficient source"
            value={method.data?.coefficient_source ?? 'reference_fallback'}
            borderLeft
          />
          <Stat
            label="Clinically validated"
            value={validated ? 'yes' : 'no'}
            borderLeft
            accent={!validated}
          />
        </div>

        {!validated && (
          <p className="border-t border-drift/40 bg-drift/5 px-4 py-2 font-mono text-micro leading-relaxed text-drift">
            The private coefficient packages are not installed. Schedules generated in this
            state are structurally complete and clinically unvalidated, and every one of them
            is stamped as such.
          </p>
        )}
      </section>

      <div className="mb-6 grid gap-6 md:grid-cols-4">
        <Panel label="Queued reminders" value={String(queue.data?.queued ?? 0)} />
        <Panel label="In flight" value={String(queue.data?.claimed ?? 0)} />
        <Panel label="Due now" value={String(queue.data?.due_now ?? 0)} />
        <Panel
          label="Dispatcher"
          value={queue.data?.dispatcher_running ? 'running' : 'stopped'}
          accent={!queue.data?.dispatcher_running}
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
        <WearableStatusPanel links={sampleWearables} />
        <IngestionFeed events={sampleFeed} />
      </div>

      <p className="mt-6 text-sm text-ink-muted">
        <Link href="/patients" className="text-circadian hover:underline">
          Patients by phase drift
        </Link>{' '}
        is where a clinical session normally starts.
      </p>
    </AppShell>
  )
}

function Stat({
  label,
  value,
  borderLeft,
  accent,
}: {
  label: string
  value: string
  borderLeft?: boolean
  accent?: boolean
}) {
  return (
    <div className={`px-4 py-4 ${borderLeft ? 'border-l border-line' : ''}`}>
      <span className="label block">{label}</span>
      <span className={`value mt-1 block truncate text-sm ${accent ? 'text-drift' : ''}`}>
        {value}
      </span>
    </div>
  )
}

function Panel({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="panel px-4 py-4">
      <span className="label block">{label}</span>
      <span className={`value mt-2 block text-2xl ${accent ? 'text-drift' : ''}`}>{value}</span>
    </div>
  )
}
