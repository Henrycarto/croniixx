import { describe, expect, it } from 'vitest'

import { toDialWindows, toSleepSegments } from './dial-adapters'
import { sampleSchedule } from './sample-patient'

describe('toDialWindows', () => {
  it('produces one arc per dose plus its avoidance periods', () => {
    const windows = toDialWindows(sampleSchedule, 'UTC')
    const avoidCount = sampleSchedule.entries.reduce(
      (total, entry) => total + entry.avoid_windows.length,
      0,
    )
    expect(windows).toHaveLength(sampleSchedule.entries.length + avoidCount)
  })

  it('marks every avoidance arc as contraindicated', () => {
    const windows = toDialWindows(sampleSchedule, 'UTC')
    const avoid = windows.filter((window) => window.id.includes('-avoid-'))
    expect(avoid.length).toBeGreaterThan(0)
    expect(avoid.every((window) => window.status === 'contraindicated')).toBe(true)
  })

  it('keeps every target inside its own window', () => {
    for (const window of toDialWindows(sampleSchedule, 'UTC')) {
      const span = (window.endHour - window.startHour + 24) % 24 || 24
      const offset = (window.targetHour - window.startHour + 24) % 24
      expect(offset).toBeLessThanOrEqual(span)
    }
  })

  it('shifts every arc by the same amount when the zone changes', () => {
    const utc = toDialWindows(sampleSchedule, 'UTC')
    const vienna = toDialWindows(sampleSchedule, 'Europe/Vienna')

    const shifts = utc.map((window, index) =>
      Number((((vienna[index].startHour - window.startHour + 24) % 24)).toFixed(4)),
    )
    expect(new Set(shifts).size).toBe(1)
  })
})

describe('toSleepSegments', () => {
  it('covers the full twenty four hours without a gap', () => {
    const segments = toSleepSegments(sampleSchedule)
    const total = segments.reduce(
      (sum, segment) => sum + (((segment.endHour - segment.startHour + 24) % 24) || 24),
      0,
    )
    expect(total).toBeCloseTo(24, 3)
  })

  it('is contiguous, so the ring has no holes in it', () => {
    const segments = toSleepSegments(sampleSchedule)
    for (let index = 1; index < segments.length; index += 1) {
      expect(segments[index].startHour).toBeCloseTo(segments[index - 1].endHour, 6)
    }
  })

  it('moves the whole night when the phase offset moves', () => {
    const baseline = toSleepSegments(sampleSchedule)
    const shifted = toSleepSegments({
      ...sampleSchedule,
      phase: { ...sampleSchedule.phase, phase_offset_min: sampleSchedule.phase.phase_offset_min + 120 },
    })
    const delta = (shifted[0].startHour - baseline[0].startHour + 24) % 24
    expect(delta).toBeCloseTo(2, 3)
  })

  it('includes exactly one wake segment', () => {
    const segments = toSleepSegments(sampleSchedule)
    expect(segments.filter((segment) => segment.stage === 'awake')).toHaveLength(1)
  })
})
