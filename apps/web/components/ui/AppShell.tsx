import Link from 'next/link'

/**
 * The frame every clinical screen sits inside.
 *
 * A single hairline rule separates navigation from content. There is no
 * sidebar because the tool has one subject, the patient, and a persistent
 * navigation column would spend a fifth of the screen restating that.
 */

export interface AppShellProps {
  children: React.ReactNode
  patientLabel?: string
  actions?: React.ReactNode
}

const NAV = [
  { href: '/dashboard', label: 'Dashboard' },
  { href: '/patients', label: 'Patients' },
]

export function AppShell({ children, patientLabel, actions }: AppShellProps) {
  return (
    <div className="min-h-screen bg-base">
      <header className="border-b border-line">
        <div className="mx-auto flex max-w-[1600px] items-center justify-between px-6 py-3">
          <div className="flex items-baseline gap-8">
            <Link href="/" className="font-mono text-sm tracking-label text-ink">
              CRONIIXX
            </Link>
            <nav className="flex gap-6">
              {NAV.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="font-mono text-micro uppercase tracking-label text-ink-muted transition-colors hover:text-circadian"
                >
                  {item.label}
                </Link>
              ))}
            </nav>
          </div>

          <div className="flex items-center gap-4">
            {patientLabel && (
              <span className="border border-line px-3 py-1 font-mono text-micro text-ink-muted">
                {patientLabel}
              </span>
            )}
            {actions}
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1600px] px-6 py-6">{children}</main>
    </div>
  )
}

export function DemoNotice({ className }: { className?: string }) {
  return (
    <p
      className={`border border-drift/40 bg-drift/5 px-4 py-2 font-mono text-micro text-drift ${className ?? ''}`}
    >
      Demonstration data. Live services are not reachable from this environment, and the
      phase model is running on reference coefficients rather than validated ones.
    </p>
  )
}
