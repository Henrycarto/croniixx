import type { Metadata } from 'next'
import { Inter, Space_Mono } from 'next/font/google'

import './globals.css'

const inter = Inter({
  subsets: ['latin'],
  variable: '--font-inter',
  display: 'swap',
})

// Space Mono carries every measurement in this interface: phase offsets,
// timing windows, HRV readings, timestamps. Its slight mechanical warmth suits
// biological data better than a purely geometric mono.
const spaceMono = Space_Mono({
  subsets: ['latin'],
  weight: ['400', '700'],
  variable: '--font-space-mono',
  display: 'swap',
})

export const metadata: Metadata = {
  title: 'Croniixx',
  description:
    'Circadian aware medication scheduling. Drug timing calculated from a patient measured biological clock rather than a printed schedule.',
  robots: { index: true, follow: true },
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${spaceMono.variable}`}>
      <body className="min-h-screen bg-base font-sans text-ink antialiased">{children}</body>
    </html>
  )
}
