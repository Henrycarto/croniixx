import type { CoefficientSource, PhaseEstimate } from './circadian'

export type PhaseAnchor =
  | 'dlmo'
  | 'midsleep'
  | 'sleep_onset'
  | 'wake'
  | 'activity_acrophase'
  | 'temperature_nadir'
  | 'clock_time'

export type WindowStatus = 'optimal' | 'acceptable' | 'suboptimal' | 'contraindicated'

export type DoseStatus = 'pending' | 'taken' | 'missed' | 'skipped' | 'rescheduled'

export type DrugClass =
  | 'corticosteroid'
  | 'antihypertensive'
  | 'statin'
  | 'chemotherapy_antimetabolite'
  | 'chemotherapy_platinum'
  | 'chemotherapy_topoisomerase'
  | 'immunosuppressant'
  | 'thyroid_replacement'
  | 'proton_pump_inhibitor'
  | 'anticoagulant'
  | 'bronchodilator'
  | 'nsaid'
  | 'chronobiotic'
  | 'unclassified'

export interface ResolvedWindow {
  start: string
  end: string
  target: string
  status: WindowStatus
  anchor: PhaseAnchor
  anchor_offset_min: number
  rationale: string
  duration_min: number
}

export interface ScheduleEntry {
  entry_id: string
  medication_id: string
  display_name: string
  drug_class: DrugClass
  rxnorm_code: string | null
  dose_amount: number
  dose_unit: string
  dose_index: number
  window: ResolvedWindow
  alternate_windows: ResolvedWindow[]
  avoid_windows: ResolvedWindow[]
  status: DoseStatus
  confidence: number
  conventional_time: string | null
  /** Signed minutes between the biological target and the printed schedule. */
  drift_from_conventional_min: number | null
}

export interface ScheduleMeta {
  profile_completeness: number
  phase_confidence: number
  coefficient_source: CoefficientSource
  method_version: string
  provisional: boolean
  warnings: string[]
}

export interface AdaptiveSchedule {
  schedule_id: string
  patient_id: string
  generated_at: string
  valid_from: string
  valid_until: string
  schedule_version: number
  supersedes: string | null
  timezone: string
  phase: PhaseEstimate
  entries: ScheduleEntry[]
  meta: ScheduleMeta
  entry_count: number
  next_dose_at: string | null
}

export interface Medication {
  id: string
  patient_id: string
  display_name: string
  drug_class: DrugClass
  dose_amount: number
  dose_unit: string
  doses_per_day: number
  rxnorm_code?: string | null
  active: boolean
  fixed_clock_time?: string | null
}

export const DRUG_CLASS_LABELS: Record<DrugClass, string> = {
  corticosteroid: 'Corticosteroid',
  antihypertensive: 'Antihypertensive',
  statin: 'Statin',
  chemotherapy_antimetabolite: 'Antimetabolite',
  chemotherapy_platinum: 'Platinum agent',
  chemotherapy_topoisomerase: 'Topoisomerase inhibitor',
  immunosuppressant: 'Immunosuppressant',
  thyroid_replacement: 'Thyroid replacement',
  proton_pump_inhibitor: 'Proton pump inhibitor',
  anticoagulant: 'Anticoagulant',
  bronchodilator: 'Bronchodilator',
  nsaid: 'NSAID',
  chronobiotic: 'Chronobiotic',
  unclassified: 'Unclassified',
}

export const ANCHOR_LABELS: Record<PhaseAnchor, string> = {
  dlmo: 'DLMO',
  midsleep: 'Midsleep',
  sleep_onset: 'Sleep onset',
  wake: 'Wake',
  activity_acrophase: 'Activity acrophase',
  temperature_nadir: 'Temperature nadir',
  clock_time: 'Clock time',
}
