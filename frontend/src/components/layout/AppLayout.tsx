import { useEffect } from 'react'
import { Navigate, Outlet } from 'react-router-dom'
import { useLTI } from '@/contexts/LTIContext'
import { Spinner } from '@/components/ui/Spinner'
import { PageActionsProvider } from './PageActionsContext'
import { Sidebar } from './Sidebar'
import { TopBar } from './TopBar'

export function AppLayout() {
  const { session, loading, error } = useLTI()

  // Request full-width iframe from Canvas LMS
  useEffect(() => {
    if (window.parent !== window) {
      window.parent.postMessage(
        JSON.stringify({ subject: 'lti.frameResize', height: window.innerHeight }),
        '*',
      )
      // Also request removal of scrollbar and full width via Canvas postMessage API
      window.parent.postMessage(
        JSON.stringify({ subject: 'lti.showModuleNavigation', show: false }),
        '*',
      )
    }
  }, [])

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-surface-muted">
        <Spinner size="lg" label="Loading application" />
      </div>
    )
  }

  if (!session || error) {
    return <Navigate to="/" replace />
  }

  return (
    <PageActionsProvider>
      <div className="flex h-screen overflow-hidden bg-surface-muted">
        <Sidebar />
        <div className="flex flex-1 flex-col overflow-hidden">
          <TopBar />
          <main className="flex-1 overflow-y-auto p-6">
            <Outlet />
          </main>
        </div>
      </div>
    </PageActionsProvider>
  )
}
