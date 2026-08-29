import createClient from 'openapi-fetch'
import type { paths } from './schema'

let accessToken: string | null = null

export function configureAccessToken(token: string | null) {
  accessToken = token
}

export const apiClient = createClient<paths>({
  baseUrl: import.meta.env.VITE_API_URL || 'http://localhost:8000',
})

apiClient.use({
  async onRequest({ request }) {
    if (accessToken) request.headers.set('Authorization', `Bearer ${accessToken}`)
    return request
  },
})
