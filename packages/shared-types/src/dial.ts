import type { SleepStage } from './wearables'
import type { WindowStatus } from './schedule'

/**
 * Input types for the CircadianClockDial.
 *
 * Hours are local decimal hours in the patient's own timezone, in the range
 * [0, 24). The dial does no timezone conversion: it draws what it is given.
 * Conversion happens once, where the patient's zone is known, so the component
 * cannot disagree with the rest of the interface about what time it is.
 */

export interface DialSleepSegment {
  startHour: number
  endHour: number
  stage: SleepStage
}

export interface DialDrugWindow {
  id: string
  label: string
  startHour: number
  endHour: number
  targetHour: number
  status: WindowStatus
}

export interface DialMarker {
  hour: number
  label: string
  kind: 'dlmo' | 'midsleep' | 'reference'
}

export interface CircadianDialData {
  currentHour: number
  phaseOffsetMin: number
  sleepSegments: DialSleepSegment[]
  drugWindows: DialDrugWindow[]
  markers: DialMarker[]
}
