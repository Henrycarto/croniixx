/**
 * The FHIR R4 resources Croniixx reads, and the mapping into our own types.
 *
 * Only the fields the scheduler needs are modelled. A full FHIR type surface
 * would be thousands of lines of optionality that nothing here reads, and the
 * useful work is the mapping below rather than the type definitions.
 */

import type { SmartAuthError } from './smart'

export interface FhirCoding {
  system?: string
  code?: string
  display?: string
}

export interface FhirCodeableConcept {
  coding?: FhirCoding[]
  text?: string
}

export interface FhirPatient {
  resourceType: 'Patient'
  id: string
  name?: Array<{ family?: string; given?: string[]; text?: string }>
  birthDate?: string
  gender?: string
}

export interface FhirDosage {
  text?: string
  timing?: {
    repeat?: {
      frequency?: number
      period?: number
      periodUnit?: string
      timeOfDay?: string[]
      when?: string[]
    }
  }
  doseAndRate?: Array<{
    doseQuantity?: { value?: number; unit?: string; code?: string; system?: string }
  }>
}

export interface FhirMedicationRequest {
  resourceType: 'MedicationRequest'
  id: string
  status?: string
  intent?: string
  medicationCodeableConcept?: FhirCodeableConcept
  medicationReference?: { reference?: string; display?: string }
  subject?: { reference?: string }
  dosageInstruction?: FhirDosage[]
  authoredOn?: string
}

export interface FhirBundle<T> {
  resourceType: 'Bundle'
  type?: string
  total?: number
  entry?: Array<{ resource?: T }>
  link?: Array<{ relation: string; url: string }>
}

const RXNORM_SYSTEM = 'http://www.nlm.nih.gov/research/umls/rxnorm'

export interface ImportedMedication {
  fhir_id: string
  display_name: string
  rxnorm_code: string | null
  dose_amount: number | null
  dose_unit: string | null
  doses_per_day: number
  /** Times the prescription itself names, if any. */
  prescribed_times: string[]
}

/**
 * Map a MedicationRequest into the shape the Engine schedules.
 *
 * The frequency is taken from the timing repeat rather than parsed out of the
 * free text instruction. Free text carries the same information in a hundred
 * phrasings, and a scheduler that guesses wrong about frequency produces a
 * regimen with the wrong number of doses in it.
 */
export function toMedication(request: FhirMedicationRequest): ImportedMedication {
  const concept = request.medicationCodeableConcept
  const rxnorm = concept?.coding?.find((coding) => coding.system === RXNORM_SYSTEM)

  const displayName =
    rxnorm?.display ??
    concept?.text ??
    concept?.coding?.[0]?.display ??
    request.medicationReference?.display ??
    'Unnamed medication'

  const dosage = request.dosageInstruction?.[0]
  const quantity = dosage?.doseAndRate?.[0]?.doseQuantity
  const repeat = dosage?.timing?.repeat

  return {
    fhir_id: request.id,
    display_name: displayName,
    rxnorm_code: rxnorm?.code ?? null,
    dose_amount: quantity?.value ?? null,
    dose_unit: quantity?.unit ?? quantity?.code ?? null,
    doses_per_day: dosesPerDay(repeat),
    prescribed_times: repeat?.timeOfDay ?? [],
  }
}

type TimingRepeat = NonNullable<NonNullable<FhirDosage['timing']>['repeat']>

function dosesPerDay(repeat: TimingRepeat | undefined): number {
  if (!repeat) return 1
  if (repeat.timeOfDay && repeat.timeOfDay.length > 0) return repeat.timeOfDay.length

  const frequency = repeat.frequency ?? 1
  const period = repeat.period ?? 1
  const unit = repeat.periodUnit ?? 'd'

  // Normalise the period to a day so "three times per 8 hours" and "once
  // daily" land on the same scale.
  const periodInDays = unit === 'h' ? period / 24 : unit === 'wk' ? period * 7 : period
  if (periodInDays <= 0) return 1

  return Math.max(1, Math.min(6, Math.round(frequency / periodInDays)))
}

export function patientDisplayName(patient: FhirPatient): string {
  const name = patient.name?.[0]
  if (!name) return patient.id
  if (name.text) return name.text
  const given = name.given?.join(' ') ?? ''
  return `${given} ${name.family ?? ''}`.trim() || patient.id
}

export function bundleResources<T>(bundle: FhirBundle<T>): T[] {
  return (bundle.entry ?? [])
    .map((entry) => entry.resource)
    .filter((resource): resource is T => resource !== undefined)
}

export type FhirRequestError = SmartAuthError
