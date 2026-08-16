/**
 * SMART on FHIR authorization, EHR launch and standalone launch.
 *
 * This is the real sequence, not a placeholder:
 *
 *   1. Read the FHIR server's SMART configuration to find its authorize and
 *      token endpoints. Hardcoding endpoints per vendor is what makes an
 *      integration break on every customer.
 *   2. Build an authorize URL carrying a PKCE challenge, a state value, an
 *      aud parameter naming the FHIR server, and the launch token when the
 *      app was launched from inside the EHR.
 *   3. Exchange the returned code for an access token using the verifier.
 *   4. Read patient, id_token, and the granted scopes out of the token
 *      response, since SMART returns launch context alongside the token.
 *
 * PKCE is mandatory in SMART App Launch 2.0 and used here unconditionally.
 * A public client without it can have its authorization code intercepted.
 */

export interface SmartConfiguration {
  authorization_endpoint: string
  token_endpoint: string
  introspection_endpoint?: string
  revocation_endpoint?: string
  capabilities?: string[]
  code_challenge_methods_supported?: string[]
  scopes_supported?: string[]
  issuer?: string
}

export interface LaunchParams {
  /** FHIR base URL. Supplied by the EHR as `iss` on an EHR launch. */
  iss: string
  /** Opaque launch token from the EHR. Absent on a standalone launch. */
  launch?: string
  clientId: string
  redirectUri: string
  scope: string
}

export interface AuthorizeRequest {
  url: string
  state: string
  codeVerifier: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
  scope: string
  refresh_token?: string
  id_token?: string
  /** SMART launch context: the patient the session is about. */
  patient?: string
  encounter?: string
  need_patient_banner?: boolean
  smart_style_url?: string
}

export class SmartAuthError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message)
    this.name = 'SmartAuthError'
  }
}

const WELL_KNOWN_PATH = '.well-known/smart-configuration'

/** Fetch the SMART configuration advertised by a FHIR server. */
export async function discoverSmartConfiguration(
  fhirBaseUrl: string,
  fetchImpl: typeof fetch = fetch,
): Promise<SmartConfiguration> {
  const base = fhirBaseUrl.endsWith('/') ? fhirBaseUrl : `${fhirBaseUrl}/`
  const response = await fetchImpl(`${base}${WELL_KNOWN_PATH}`, {
    headers: { accept: 'application/json' },
  })

  if (!response.ok) {
    throw new SmartAuthError(
      `SMART configuration unavailable at ${base}${WELL_KNOWN_PATH}`,
      response.status,
    )
  }

  const config = (await response.json()) as SmartConfiguration
  if (!config.authorization_endpoint || !config.token_endpoint) {
    throw new SmartAuthError('SMART configuration is missing required endpoints')
  }
  return config
}

/** Build the authorize URL along with the state and verifier to keep. */
export async function buildAuthorizeRequest(
  config: SmartConfiguration,
  params: LaunchParams,
): Promise<AuthorizeRequest> {
  const state = randomString(32)
  const codeVerifier = randomString(64)
  const codeChallenge = await deriveCodeChallenge(codeVerifier)

  const url = new URL(config.authorization_endpoint)
  url.searchParams.set('response_type', 'code')
  url.searchParams.set('client_id', params.clientId)
  url.searchParams.set('redirect_uri', params.redirectUri)
  url.searchParams.set('scope', params.scope)
  url.searchParams.set('state', state)
  // aud names the FHIR server this token is for. Without it a token issued
  // for one server can be presented to another.
  url.searchParams.set('aud', params.iss)
  url.searchParams.set('code_challenge', codeChallenge)
  url.searchParams.set('code_challenge_method', 'S256')

  if (params.launch) {
    url.searchParams.set('launch', params.launch)
  }

  return { url: url.toString(), state, codeVerifier }
}

/** Exchange an authorization code for an access token. */
export async function exchangeCodeForToken(
  config: SmartConfiguration,
  args: {
    code: string
    clientId: string
    redirectUri: string
    codeVerifier: string
    clientSecret?: string
  },
  fetchImpl: typeof fetch = fetch,
): Promise<TokenResponse> {
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    code: args.code,
    redirect_uri: args.redirectUri,
    client_id: args.clientId,
    code_verifier: args.codeVerifier,
  })

  const headers: Record<string, string> = {
    'content-type': 'application/x-www-form-urlencoded',
    accept: 'application/json',
  }

  // A confidential client authenticates with HTTP Basic. A public client sends
  // client_id in the body only, which is already set above.
  if (args.clientSecret) {
    headers.authorization = `Basic ${base64(`${args.clientId}:${args.clientSecret}`)}`
  }

  const response = await fetchImpl(config.token_endpoint, {
    method: 'POST',
    headers,
    body: body.toString(),
  })

  const payload = await response.json()

  if (!response.ok) {
    throw new SmartAuthError(
      `Token exchange failed: ${payload?.error_description ?? payload?.error ?? response.statusText}`,
      response.status,
    )
  }

  return payload as TokenResponse
}

/** Refresh an access token using a refresh token from an offline_access grant. */
export async function refreshAccessToken(
  config: SmartConfiguration,
  args: { refreshToken: string; clientId: string; clientSecret?: string; scope?: string },
  fetchImpl: typeof fetch = fetch,
): Promise<TokenResponse> {
  const body = new URLSearchParams({
    grant_type: 'refresh_token',
    refresh_token: args.refreshToken,
    client_id: args.clientId,
  })
  if (args.scope) body.set('scope', args.scope)

  const headers: Record<string, string> = {
    'content-type': 'application/x-www-form-urlencoded',
    accept: 'application/json',
  }
  if (args.clientSecret) {
    headers.authorization = `Basic ${base64(`${args.clientId}:${args.clientSecret}`)}`
  }

  const response = await fetchImpl(config.token_endpoint, {
    method: 'POST',
    headers,
    body: body.toString(),
  })

  const payload = await response.json()
  if (!response.ok) {
    throw new SmartAuthError(
      `Token refresh failed: ${payload?.error_description ?? response.statusText}`,
      response.status,
    )
  }
  return payload as TokenResponse
}

/**
 * Verify the state value returned by the authorization server.
 * Compared in constant time so a mismatch cannot be probed character by
 * character through timing.
 */
export function verifyState(expected: string, received: string | null): boolean {
  if (!received || expected.length !== received.length) return false
  let difference = 0
  for (let index = 0; index < expected.length; index += 1) {
    difference |= expected.charCodeAt(index) ^ received.charCodeAt(index)
  }
  return difference === 0
}

// ---------------------------------------------------------------------------
// PKCE helpers
// ---------------------------------------------------------------------------

const VERIFIER_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~'

export function randomString(length: number): string {
  const bytes = new Uint8Array(length)
  crypto.getRandomValues(bytes)
  let out = ''
  for (const byte of bytes) {
    out += VERIFIER_ALPHABET[byte % VERIFIER_ALPHABET.length]
  }
  return out
}

export async function deriveCodeChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))
  return base64UrlEncode(new Uint8Array(digest))
}

export function base64UrlEncode(bytes: Uint8Array): string {
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return base64(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function base64(input: string): string {
  if (typeof btoa === 'function') return btoa(input)
  // Node without a DOM global.
  return Buffer.from(input, 'binary').toString('base64')
}
