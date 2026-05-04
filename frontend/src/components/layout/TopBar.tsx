import { useLocation } from 'react-router-dom'
import { useLTI } from '@/contexts/LTIContext'
import { usePageActionsSlot } from './PageActionsContext'
import { ThemeToggle } from '@/components/ui/ThemeToggle'

const ROUTE_LABELS: Record<string, string> = {
  '/dashboard': 'Dashboard',
  '/scan': 'Scan Results',
  '/remediate': 'Remediate',
  '/autoremedy': 'AutoRemedy',
  '/files': 'Course Files',
  '/contrast': 'Contrast Checker',
  '/dochub': 'DocHub',
  '/admin': 'Admin',
}

function getPageLabel(pathname: string): string {
  if (ROUTE_LABELS[pathname]) return ROUTE_LABELS[pathname]
  for (const [route, label] of Object.entries(ROUTE_LABELS)) {
    if (pathname.startsWith(route + '/')) return label
  }
  return 'Page'
}

export function TopBar() {
  const { session } = useLTI()
  const location = useLocation()
  const pageActions = usePageActionsSlot()

  const pageLabel = getPageLabel(location.pathname)

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-surface px-6">
      <div className="flex items-center gap-2 text-sm">
        <span className="font-medium text-text">
          {session?.courseName ?? 'Course'}
        </span>
        <span className="text-text-subtle">/</span>
        <span className="text-text-subtle">{pageLabel}</span>
      </div>
      <div className="ml-auto flex items-center gap-2">
        {pageActions && <div className="flex items-center gap-2">{pageActions}</div>}
        <ThemeToggle />
      </div>
    </header>
  )
}
