import type {
  AdaptiveSchedule,
  DialDrugWindow,
  DialSleepSegment,
} from '@croniixx/shared-types'

import { isoToLocalHour, normalizeHour } from './dial-geometry'

/**
 * Converts service payloads into dial input.
 *
 * The dial takes local decimal hours and nothing else. Doing the timezone
 * conversion here, once, keeps that rule intact: a component that also parsed
 * timestamps could disagree with the schedule panel about what hour a window
 * opens, and two different answers on one screen is worse than either.
 */

/** Every dose window from a schedule, plus its contraindicated periods. */
export function toDialWindows(schedule: AdaptiveSchedule, timeZone: string): DialDrugWindow[] {
  const windows: DialDrugWindow[] = []

  for (const entry of schedule.entries) {
    windows.push({
      id: entry.entry_id,
      label: `${entry.display_name} ${entry.dose_amount}${entry.dose_unit}`,
      startHour: isoToLocalHour(entry.window.start, timeZone),
      endHour: isoToLocalHour(entry.window.end, timeZone),
      targetHour: isoToLocalHour(entry.window.target, timeZone),
      status: entry.window.status,
    })

    entry.avoid_windows.forEach((avoid, index) => {
      windows.push({
        id: `${entry.entry_id}-avoid-${index}`,
        label: `${entry.display_name} avoid`,
        startHour: isoToLocalHour(avoid.start, timeZone),
        endHour: isoToLocalHour(avoid.end, timeZone),
        targetHour: isoToLocalHour(avoid.target, timeZone),
        status: 'contraindicated',
      })
    })
  }

  return windows
}

/**
 * A representative night derived from the phase estimate.
 *
 * Used when a real hypnogram is not on hand. It is a schematic of a normal
 * sleep cycle shifted to the patient's own phase, not a recording, so the
 * caller decides whether showing it is honest in context.
 */
export function toSleepSegments(schedule: AdaptiveSchedule): DialSleepSegment[] {
  const offsetHours = schedule.phase.phase_offset_min / 60
  const onset = normalizeHour(23 + offsetHours)
  const durationHours = 7.75

  // Cycle structure across a night: deep sleep front loaded, REM episodes
  // lengthening toward morning. Fractions of the night rather than fixed
  // durations so a short sleeper keeps the same architecture.
  const pattern: Array<[DialSleepSegment['stage'], number]> = [
    ['light', 0.08],
    ['deep', 0.14],
    ['light', 0.07],
    ['rem', 0.07],
    ['light', 0.09],
    ['deep', 0.1],
    ['light', 0.09],
    ['rem', 0.1],
    ['light', 0.11],
    ['rem', 0.15],
  ]

  const segments: DialSleepSegment[] = []
  let cursor = onset

  for (const [stage, fraction] of pattern) {
    const end = cursor + durationHours * fraction
    segments.push({ startHour: normalizeHour(cursor), endHour: normalizeHour(end), stage })
    cursor = end
  }

  segments.push({ startHour: normalizeHour(cursor), endHour: normalizeHour(onset), stage: 'awake' })
  return segments
}
