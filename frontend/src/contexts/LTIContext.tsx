import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react'
import { apiClient, setToken, ApiError } from '@/api/client'

export interface LTISession {
  canvasCourseId: number
  courseName: string
  userId: string
  userName: string
  userEmail: string
  roles: string[]
  isInstructor: boolean
  isAdmin: boolean
  sessionToken: string
  canvasBaseUrl: string
}

interface LTIContextValue {
  session: LTISession | null
  loading: boolean
  error: string | null
}

const LTIContext = createContext<LTIContextValue | null>(null)

export function LTIProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<LTISession | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    async function fetchSession() {
      try {
        const data = await apiClient.get<LTISession>('/session')
        if (!cancelled) {
          if (data.sessionToken) {
            setToken(data.sessionToken)
          }
          setSession(data)
        }
      } catch (err) {
        if (!cancelled) {
          if (err instanceof ApiError) {
            const params = new URLSearchParams(window.location.search)
            if (params.get('oauth') === 'required') {
              try {
                const { url } = await apiClient.get<{ url: string }>('/oauth2/url')
                window.location.href = url
                return
              } catch {
                // Fall through to normal error handling
              }
            }
            setError(
              err.status === 401
                ? 'Session expired or not found. Please relaunch from Canvas.'
                : `Failed to load session (HTTP ${err.status}).`,
            )
          } else {
            setError('Unable to connect to the accessibility tool server.')
          }
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    fetchSession()
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <LTIContext.Provider value={{ session, loading, error }}>
      {children}
    </LTIContext.Provider>
  )
}

export function useLTI(): LTIContextValue {
  const ctx = useContext(LTIContext)
  if (!ctx) {
    throw new Error('useLTI must be used inside LTIProvider')
  }
  return ctx
}
