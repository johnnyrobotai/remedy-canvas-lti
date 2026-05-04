import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useLTI } from '@/contexts/LTIContext'
import {
  LayoutDashboard,
  Sparkles,
  ChevronsLeft,
  ChevronsRight,
  Accessibility,
  ChevronDown,
  ChevronRight,
  FileText,
  ClipboardList,
  MessageSquare,
  HelpCircle,
  FolderOpen,
  BookOpen,
  Megaphone,
  FileCheck,
} from 'lucide-react'

const TOP_NAV = [
  { label: 'Dashboard', icon: LayoutDashboard, path: '/dashboard' },
  { label: 'Fix My Course', icon: Sparkles, path: '/autoremedy' },
  { label: 'ACR Reports', icon: FileCheck, path: '/acr' },
] as const

const CONTENT_ITEMS = [
  { label: 'Pages', icon: FileText, path: '/content/pages' },
  { label: 'Assignments', icon: ClipboardList, path: '/content/assignments' },
  { label: 'Discussions', icon: MessageSquare, path: '/content/discussions' },
  { label: 'Quizzes', icon: HelpCircle, path: '/content/quizzes' },
  { label: 'Announcements', icon: Megaphone, path: '/content/announcements' },
  { label: 'Syllabus', icon: BookOpen, path: '/content/syllabus' },
  { label: 'Files', icon: FolderOpen, path: '/content/files' },
] as const

const SIDEBAR_KEY = 'clu-sidebar-collapsed'
const CONTENT_KEY = 'clu-sidebar-content-expanded'

function getInitialCollapsed(): boolean {
  try {
    const stored = localStorage.getItem(SIDEBAR_KEY)
    if (stored !== null) return stored === 'true'
    return window.parent !== window
  } catch {
    return false
  }
}

function getInitials(name: string): string {
  return name
    .split(' ')
    .slice(0, 2)
    .map((w) => w[0])
    .join('')
    .toUpperCase()
}

export function Sidebar() {
  const { session } = useLTI()
  const location = useLocation()
  const navigate = useNavigate()
  const [collapsed, setCollapsed] = useState(getInitialCollapsed)

  // Auto-expand content section when on a /content/* route
  const onContentRoute = location.pathname.startsWith('/content/')
  const [contentExpanded, setContentExpanded] = useState(() => {
    if (onContentRoute) return true
    try {
      return localStorage.getItem(CONTENT_KEY) === 'true'
    } catch {
      return false
    }
  })

  // Keep content section expanded when navigating to a content route
  if (onContentRoute && !contentExpanded) {
    setContentExpanded(true)
  }

  const toggleCollapse = () => {
    const next = !collapsed
    setCollapsed(next)
    try { localStorage.setItem(SIDEBAR_KEY, String(next)) } catch { /* */ }
  }

  const toggleContent = () => {
    const next = !contentExpanded
    setContentExpanded(next)
    try { localStorage.setItem(CONTENT_KEY, String(next)) } catch { /* */ }
  }

  const isActive = (path: string) => {
    if (path === '/dashboard') return location.pathname === '/dashboard'
    return location.pathname === path || location.pathname.startsWith(path + '/')
  }

  const navButton = (
    item: { label: string; icon: typeof LayoutDashboard; path: string },
    indent = false,
  ) => {
    const Icon = item.icon
    const active = isActive(item.path)
    return (
      <li key={item.path}>
        <button
          type="button"
          onClick={() => navigate(item.path)}
          title={collapsed ? item.label : undefined}
          className={`flex w-full items-center gap-3 rounded-lg text-left text-sm font-medium transition ${
            indent && !collapsed ? 'pl-8 pr-3 py-2' : 'px-3 py-2.5'
          } ${
            active
              ? 'border-l-3 border-white/80 bg-surface/15 text-white'
              : 'text-blue-200 hover:bg-surface/10 hover:text-white'
          }`}
        >
          <Icon className="h-5 w-5 shrink-0" aria-hidden="true" />
          {!collapsed && <span className="truncate">{item.label}</span>}
        </button>
      </li>
    )
  }

  return (
    <aside
      className="flex h-screen flex-col bg-[#003D66] transition-[width] duration-200"
      style={{ width: collapsed ? 'var(--sidebar-collapsed-width)' : 'var(--sidebar-width)' }}
    >
      {/* Brand */}
      <div className="flex h-16 shrink-0 items-center gap-2 overflow-hidden px-4">
        <Accessibility className="h-6 w-6 shrink-0 text-white" aria-hidden="true" />
        {!collapsed && (
          <span className="whitespace-nowrap text-sm font-bold text-white">
            Remedy Canvas LTI
          </span>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-2 py-2" aria-label="Main navigation">
        <ul className="space-y-1">
          {TOP_NAV.map((item) => navButton(item))}

          {/* Course Content collapsible section */}
          <li>
            <button
              type="button"
              onClick={collapsed ? () => { setCollapsed(false); setContentExpanded(true) } : toggleContent}
              title={collapsed ? 'Course Content' : undefined}
              aria-expanded={contentExpanded}
              className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm font-medium transition ${
                onContentRoute && !contentExpanded
                  ? 'bg-surface/15 text-white'
                  : 'text-blue-200 hover:bg-surface/10 hover:text-white'
              }`}
            >
              <BookOpen className="h-5 w-5 shrink-0" aria-hidden="true" />
              {!collapsed && (
                <>
                  <span className="flex-1 truncate">Course Content</span>
                  {contentExpanded ? (
                    <ChevronDown className="h-4 w-4 shrink-0" aria-hidden="true" />
                  ) : (
                    <ChevronRight className="h-4 w-4 shrink-0" aria-hidden="true" />
                  )}
                </>
              )}
            </button>

            {/* Sub-items */}
            {contentExpanded && !collapsed && (
              <ul className="mt-1 space-y-0.5">
                {CONTENT_ITEMS.map((item) => navButton(item, true))}
              </ul>
            )}
          </li>
        </ul>
      </nav>

      {/* User section */}
      {session && (
        <div className="shrink-0 border-t border-white/10 px-3 py-3">
          <div className="flex items-center gap-3 overflow-hidden">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface/20 text-xs font-bold text-white">
              {getInitials(session.userName)}
            </div>
            {!collapsed && (
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-white">
                  {session.userName}
                </p>
                <p className="truncate text-xs text-blue-200">
                  {session.userEmail}
                </p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Collapse toggle */}
      <button
        type="button"
        onClick={toggleCollapse}
        className="flex h-10 shrink-0 items-center justify-center border-t border-white/10 text-blue-200 hover:bg-surface/10 hover:text-white"
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      >
        {collapsed ? (
          <ChevronsRight className="h-4 w-4" />
        ) : (
          <ChevronsLeft className="h-4 w-4" />
        )}
      </button>
    </aside>
  )
}
