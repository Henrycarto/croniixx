import Link from 'next/link'

import { LiveDial } from '@/components/engine/LiveDial'
import {
  SAMPLE_PHASE_OFFSET_MIN,
  SAMPLE_TIMEZONE,
  sampleDrugWindows,
  sampleMarkers,
  sampleSleepSegments,
} from '@/lib/sample-patient'

// The statement comes first and nothing precedes it. A clinician deciding
// whether to read further needs the claim, not a logo and a navigation bar.
const STATEMENT =
  "Drug efficacy varies by up to 40% depending on when in the circadian cycle it is administered. Croniixx calculates the right time from your patient's actual biology."

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-base">
      <section className="border-b border-line">
        <div className="mx-auto max-w-[1200px] px-6 pb-10 pt-16">
          <h1 className="max-w-4xl text-2xl leading-snug text-ink md:text-[1.75rem]">
            {STATEMENT}
          </h1>
        </div>
      </section>

      <section className="border-b border-line">
        <div className="mx-auto grid max-w-[1200px] gap-10 px-6 py-10 lg:grid-cols-[auto_1fr] lg:items-start">
          <LiveDial
            phaseOffsetMin={SAMPLE_PHASE_OFFSET_MIN}
            sleepSegments={sampleSleepSegments}
            drugWindows={sampleDrugWindows}
            markers={sampleMarkers}
            timeZone={SAMPLE_TIMEZONE}
            size={520}
          />

          <div className="max-w-xl">
            <p className="label">Sample patient · delayed phase · four agent regimen</p>

            <p className="mt-4 text-sm leading-relaxed text-ink-muted">
              The outer ring is one night of this patient&apos;s own sleep architecture, staged
              from a wearable. The violet needle is where their internal clock currently sits.
              The dashed needle is the wall clock. The angle between the two is the phase
              offset, and it is the reason a printed schedule and this schedule disagree.
            </p>

            <p className="mt-4 text-sm leading-relaxed text-ink-muted">
              The inner arcs are dose windows for the regimen, placed against biological
              anchors rather than clock times. Green is the calculated window. Red is a period
              the agent should not be given in. The white tick inside each arc is the target
              moment.
            </p>

            <dl className="mt-8 divide-y divide-line border-y border-line">
              <Fact term="Phase offset" definition="+01:12 delayed against the population reference" />
              <Fact term="Midsleep" definition="05:12 local, from fourteen nights of wearable data" />
              <Fact term="DLMO estimate" definition="22:12, projected from sleep timing" />
              <Fact
                term="Largest dose shift"
                definition="Capecitabine moves 2 h 48 m earlier than the printed 08:00"
              />
            </dl>

            <div className="mt-8 flex flex-wrap gap-3">
              <Link href="/patients/demo-patient/circadian" className="action-primary">
                Open the worked example
              </Link>
              <Link href="/dashboard" className="action">
                Dashboard
              </Link>
            </div>
          </div>
        </div>
      </section>

      <section className="border-b border-line">
        <div className="mx-auto max-w-[1200px] px-6 py-10">
          <h2 className="label">How the timing is derived</h2>

          <ol className="mt-6 divide-y divide-line border-y border-line">
            <Step
              index="01"
              title="Ingest"
              body="Terra streams sleep staging, heart rate variability, and movement from Oura, Apple Watch, Garmin, and Whoop. The differences between those four are reconciled before anything is stored, because Apple reports SDNN where the others report rMSSD and Whoop samples inside slow wave sleep rather than across the night."
            />
            <Step
              index="02"
              title="Profile"
              body="Fourteen days of normalized data produce a circadian profile: sleep midpoint and its variability, cosinor fits for heart rate variability and skin temperature, and the standard nonparametric actigraphy measures. Nothing at this stage claims a phase position; it describes what was observed."
            />
            <Step
              index="03"
              title="Position"
              body="The profile is scored into a signed phase offset with a confidence attached. A positive offset means a delayed clock. This is the step that carries the proprietary coefficients, and a public checkout of this repository runs a transparent reference estimator instead."
            />
            <Step
              index="04"
              title="Schedule"
              body="Each agent in the regimen has timing windows expressed relative to biological anchors rather than clock times. Resolving those anchors against the patient's own phase produces dated windows, which is the adaptive schedule the app and the reminder queue consume."
            />
          </ol>
        </div>
      </section>

      <footer className="mx-auto max-w-[1200px] px-6 py-8">
        <div className="flex flex-wrap items-baseline justify-between gap-4">
          <span className="font-mono text-micro text-ink-faint">
            Croniixx · circadian aware medication scheduling
          </span>
          <a
            href="https://github.com/Henrycarto/croniixx"
            className="font-mono text-micro text-ink-muted transition-colors hover:text-circadian"
          >
            github.com/Henrycarto/croniixx
          </a>
        </div>
      </footer>
    </div>
  )
}

function Fact({ term, definition }: { term: string; definition: string }) {
  return (
    <div className="flex items-baseline justify-between gap-6 py-2.5">
      <dt className="label shrink-0">{term}</dt>
      <dd className="value text-right text-sm">{definition}</dd>
    </div>
  )
}

function Step({ index, title, body }: { index: string; title: string; body: string }) {
  return (
    <li className="grid gap-4 py-5 md:grid-cols-[3rem_8rem_1fr]">
      <span className="value text-sm text-circadian">{index}</span>
      <span className="text-sm text-ink">{title}</span>
      <p className="max-w-3xl text-sm leading-relaxed text-ink-muted">{body}</p>
    </li>
  )
}
