import createClient from 'openapi-fetch'
import type { paths } from './schema'

const authMode = import.meta.env.VITE_AUTH_MODE || 'development'

// Development identity. Keycloak is not required to run the stack locally; the
// API accepts these headers only when SMARTSTOCK_AUTH_MODE=development, and
// refuses them outright when the environment is production.
const developmentUser =
  import.meta.env.VITE_DEV_USER || '00000000-0000-0000-0000-000000000001'
const developmentOrganization =
  import.meta.env.VITE_DEV_ORGANIZATION || '00000000-0000-0000-0000-000000000001'

let accessToken: string | null = null

export function configureAccessToken(token: string | null) {
  accessToken = token
}

export const apiBaseUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000'

export const apiClient = createClient<paths>({ baseUrl: apiBaseUrl })

apiClient.use({
  async onRequest({ request }) {
    if (accessToken) {
      request.headers.set('Authorization', `Bearer ${accessToken}`)
    } else if (authMode === 'development') {
      request.headers.set('X-Development-User', developmentUser)
      request.headers.set('X-Development-Organization', developmentOrganization)
    }
    if (!request.headers.has('X-Correlation-ID')) {
      request.headers.set('X-Correlation-ID', crypto.randomUUID())
    }
    return request
  },
})

/** Headers for a state-changing command: every mutation needs its own key. */
export function commandHeaders(extra?: Record<string, string>) {
  return { 'Idempotency-Key': crypto.randomUUID(), ...extra }
}

/** Auth/identity headers for transports that bypass openapi-fetch, such as SSE. */
export function identityHeaders(): Record<string, string> {
  if (accessToken) return { Authorization: `Bearer ${accessToken}` }
  if (authMode === 'development') {
    return {
      'X-Development-User': developmentUser,
      'X-Development-Organization': developmentOrganization,
    }
  }
  return {}
}
