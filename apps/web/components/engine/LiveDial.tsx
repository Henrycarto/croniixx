'use client'

import { useEffect, useState } from 'react'
import type { DialDrugWindow, DialMarker, DialSleepSegment } from '@croniixx/shared-types'

import { CircadianClockDial, CircadianDialLegend } from './CircadianClockDial'
import { isoToLocalHour } from '@/lib/dial-geometry'

/**
 * The dial wired to the wall clock.
 *
 * The clock needle follows real time so the gap between it and the violet
 * biological needle is live rather than a still frame. The first render uses a
 * fixed hour and the real time is set after mount, because a server rendered
 * clock and a client rendered one disagree by definition and React would
 * report that as a hydration error.
 */

export interface LiveDialProps {
  phaseOffsetMin: number
  sleepSegments: DialSleepSegment[]
  drugWindows: DialDrugWindow[]
  markers?: DialMarker[]
  timeZone: string
  size?: number
  showLegend?: boolean
  className?: string
}

export function LiveDial({
  phaseOffsetMin,
  sleepSegments,
  drugWindows,
  markers,
  timeZone,
  size = 520,
  showLegend = true,
  className,
}: LiveDialProps) {
  const [currentHour, setCurrentHour] = useState<number | null>(null)

  useEffect(() => {
    const update = () => setCurrentHour(isoToLocalHour(new Date().toISOString(), timeZone))
    update()
    // Half a minute is finer than the dial can show and coarse enough that the
    // page costs nothing to leave open on a ward screen.
    const timer = window.setInterval(update, 30_000)
    return () => window.clearInterval(timer)
  }, [timeZone])

  return (
    <div className={className}>
      <CircadianClockDial
        currentHour={currentHour ?? 9}
        phaseOffsetMin={phaseOffsetMin}
        sleepSegments={sleepSegments}
        drugWindows={drugWindows}
        markers={markers}
        size={size}
      />
      {showLegend && <CircadianDialLegend className="mt-4" />}
    </div>
  )
}
