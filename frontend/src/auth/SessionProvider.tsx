import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, ApiError, sessionExpiredEvent, setSessionToken, type Session } from '../api/client'
import { ErrorState, Loading } from '../components/ui'
import AuthPage from './AuthPage'

type SessionContextValue = {
  session: Session
  updateSession: (session: Session) => void
  logout: () => Promise<void>
}
const SessionContext = createContext<SessionContextValue | null>(null)

export function useSession() {
  const value = useContext(SessionContext)
  if (!value) throw new Error('Рабочее пространство должно быть внутри SessionProvider.')
  return value
}

export default function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [expired, setExpired] = useState(false)
  const updateSession = useCallback((value: Session) => {
    setSessionToken(value.csrf_token)
    setSession(value)
    setExpired(false)
  }, [])
  const restore = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      updateSession(await api.session())
    } catch (cause) {
      setSessionToken(null)
      setSession(null)
      if (!(cause instanceof ApiError && cause.status === 401)) setError((cause as Error).message)
    } finally {
      setLoading(false)
    }
  }, [updateSession])
  useEffect(() => {
    void restore()
    const onExpired = () => {
      setSessionToken(null)
      setSession(null)
      setExpired(true)
    }
    window.addEventListener(sessionExpiredEvent, onExpired)
    return () => window.removeEventListener(sessionExpiredEvent, onExpired)
  }, [restore])

  if (loading)
    return (
      <div className="session-loading">
        <Loading text="Проверяем сессию…" />
      </div>
    )
  if (error)
    return (
      <div className="session-loading">
        <ErrorState message={error} retry={() => void restore()} />
      </div>
    )
  if (!session) return <AuthPage onAuthenticated={updateSession} expired={expired} />

  return (
    <SessionContext.Provider
      value={{
        session,
        updateSession,
        logout: async () => {
          await api.logout()
          setSessionToken(null)
          setSession(null)
          setExpired(false)
        },
      }}
    >
      <div key={session.user?.id || 'local'}>{children}</div>
    </SessionContext.Provider>
  )
}
