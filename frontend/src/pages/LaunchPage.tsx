import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useLTI } from '@/contexts/LTIContext'

export function LaunchPage() {
  const { session, loading, error } = useLTI()
  const navigate = useNavigate()

  useEffect(() => {
    if (session) {
      navigate('/dashboard', { replace: true })
    }
  }, [session, navigate])

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-surface-muted">
        <div className="text-center">
          <div
            className="mx-auto mb-4 h-12 w-12 animate-spin rounded-full border-4 border-border border-t-brand-primary"
            role="status"
            aria-label="Loading"
          />
          <p className="text-sm text-text-muted">
            Connecting to Canvas course...
          </p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-surface-muted">
        <div className="max-w-md rounded-lg border border-red-200 bg-surface p-6 shadow-sm">
          <h1 className="mb-2 text-lg font-semibold text-red-700">
            Launch Failed
          </h1>
          <p className="text-sm text-text-muted">{error}</p>
          <p className="mt-4 text-xs text-text-subtle">
            Return to your Canvas course and relaunch the accessibility tool
            from the course navigation.
          </p>
        </div>
      </div>
    )
  }

  return null
}
