import { useState, useEffect, useCallback } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useLTI } from '@/contexts/LTIContext'
import { useSelection } from '@/contexts/SelectionContext'
import { Badge, Button, Card } from '@/components/ui'
import {
  getItemsByContentType,
  addExclusion,
  removeExclusion,
  type ContentItemSummary,
} from '@/api/client'
import {
  FileText,
  ClipboardList,
  MessageSquare,
  HelpCircle,
  BookOpen,
  Megaphone,
  CheckCircle2,
  MoreVertical,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

/**
 * CLU-85 Task 12: item-grouped rendering for Pages/Assignments/
 * Discussions/Quizzes/Announcements/Syllabus content-type review
 * views. Replaces the old one-row-per-issue table with one row per
 * item + checkbox + overflow menu for permanent exclusion.
 *
 * Files are handled by FilesContentView (Task 13) — not registered
 * in CONTENT_TYPE_CONFIG here.
 *
 * Must be mounted inside a SelectionProvider. Router wiring lives in
 * App.tsx at the integration step.
 */

interface ContentTypeConfig {
  label: string
  icon: LucideIcon
  apiValue: string
  description: string
}

const CONTENT_TYPE_CONFIG: Record<string, ContentTypeConfig> = {
  pages: {
    label: 'Pages',
    icon: FileText,
    apiValue: 'wiki_page',
    description: 'Wiki pages in your course',
  },
  assignments: {
    label: 'Assignments',
    icon: ClipboardList,
    apiValue: 'assignment',
    description: 'Assignment descriptions',
  },
  discussions: {
    label: 'Discussions',
    icon: MessageSquare,
    apiValue: 'discussion',
    description: 'Discussion topics',
  },
  quizzes: {
    label: 'Quizzes',
    icon: HelpCircle,
    apiValue: 'quiz',
    description: 'Quizzes and quiz questions',
  },
  syllabus: {
    label: 'Syllabus',
    icon: BookOpen,
    apiValue: 'syllabus',
    description: 'Course syllabus',
  },
  announcements: {
    label: 'Announcements',
    icon: Megaphone,
    apiValue: 'announcement',
    description: 'Course announcements',
  },
}

export function ContentTypeIssuePage() {
  const { contentType } = useParams<{ contentType: string }>()
  const { session } = useLTI()
  const navigate = useNavigate()
  const {
    isUnchecked,
    isRechecked,
    markUnchecked,
    markRechecked,
    markContentTypeTouched,
  } = useSelection()

  const [items, setItems] = useState<ContentItemSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [openMenu, setOpenMenu] = useState<string | null>(null)

  const config = contentType ? CONTENT_TYPE_CONFIG[contentType] : undefined
  const courseId = session?.canvasCourseId
  const apiValue = config?.apiValue

  useEffect(() => {
    if (!courseId || !apiValue) return
    let cancelled = false
    setLoading(true)
    setError(null)
    getItemsByContentType(courseId, apiValue)
      .then((res) => {
        if (cancelled) return
        setItems(res.items)
        markContentTypeTouched(apiValue)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Failed to load items')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [courseId, apiValue, markContentTypeTouched])

  const isItemSelected = useCallback(
    (item: ContentItemSummary): boolean => {
      if (!apiValue) return false
      // Re-check always wins (allows this-run override of permanent exclusion)
      if (isRechecked(apiValue, item.identifier)) return true
      if (isUnchecked(apiValue, item.identifier)) return false
      return !item.permanently_excluded
    },
    [apiValue, isUnchecked, isRechecked],
  )

  const toggleItem = (item: ContentItemSummary) => {
    if (!apiValue) return
    if (isItemSelected(item)) {
      markUnchecked(apiValue, item.identifier)
    } else {
      markRechecked(apiValue, item.identifier)
    }
  }

  const handleExcludePermanently = async (item: ContentItemSummary) => {
    if (!courseId || !apiValue) return
    try {
      await addExclusion(courseId, {
        item_identifier: item.identifier,
        item_type: apiValue,
      })
      setItems((prev) =>
        prev.map((i) =>
          i.identifier === item.identifier
            ? { ...i, permanently_excluded: true }
            : i,
        ),
      )
    } catch (err) {
      setError(
        `Failed to exclude: ${err instanceof Error ? err.message : String(err)}`,
      )
    }
    setOpenMenu(null)
  }

  const handleRemoveExclusion = async (item: ContentItemSummary) => {
    if (!courseId) return
    try {
      await removeExclusion(courseId, item.identifier)
      setItems((prev) =>
        prev.map((i) =>
          i.identifier === item.identifier
            ? { ...i, permanently_excluded: false }
            : i,
        ),
      )
    } catch (err) {
      setError(
        `Failed to remove exclusion: ${err instanceof Error ? err.message : String(err)}`,
      )
    }
    setOpenMenu(null)
  }

  if (!config) {
    return (
      <div className="py-12 text-center text-text-subtle">
        Unknown content type.{' '}
        <Link to="/dashboard" className="text-brand-primary underline">
          Return to Dashboard
        </Link>
      </div>
    )
  }

  const Icon = config.icon
  const selectedCount = items.filter(isItemSelected).length

  return (
    <div>
      <div className="mb-6 flex items-center gap-3">
        <Icon className="h-6 w-6 text-brand-primary" aria-hidden="true" />
        <div>
          <h2 className="text-xl font-bold text-text">{config.label}</h2>
          <p className="text-sm text-text-subtle">{config.description}</p>
        </div>
        {!loading && (
          <Badge
            variant={items.length === 0 ? 'success' : 'error'}
            className="ml-auto"
          >
            {items.length === 0
              ? '0 items with issues'
              : `${items.length} items with issues`}
          </Badge>
        )}
      </div>

      {error && (
        <Card className="mb-4 p-4 text-sm text-red-600">{error}</Card>
      )}

      {loading ? (
        <Card className="py-12 text-center">
          <div
            className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-brand-primary border-t-transparent"
            role="status"
            aria-label="Loading items"
          />
          <p className="mt-3 text-sm text-text-subtle">Loading items...</p>
        </Card>
      ) : items.length === 0 ? (
        <Card className="py-12 text-center">
          <CheckCircle2
            className="mx-auto h-12 w-12 text-green-500"
            aria-hidden="true"
          />
          <h3 className="mt-3 text-lg font-semibold text-text">
            No issues found
          </h3>
          <p className="mt-1 text-sm text-text-subtle">
            {config.label} in this course look good! No accessibility issues
            detected.
          </p>
        </Card>
      ) : (
        <>
          <div className="mb-4 flex items-center justify-between">
            <p className="text-sm text-text-muted">
              {selectedCount} of {items.length} selected for remediation
            </p>
            <Button
              onClick={() => navigate('/autoremedy/confirm')}
              disabled={selectedCount === 0}
            >
              Run Remediation
            </Button>
          </div>
          <div className="overflow-hidden rounded-lg border border-border bg-surface shadow-sm">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-border bg-surface-muted">
                <tr>
                  <th className="w-10 px-4 py-3" scope="col">
                    <span className="sr-only">Select</span>
                  </th>
                  <th
                    className="px-4 py-3 font-medium text-text-subtle"
                    scope="col"
                  >
                    Item
                  </th>
                  <th
                    className="px-4 py-3 font-medium text-text-subtle"
                    scope="col"
                  >
                    Issues
                  </th>
                  <th className="w-10 px-4 py-3" scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {items.map((item) => {
                  const selected = isItemSelected(item)
                  const canvasHref =
                    item.canvas_url && session?.canvasBaseUrl
                      ? `${session.canvasBaseUrl}${item.canvas_url}`
                      : null
                  return (
                    <tr
                      key={item.identifier}
                      className={selected ? '' : 'opacity-60'}
                    >
                      <td className="px-4 py-3">
                        <input
                          type="checkbox"
                          checked={selected}
                          onChange={() => toggleItem(item)}
                          aria-label={`Select ${item.title}`}
                        />
                      </td>
                      <td className="px-4 py-3">
                        {canvasHref ? (
                          <a
                            href={canvasHref}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-brand-primary hover:underline"
                          >
                            {item.title}
                          </a>
                        ) : (
                          item.title
                        )}
                        {item.permanently_excluded && (
                          <span className="ml-2 text-xs text-amber-700">
                            (permanently excluded)
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <span className="font-medium">{item.issue_count}</span>
                        {item.issue_severities.error > 0 && (
                          <span className="ml-2 text-xs text-red-600">
                            {item.issue_severities.error} error
                          </span>
                        )}
                        {item.issue_severities.warning > 0 && (
                          <span className="ml-2 text-xs text-amber-600">
                            {item.issue_severities.warning} warning
                          </span>
                        )}
                      </td>
                      <td className="relative px-4 py-3">
                        <button
                          onClick={() =>
                            setOpenMenu(
                              openMenu === item.identifier
                                ? null
                                : item.identifier,
                            )
                          }
                          className="rounded p-1 hover:bg-surface-muted"
                          aria-label="Actions"
                          aria-expanded={openMenu === item.identifier}
                          aria-haspopup="menu"
                        >
                          <MoreVertical size={16} aria-hidden="true" />
                        </button>
                        {openMenu === item.identifier && (
                          <div
                            className="absolute right-0 top-8 z-10 w-56 rounded border border-border bg-surface shadow-lg"
                            role="menu"
                          >
                            {item.permanently_excluded ? (
                              <button
                                onClick={() => handleRemoveExclusion(item)}
                                className="block w-full px-4 py-2 text-left text-sm hover:bg-surface-muted"
                                role="menuitem"
                              >
                                Remove permanent exclusion
                              </button>
                            ) : (
                              <button
                                onClick={() => handleExcludePermanently(item)}
                                className="block w-full px-4 py-2 text-left text-sm hover:bg-surface-muted"
                                role="menuitem"
                              >
                                Exclude permanently
                              </button>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
