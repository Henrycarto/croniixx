import { NextResponse } from 'next/server'

import { completeSmartLaunch } from '@/lib/fhir'

export const dynamic = 'force-dynamic'

export async function GET(request: Request) {
  const url = new URL(request.url)
  const code = url.searchParams.get('code')
  const state = url.searchParams.get('state')
  const error = url.searchParams.get('error')

  if (error) {
    return NextResponse.json(
      {
        data: null,
        error: {
          code: 'authorization_denied',
          message: url.searchParams.get('error_description') ?? error,
        },
        meta: { service: 'web', generated_at: new Date().toISOString() },
      },
      { status: 400 },
    )
  }

  if (!code) {
    return NextResponse.json(
      {
        data: null,
        error: { code: 'missing_code', message: 'No authorization code on the callback' },
        meta: { service: 'web', generated_at: new Date().toISOString() },
      },
      { status: 400 },
    )
  }

  try {
    const token = await completeSmartLaunch(code, state)
    // SMART returns the launch context alongside the token, so the patient the
    // clinician was looking at in the chart is known without asking again.
    const destination = token.patient ? `/patients/${token.patient}/circadian` : '/patients'
    return NextResponse.redirect(new URL(destination, url.origin))
  } catch (err) {
    return NextResponse.json(
      {
        data: null,
        error: {
          code: 'token_exchange_failed',
          message: err instanceof Error ? err.message : 'Token exchange failed',
        },
        meta: { service: 'web', generated_at: new Date().toISOString() },
      },
      { status: 400 },
    )
  }
}
