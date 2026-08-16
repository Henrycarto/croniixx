import Link from 'next/link'

import { PhaseOffsetBadge } from '@/components/engine/PhaseOffsetBadge'
import { AppShell, DemoNotice } from '@/components/ui/AppShell'
import { getMethodInfo } from '@/lib/api'
import { formatOffset } from '@/lib/dial-geometry'
import { SAMPLE_PATIENT_ID, samplePhase } from '@/lib/sample-patient'

/**
 * Patient list.
 *
 * Sorted by drift rather than by name. A clinician opening this screen wants
 * the patients whose clock has moved away from the schedule they were written,
 * and alphabetical order buries exactly those.
 */

export const dynamic = 'force-dynamic'

interface PatientRow {
  id: string
  label: string
  regimen: string
  phaseOffsetMin: number
  driftMin: number
  completeness: number
  devices: number
}

const ROSTER: PatientRow[] = [
  {
    id: SAMPLE_PATIENT_ID,
    label: 'Demonstration patient',
    regimen: 'Capecitabine, prednisolone, ramipril, simvastatin',
    phaseOffsetMin: 72,
    driftMin: 84,
    completeness: 0.78,
    devices: 3,
  },
  {
    id: 'patient-b41f',
    label: 'Patient B41F',
    regimen: 'Oxaliplatin, dexamethasone',
    phaseOffsetMin: -38,
    driftMin: 22,
    completeness: 0.91,
    devices: 1,
  },
  {
    id: 'patient-c07a',
    label: 'Patient C07A',
    regimen: 'Tacrolimus, prednisolone, pantoprazole',
    phaseOffsetMin: 15,
    driftMin: 9,
    completeness: 0.64,
    devices: 2,
  },
]

export default async function PatientsPage() {
  const method = await getMethodInfo()
  const live = method.data !== null

  const rows = [...ROSTER].sort((a, b) => Math.abs(b.driftMin) - Math.abs(a.driftMin))

  return (
    <AppShell>
      {!live && <DemoNotice className="mb-6" />}

      <div className="grid gap-6 xl:grid-cols-[2fr_1fr]">
        <section className="panel">
          <header className="panel-header">
            <span className="label">Patients by phase drift</span>
            <span className="label">{rows.length} on service</span>
          </header>

          <div className="grid grid-cols-[1fr_5rem_5rem_5rem_4rem] gap-4 border-b border-line px-4 py-2">
            <span className="label">Patient</span>
            <span className="label text-right">Offset</span>
            <span className="label text-right">Drift</span>
            <span className="label text-right">Data</span>
            <span className="label text-right">Dev</span>
          </div>

          <ul>
            {rows.map((row) => (
              <li key={row.id}>
                <Link
                  href={`/patients/${row.id}/circadian`}
                  className="grid grid-cols-[1fr_5rem_5rem_5rem_4rem] items-center gap-4 border-b border-line px-4 py-3 transition-colors hover:bg-surface-raised"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-sm text-ink">{row.label}</span>
                    <span className="block truncate font-mono text-micro text-ink-faint">
                      {row.regimen}
                    </span>
                  </span>
                  <span className="value text-right text-sm">
                    {formatOffset(row.phaseOffsetMin)}
                  </span>
                  <span
                    className="value text-right text-sm"
                    style={{ color: Math.abs(row.driftMin) >= 60 ? '#F59E0B' : '#8A96B2' }}
                  >
                    {formatOffset(row.driftMin)}
                  </span>
                  <span className="value text-right text-sm">
                    {row.completeness.toFixed(2)}
                  </span>
                  <span className="value text-right text-sm">{row.devices}</span>
                </Link>
              </li>
            ))}
          </ul>

          <p className="px-4 py-3 text-sm leading-relaxed text-ink-muted">
            Drift is the movement of a patient&apos;s phase estimate since their current
            schedule was generated. Past an hour, most timing windows have moved further than
            their own width and the regimen needs regenerating.
          </p>
        </section>

        <PhaseOffsetBadge phase={samplePhase} />
      </div>
    </AppShell>
  )
}
