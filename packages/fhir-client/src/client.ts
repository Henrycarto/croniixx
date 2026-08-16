/**
 * Authenticated FHIR reads.
 *
 * Paging is followed to completion rather than returning the first page. A
 * regimen truncated at the first twenty MedicationRequests would produce a
 * schedule that quietly omits doses, which is the one failure mode this system
 * must not have.
 */

import type { FhirBundle, FhirMedicationRequest, FhirPatient } from './resources'
import { bundleResources } from './resources'
import { SmartAuthError } from './smart'

export interface FhirClientOptions {
  baseUrl: string
  accessToken: string
  fetchImpl?: typeof fetch
  /** Stop after this many pages. A runaway server should not hang a request. */
  maxPages?: number
}

export class FhirClient {
  private readonly baseUrl: string
  private readonly accessToken: string
  private readonly fetchImpl: typeof fetch
  private readonly maxPages: number

  constructor(options: FhirClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, '')
    this.accessToken = options.accessToken
    this.fetchImpl = options.fetchImpl ?? fetch
    this.maxPages = options.maxPages ?? 20
  }

  private async read<T>(path: string): Promise<T> {
    const url = path.startsWith('http') ? path : `${this.baseUrl}/${path.replace(/^\//, '')}`

    const response = await this.fetchImpl(url, {
      headers: {
        authorization: `Bearer ${this.accessToken}`,
        accept: 'application/fhir+json',
      },
    })

    if (response.status === 401) {
      throw new SmartAuthError('FHIR access token rejected', 401)
    }
    if (!response.ok) {
      throw new SmartAuthError(`FHIR request failed for ${path}`, response.status)
    }

    return (await response.json()) as T
  }

  getPatient(patientId: string): Promise<FhirPatient> {
    return this.read<FhirPatient>(`Patient/${patientId}`)
  }

  /** Every active MedicationRequest for a patient, across all pages. */
  async getMedicationRequests(patientId: string): Promise<FhirMedicationRequest[]> {
    const collected: FhirMedicationRequest[] = []
    let next: string | null = `MedicationRequest?patient=${encodeURIComponent(patientId)}&status=active&_count=50`
    let pages = 0

    while (next && pages < this.maxPages) {
      const bundle: FhirBundle<FhirMedicationRequest> =
        await this.read<FhirBundle<FhirMedicationRequest>>(next)
      collected.push(...bundleResources(bundle))
      next = bundle.link?.find((link) => link.relation === 'next')?.url ?? null
      pages += 1
    }

    return collected
  }

  /** Confirm the token works before starting a longer import. */
  async capabilityCheck(): Promise<boolean> {
    try {
      await this.read<unknown>('metadata')
      return true
    } catch {
      return false
    }
  }
}
