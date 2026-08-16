import { cookies } from 'next/headers'
import {
  FhirClient,
  buildAuthorizeRequest,
  discoverSmartConfiguration,
  exchangeCodeForToken,
  toMedication,
  verifyState,
  type ImportedMedication,
  type SmartConfiguration,
  type TokenResponse,
} from '@croniixx/fhir-client'

/**
 * Server side glue for the SMART on FHIR launch.
 *
 * The PKCE verifier and the state value are held in httpOnly cookies for the
 * duration of the redirect. They must not reach client JavaScript: a verifier
 * readable from the page is a verifier an injected script can steal, and PKCE
 * then protects nothing.
 */

const STATE_COOKIE = 'croniixx_smart_state'
const VERIFIER_COOKIE = 'croniixx_smart_verifier'
const ISS_COOKIE = 'croniixx_smart_iss'
const TOKEN_COOKIE = 'croniixx_fhir_token'

const DEFAULT_SCOPE =
  process.env.FHIR_SCOPES ??
  'launch openid fhirUser profile patient/Patient.read patient/MedicationRequest.read offline_access'

const COOKIE_OPTIONS = {
  httpOnly: true,
  sameSite: 'lax' as const,
  secure: process.env.NODE_ENV === 'production',
  path: '/',
  maxAge: 600,
}

export interface LaunchResult {
  authorizeUrl: string
}

export async function beginSmartLaunch(iss: string, launch?: string): Promise<LaunchResult> {
  const clientId = requireEnv('FHIR_CLIENT_ID')
  const redirectUri = requireEnv('FHIR_REDIRECT_URI')

  const config = await discoverSmartConfiguration(iss)
  const request = await buildAuthorizeRequest(config, {
    iss,
    launch,
    clientId,
    redirectUri,
    scope: DEFAULT_SCOPE,
  })

  const jar = cookies()
  jar.set(STATE_COOKIE, request.state, COOKIE_OPTIONS)
  jar.set(VERIFIER_COOKIE, request.codeVerifier, COOKIE_OPTIONS)
  jar.set(ISS_COOKIE, iss, COOKIE_OPTIONS)

  return { authorizeUrl: request.url }
}

export async function completeSmartLaunch(
  code: string,
  state: string | null,
): Promise<TokenResponse> {
  const jar = cookies()
  const expectedState = jar.get(STATE_COOKIE)?.value
  const verifier = jar.get(VERIFIER_COOKIE)?.value
  const iss = jar.get(ISS_COOKIE)?.value

  if (!expectedState || !verifier || !iss) {
    throw new Error('Launch context is missing. Start the launch again from the EHR.')
  }
  if (!verifyState(expectedState, state)) {
    throw new Error('State mismatch on the authorization callback.')
  }

  const config = await discoverSmartConfiguration(iss)
  const token = await exchangeCodeForToken(config, {
    code,
    clientId: requireEnv('FHIR_CLIENT_ID'),
    redirectUri: requireEnv('FHIR_REDIRECT_URI'),
    codeVerifier: verifier,
    clientSecret: process.env.FHIR_CLIENT_SECRET,
  })

  jar.delete(STATE_COOKIE)
  jar.delete(VERIFIER_COOKIE)

  jar.set(TOKEN_COOKIE, JSON.stringify({ token, iss }), {
    ...COOKIE_OPTIONS,
    maxAge: token.expires_in ?? 3600,
  })

  return token
}

export function currentFhirSession(): { token: TokenResponse; iss: string } | null {
  const raw = cookies().get(TOKEN_COOKIE)?.value
  if (!raw) return null
  try {
    return JSON.parse(raw) as { token: TokenResponse; iss: string }
  } catch {
    return null
  }
}

/** Pull a patient's active regimen out of the EHR. */
export async function importRegimen(patientId: string): Promise<ImportedMedication[]> {
  const session = currentFhirSession()
  if (!session) throw new Error('No active FHIR session')

  const client = new FhirClient({
    baseUrl: session.iss,
    accessToken: session.token.access_token,
  })

  const requests = await client.getMedicationRequests(patientId)
  return requests.map(toMedication)
}

export async function smartConfigurationFor(iss: string): Promise<SmartConfiguration> {
  return discoverSmartConfiguration(iss)
}

function requireEnv(name: string): string {
  const value = process.env[name]
  if (!value) {
    throw new Error(`${name} is not set. SMART on FHIR cannot start without it.`)
  }
  return value
}
