/** Every Croniixx endpoint returns this shape, success or failure. */

export interface ApiError {
  code: string
  message: string
  details?: Record<string, unknown> | null
}

export interface ApiMeta {
  service: string
  request_id?: string | null
  generated_at: string
  extra?: Record<string, unknown>
}

export interface ApiEnvelope<T> {
  data: T | null
  error: ApiError | null
  meta: ApiMeta
}

/** Narrows an envelope to its success case. */
export function isOk<T>(
  envelope: ApiEnvelope<T>,
): envelope is ApiEnvelope<T> & { data: T; error: null } {
  return envelope.error === null && envelope.data !== null
}
