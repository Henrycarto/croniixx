export type CoefficientSource = 'private_validated' | 'reference_fallback'

export type PhaseDirection = 'delayed' | 'advanced' | 'aligned'

export interface PhaseEstimate {
  patient_id: string
  computed_at: string
  /** Signed minutes from the population reference. Positive is delayed. */
  phase_offset_min: number
  dlmo_estimate: string | null
  amplitude: number | null
  stability: number | null
  confidence: number
  method_version: string
  coefficient_source: CoefficientSource
  inputs_used: string[]
  warnings: string[]
  direction: PhaseDirection
  /** Preformatted signed offset, for example "+01:45". */
  offset_display: string
}

export interface PhaseDrift {
  patient_id: string
  baseline_offset_min: number
  current_offset_min: number
  drift_min: number
  window_days: number
  alert_threshold_min: number
  alerting: boolean
}

export interface PhaseHistoryPoint {
  at: string
  phase_offset_min: number
  confidence: number
  method_version: string
}

export interface CosinorFit {
  mesor: number
  amplitude: number
  acrophase_hour: number
  r_squared: number
  n_points: number
}

export interface SleepTimingSummary {
  nights_observed: number
  mean_onset_hour: number | null
  mean_offset_hour: number | null
  mean_midsleep_hour: number | null
  midsleep_variability_h: number | null
  mean_duration_h: number | null
  mean_efficiency: number | null
  deep_fraction: number | null
  rem_fraction: number | null
  mean_first_rem_latency_min: number | null
}

export interface ActigraphySummary {
  interdaily_stability: number | null
  intradaily_variability: number | null
  l5_onset_hour: number | null
  l5_mean: number | null
  m10_onset_hour: number | null
  m10_mean: number | null
  relative_amplitude: number | null
}

export interface MetricCoverage {
  metric: string
  sample_count: number
  days_covered: number
  mean_confidence: number
}

export interface CircadianProfile {
  patient_id: string
  window_start: string
  window_end: string
  providers: string[]
  sleep: SleepTimingSummary
  actigraphy: ActigraphySummary
  hrv_cosinor: CosinorFit | null
  temperature_cosinor: CosinorFit | null
  activity_cosinor: CosinorFit | null
  resting_hr_cosinor: CosinorFit | null
  coverage: MetricCoverage[]
  data_completeness: number
  warnings: string[]
}
