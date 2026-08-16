import { NextResponse } from 'next/server'

import { beginSmartLaunch } from '@/lib/fhir'

// The EHR calls this with iss and launch when a clinician opens Croniixx from
// inside the chart. A standalone launch arrives with iss only.
export const dynamic = 'force-dynamic'

export async function GET(request: Request) {
  const url = new URL(request.url)
  const iss = url.searchParams.get('iss')
  const launch = url.searchParams.get('launch') ?? undefined

  if (!iss) {
    return NextResponse.json(
      {
        data: null,
        error: { code: 'missing_iss', message: 'A SMART launch requires an iss parameter' },
        meta: { service: 'web', generated_at: new Date().toISOString() },
      },
      { status: 400 },
    )
  }

  try {
    const { authorizeUrl } = await beginSmartLaunch(iss, launch)
    return NextResponse.redirect(authorizeUrl)
  } catch (error) {
    return NextResponse.json(
      {
        data: null,
        error: {
          code: 'launch_failed',
          message: error instanceof Error ? error.message : 'Launch failed',
        },
        meta: { service: 'web', generated_at: new Date().toISOString() },
      },
      { status: 502 },
    )
  }
}
