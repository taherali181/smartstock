import { useEffect, useMemo, useState } from 'react'
import { UserManager, type User } from 'oidc-client-ts'
import { configureAccessToken } from '../api/client'
import { AuthContext, type AuthContextValue, type AuthStatus, useAuth } from './context'
const authMode = import.meta.env.VITE_AUTH_MODE || 'development'

function createManager() {
  return new UserManager({
    authority: import.meta.env.VITE_OIDC_AUTHORITY || 'http://localhost:8080/realms/smartstock',
    client_id: import.meta.env.VITE_OIDC_CLIENT_ID || 'smartstock-web',
    redirect_uri: window.location.origin,
    post_logout_redirect_uri: window.location.origin,
    response_type: 'code',
    scope: 'openid profile email',
    automaticSilentRenew: true,
    monitorSession: true,
  })
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const manager = useMemo(() => authMode === 'oidc' ? createManager() : null, [])
  const [user, setUser] = useState<User | null>(null)
  const [status, setStatus] = useState<AuthStatus>(authMode === 'oidc' ? 'loading' : 'authenticated')

  useEffect(() => {
    if (!manager) {
      configureAccessToken(null)
      return
    }
    let active = true
    const applyUser = (nextUser: User | null) => {
      if (!active) return
      setUser(nextUser)
      configureAccessToken(nextUser?.access_token ?? null)
      setStatus(nextUser && !nextUser.expired ? 'authenticated' : 'anonymous')
    }
    const initialize = async () => {
      try {
        const callback = new URLSearchParams(window.location.search).has('code')
        if (callback) {
          const nextUser = await manager.signinRedirectCallback()
          window.history.replaceState({}, document.title, window.location.pathname)
          applyUser(nextUser)
        } else {
          applyUser(await manager.getUser())
        }
      } catch {
        applyUser(null)
      }
    }
    manager.events.addUserLoaded(applyUser)
    manager.events.addUserUnloaded(() => applyUser(null))
    void initialize()
    return () => {
      active = false
      manager.events.removeUserLoaded(applyUser)
    }
  }, [manager])

  const value = useMemo<AuthContextValue>(() => ({
    status,
    user,
    signIn: async () => {
      if (manager) await manager.signinRedirect()
    },
    signOut: async () => {
      if (manager) await manager.signoutRedirect()
    },
  }), [manager, status, user])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function AuthGate({ children }: { children: React.ReactNode }) {
  const auth = useAuth()
  if (auth.status === 'loading') return <main className="auth-gate">Connecting to SmartStock…</main>
  if (auth.status === 'anonymous') {
    return (
      <main className="auth-gate">
        <h1>SmartStock</h1>
        <p>Sign in through your organization’s secure workspace.</p>
        <button type="button" onClick={() => void auth.signIn()}>Sign in</button>
      </main>
    )
  }
  return children
}
