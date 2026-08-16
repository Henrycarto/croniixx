import Link from 'next/link'

/**
 * Clinician entry point.
 *
 * There is no password form here on purpose. Croniixx authenticates through
 * SMART on FHIR against the EHR that already holds the patient record, so the
 * credential a clinician uses is the one their institution issued. A local
 * account would be a second identity to manage and a second thing to breach.
 */

export const metadata = {
  title: 'Sign in · Croniixx',
}

export default function LoginPage({
  searchParams,
}: {
  searchParams: { iss?: string; error?: string }
}) {
  const iss = searchParams.iss ?? ''

  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-lg">
        <p className="font-mono text-sm tracking-label text-ink">CRONIIXX</p>

        <h1 className="mt-8 text-xl leading-snug text-ink">
          Croniixx signs in through your electronic health record.
        </h1>

        <p className="mt-4 text-sm leading-relaxed text-ink-muted">
          Launching from inside the chart brings the patient context with it, so the circadian
          view opens on the patient you were already looking at. A standalone launch needs the
          FHIR base URL of your institution.
        </p>

        <form action="/api/fhir/launch" method="GET" className="mt-8">
          <label htmlFor="iss" className="label block">
            FHIR base URL
          </label>
          <input
            id="iss"
            name="iss"
            type="url"
            required
            defaultValue={iss}
            placeholder="https://fhir.example-hospital.org/r4"
            className="field mt-2"
          />

          <button type="submit" className="action-primary mt-4 w-full">
            Continue to your identity provider
          </button>
        </form>

        {searchParams.error && (
          <p className="mt-6 border border-contra/40 bg-contra/5 px-4 py-2 font-mono text-micro text-contra">
            {searchParams.error}
          </p>
        )}

        <div className="mt-10 border-t border-line pt-6">
          <p className="text-sm text-ink-muted">
            Reviewing the tool rather than using it clinically?{' '}
            <Link href="/patients/demo-patient/circadian" className="text-circadian hover:underline">
              Open the worked example
            </Link>
            .
          </p>
        </div>
      </div>
    </div>
  )
}
