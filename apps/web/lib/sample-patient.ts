import type {
  AdaptiveSchedule,
  DialDrugWindow,
  DialMarker,
  DialSleepSegment,
  IngestionEvent,
  PhaseEstimate,
  WearableLinkStatus,
} from '@croniixx/shared-types'

/**
 * A worked example used on the landing page and wherever a service is not
 * reachable.
 *
 * The patient is a delayed phase case on a four agent regimen. The numbers are
 * internally consistent: the sleep architecture, the phase offset, and the
 * dose windows all describe the same person, so the dial reads correctly
 * rather than showing decorative arcs.
 *
 * This is demonstration data and every surface that renders it says so.
 */

export const SAMPLE_PATIENT_ID = 'demo-patient'

// The sample sleep hours and the sample schedule timestamps are both authored
// in UTC. Presenting the demo in any other zone would slide the dose arcs
// against the sleep ring by the offset and make the worked example wrong.
export const SAMPLE_TIMEZONE = 'UTC'

/** Sleep onset 01:20, wake 09:05, midsleep 05:12. Delayed by about 72 minutes. */
export const SAMPLE_PHASE_OFFSET_MIN = 72

export const sampleSleepSegments: DialSleepSegment[] = [
  { startHour: 1.33, endHour: 1.75, stage: 'awake' },
  { startHour: 1.75, endHour: 2.6, stage: 'light' },
  { startHour: 2.6, endHour: 3.55, stage: 'deep' },
  { startHour: 3.55, endHour: 4.1, stage: 'light' },
  { startHour: 4.1, endHour: 4.75, stage: 'rem' },
  { startHour: 4.75, endHour: 5.5, stage: 'light' },
  { startHour: 5.5, endHour: 6.15, stage: 'deep' },
  { startHour: 6.15, endHour: 6.3, stage: 'awake' },
  { startHour: 6.3, endHour: 7.0, stage: 'light' },
  { startHour: 7.0, endHour: 7.85, stage: 'rem' },
  { startHour: 7.85, endHour: 8.35, stage: 'light' },
  { startHour: 8.35, endHour: 9.08, stage: 'rem' },
  { startHour: 9.08, endHour: 1.33, stage: 'awake' },
]

export const sampleDrugWindows: DialDrugWindow[] = [
  {
    id: 'w-prednisolone',
    label: 'Prednisolone 20 mg',
    startHour: 9.08,
    endHour: 11.08,
    targetHour: 9.58,
    status: 'optimal',
  },
  {
    id: 'w-capecitabine',
    label: 'Capecitabine 1500 mg',
    startHour: 3.2,
    endHour: 7.2,
    targetHour: 5.2,
    status: 'optimal',
  },
  {
    id: 'w-capecitabine-avoid',
    label: 'Capecitabine avoid',
    startHour: 11.08,
    endHour: 19.08,
    targetHour: 15.08,
    status: 'contraindicated',
  },
  {
    id: 'w-simvastatin',
    label: 'Simvastatin 40 mg',
    startHour: 23.33,
    endHour: 1.83,
    targetHour: 0.58,
    status: 'optimal',
  },
  {
    id: 'w-ramipril',
    label: 'Ramipril 5 mg',
    startHour: 21.2,
    endHour: 0.2,
    targetHour: 22.7,
    status: 'acceptable',
  },
]

export const sampleMarkers: DialMarker[] = [
  { hour: 22.2, label: 'DLMO estimate', kind: 'dlmo' },
  { hour: 5.2, label: 'Midsleep', kind: 'midsleep' },
]

export const samplePhase: PhaseEstimate = {
  patient_id: SAMPLE_PATIENT_ID,
  computed_at: '2026-04-10T05:12:00Z',
  phase_offset_min: SAMPLE_PHASE_OFFSET_MIN,
  dlmo_estimate: '2026-04-09T20:12:00Z',
  amplitude: 0.83,
  stability: 0.61,
  confidence: 0.42,
  method_version: 'reference-midsleep-1.0',
  coefficient_source: 'reference_fallback',
  inputs_used: ['sleep.mean_midsleep_hour', 'actigraphy.interdaily_stability'],
  warnings: [
    'Reference estimator in use. Phase offset derives from sleep midpoint alone and is not validated for clinical dosing decisions.',
  ],
  direction: 'delayed',
  offset_display: '+01:12',
}

export const sampleWearables: WearableLinkStatus[] = [
  {
    terra_user_id: 'terra-3f9a1c2e-oura',
    provider: 'OURA',
    connected_at: '2026-03-26T08:11:00Z',
    last_payload_at: '2026-04-10T07:42:00Z',
    active: true,
    scopes: ['sleep', 'daily', 'activity'],
    staleness_s: 5400,
  },
  {
    terra_user_id: 'terra-8b21d7f4-apple',
    provider: 'APPLE',
    connected_at: '2026-03-28T14:02:00Z',
    last_payload_at: '2026-04-10T06:15:00Z',
    active: true,
    scopes: ['sleep', 'daily'],
    staleness_s: 10020,
  },
  {
    terra_user_id: 'terra-51cc90ab-whoop',
    provider: 'WHOOP',
    connected_at: '2026-02-14T09:30:00Z',
    last_payload_at: '2026-04-06T22:10:00Z',
    active: true,
    scopes: ['sleep', 'daily'],
    staleness_s: 300600,
  },
]

export const sampleFeed: IngestionEvent[] = [
  {
    at: '2026-04-10T07:42:00Z',
    provider: 'OURA',
    payload_type: 'sleep',
    samples: 214,
    sleep_sessions: 1,
    warnings: [],
  },
  {
    at: '2026-04-10T06:15:00Z',
    provider: 'APPLE',
    payload_type: 'daily',
    samples: 96,
    sleep_sessions: 0,
    warnings: ['APPLE: rMSSD derived from SDNN at ratio 0.8; phase amplitude is approximate'],
  },
  {
    at: '2026-04-09T23:04:00Z',
    provider: 'OURA',
    payload_type: 'activity',
    samples: 288,
    sleep_sessions: 0,
    warnings: [],
  },
  {
    at: '2026-04-06T22:10:00Z',
    provider: 'WHOOP',
    payload_type: 'sleep',
    samples: 88,
    sleep_sessions: 1,
    warnings: [
      'WHOOP: rMSSD sampled in slow wave sleep, scaled toward a whole night equivalent',
    ],
  },
]

function iso(hour: number, minute: number, dayOffset = 0): string {
  const base = new Date(Date.UTC(2026, 3, 10 + dayOffset, hour, minute, 0))
  return base.toISOString()
}

export const sampleSchedule: AdaptiveSchedule = {
  schedule_id: 'demo-schedule-1',
  patient_id: SAMPLE_PATIENT_ID,
  generated_at: iso(5, 20),
  valid_from: iso(5, 20),
  valid_until: iso(7, 20, 1),
  schedule_version: 4,
  supersedes: 'demo-schedule-0',
  timezone: 'UTC',
  phase: samplePhase,
  meta: {
    profile_completeness: 0.78,
    phase_confidence: 0.42,
    coefficient_source: 'reference_fallback',
    method_version: 'reference-midsleep-1.0+reference-geometry-1.0',
    provisional: true,
    warnings: [
      'Timing catalog is reference geometry. Windows are structurally correct and clinically unvalidated.',
    ],
  },
  entry_count: 4,
  next_dose_at: iso(9, 35),
  entries: [
    {
      entry_id: 'e-capecitabine',
      medication_id: 'm-capecitabine',
      display_name: 'Capecitabine',
      drug_class: 'chemotherapy_antimetabolite',
      rxnorm_code: '194000',
      dose_amount: 1500,
      dose_unit: 'mg',
      dose_index: 0,
      status: 'pending',
      confidence: 0.4,
      conventional_time: iso(8, 0),
      drift_from_conventional_min: -168,
      window: {
        start: iso(3, 12),
        end: iso(7, 12),
        target: iso(5, 12),
        status: 'optimal',
        anchor: 'midsleep',
        anchor_offset_min: 0,
        rationale: 'Anchored to the trough in healthy tissue proliferation',
        duration_min: 240,
      },
      alternate_windows: [],
      avoid_windows: [
        {
          start: iso(11, 5),
          end: iso(19, 5),
          target: iso(15, 5),
          status: 'contraindicated',
          anchor: 'wake',
          anchor_offset_min: 120,
          rationale: 'Reference avoidance window',
          duration_min: 480,
        },
      ],
    },
    {
      entry_id: 'e-prednisolone',
      medication_id: 'm-prednisolone',
      display_name: 'Prednisolone',
      drug_class: 'corticosteroid',
      rxnorm_code: '8640',
      dose_amount: 20,
      dose_unit: 'mg',
      dose_index: 0,
      status: 'pending',
      confidence: 0.4,
      conventional_time: iso(8, 0),
      drift_from_conventional_min: 95,
      window: {
        start: iso(9, 5),
        end: iso(11, 5),
        target: iso(9, 35),
        status: 'optimal',
        anchor: 'wake',
        anchor_offset_min: 0,
        rationale: 'Anchored near the endogenous cortisol rise',
        duration_min: 120,
      },
      alternate_windows: [],
      avoid_windows: [],
    },
    {
      entry_id: 'e-ramipril',
      medication_id: 'm-ramipril',
      display_name: 'Ramipril',
      drug_class: 'antihypertensive',
      rxnorm_code: '35296',
      dose_amount: 5,
      dose_unit: 'mg',
      dose_index: 0,
      status: 'pending',
      confidence: 0.4,
      // The nearest 08:00 to a 22:42 target is the following morning, so the
      // shift reads as nine hours earlier rather than fifteen hours later.
      conventional_time: iso(8, 0, 1),
      drift_from_conventional_min: -558,
      window: {
        start: iso(21, 12),
        end: iso(0, 12, 1),
        target: iso(22, 42),
        status: 'acceptable',
        anchor: 'dlmo',
        anchor_offset_min: -60,
        rationale: 'Anchored to the evening decline in blood pressure',
        duration_min: 180,
      },
      alternate_windows: [],
      avoid_windows: [],
    },
    {
      entry_id: 'e-simvastatin',
      medication_id: 'm-simvastatin',
      display_name: 'Simvastatin',
      drug_class: 'statin',
      rxnorm_code: '36567',
      dose_amount: 40,
      dose_unit: 'mg',
      dose_index: 0,
      status: 'pending',
      confidence: 0.4,
      conventional_time: iso(8, 0, 1),
      drift_from_conventional_min: -445,
      window: {
        start: iso(23, 20),
        end: iso(1, 50, 1),
        target: iso(0, 35, 1),
        status: 'optimal',
        anchor: 'sleep_onset',
        anchor_offset_min: -120,
        rationale: 'Anchored to the nocturnal peak in cholesterol synthesis',
        duration_min: 150,
      },
      alternate_windows: [],
      avoid_windows: [],
    },
  ],
}
