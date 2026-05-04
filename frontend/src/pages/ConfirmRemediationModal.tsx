import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useLTI } from '@/contexts/LTIContext'
import { useSelection } from '@/contexts/SelectionContext'
import { Button, Card } from '@/components/ui'
import {
  getItemsByContentType,
  getConversionCandidates,
  startSelectiveAutoRemedy,
  type ContentItemSummary,
  type ConversionCandidate,
} from '@/api/client'
import { AlertTriangle, CheckCircle2, Clock } from 'lucide-react'

/**
 * CLU-85 Task 14: mandatory confirmation step before AutoRemedy runs
 * from a content-type review view. Rendered as a route (not a true
 * dialog) at `/autoremedy/confirm`. Must be wrapped in
 * SelectionProvider — Task 12 handles the router wiring.
 *
 * Calculates scope by fetching items for every HTML content type and
 * the file conversion candidates, then subtracting the user's
 * ephemeral unchecks and adding back any re-checks. Unreviewed types
 * get a "(not reviewed)" badge in the summary card.
 */

const HTML_CONTENT_TYPES: { apiValue: string; label: string }[] = [
  { apiValue: 'wiki_page', label: 'Pages' },
  { apiValue: 'assignment', label: 'Assignments' },
  { apiValue: 'discussion', label: 'Discussions' },
  { apiValue: 'quiz', label: 'Quizzes' },
  { apiValue: 'announcement', label: 'Announcements' },
  { apiValue: 'syllabus', label: 'Syllabus' },
]

interface ScopeSummary {
  contentTypeLabel: string
  apiValue: string
  total: number
  selected: number
  userDeselected: number
  autoExcluded: number
  reviewed: boolean
}

export function ConfirmRemediationModal() {
  const { session } = useLTI()
  const navigate = useNavigate()
  const {
    isUnchecked,
    isRechecked,
    getUncheckedList,
    getRecheckedList,
    getTouchedContentTypes,
  } = useSelection()

  const [scope, setScope] = useState<ScopeSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const courseId = session?.canvasCourseId

  useEffect(() => {
    if (!courseId) return

    let cancelled = false
    const touched = new Set(getTouchedContentTypes())

    async function load() {
      const summaries: ScopeSummary[] = []

      // HTML content types
      for (const ct of HTML_CONTENT_TYPES) {
        try {
          const res = await getItemsByContentType(courseId!, ct.apiValue)
          const selected = res.items.filter((i: ContentItemSummary) => {
            // Re-check beats everything else (can override permanent exclusion
            // for this run only).
            if (isRechecked(ct.apiValue, i.identifier)) return true
            if (isUnchecked(ct.apiValue, i.identifier)) return false
            return !i.permanently_excluded
          }).length
          const userDeselected = res.items.filter(
            (i: ContentItemSummary) =>
              isUnchecked(ct.apiValue, i.identifier) &&
              !i.permanently_excluded,
          ).length
          summaries.push({
            contentTypeLabel: ct.label,
            apiValue: ct.apiValue,
            total: res.items.length,
            selected,
            userDeselected,
            autoExcluded: 0,
            reviewed: touched.has(ct.apiValue),
          })
        } catch {
          // Skip content types that failed to load — don't block the modal
        }
      }

      // Files (conversion candidates)
      try {
        const filesRes = await getConversionCandidates(courseId!)
        const unchecked = new Set(getUncheckedList('file'))
        const rechecked = new Set(getRecheckedList('file'))
        const hardExcluded = filesRes.candidates.filter(
          (c: ConversionCandidate) => c.hard_excluded,
        ).length
        const selected = filesRes.candidates.filter((c: ConversionCandidate) => {
          if (c.hard_excluded) return false
          const id = `file-${c.file_id}`
          if (rechecked.has(id)) return true
          if (unchecked.has(id)) return false
          return c.default_selected && !c.permanently_excluded
        }).length
        summaries.push({
          contentTypeLabel: 'Files',
          apiValue: 'file',
          total: filesRes.candidates.length,
          selected,
          userDeselected: unchecked.size,
          autoExcluded: hardExcluded,
          reviewed: touched.has('file'),
        })
      } catch {
        // Skip files if fetch fails
      }

      if (!cancelled) {
        setScope(summaries)
        setLoading(false)
      }
    }

    load()
    return () => {
      cancelled = true
    }
  }, [
    courseId,
    isUnchecked,
    isRechecked,
    getUncheckedList,
    getRecheckedList,
    getTouchedContentTypes,
  ])

  const handleConfirm = async () => {
    if (!courseId) return
    setSubmitting(true)
    setError(null)
    try {
      // Build skip lists from every touched HTML content type
      const skipPageIdentifiers: string[] = []
      for (const ct of HTML_CONTENT_TYPES) {
        skipPageIdentifiers.push(...getUncheckedList(ct.apiValue))
      }
      const skipFileIds: number[] = getUncheckedList('file')
        .map((id) => id.replace('file-', ''))
        .map(Number)
        .filter((n) => !Number.isNaN(n))

      await startSelectiveAutoRemedy(courseId, {
        reviewed_content_types: getTouchedContentTypes(),
        skip_page_identifiers: skipPageIdentifiers,
        skip_file_ids: skipFileIds,
      })
      navigate('/autoremedy')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start remediation')
      setSubmitting(false)
    }
  }

  // Rough estimate: 30 seconds per selected item. Matches the
  // "15-30 minutes" scope example in the spec for a typical 50-item run.
  const totalSelected = scope.reduce((sum, s) => sum + s.selected, 0)
  const estimatedMinutes = Math.max(1, Math.ceil(totalSelected * 0.5))
  const nothingSelected = !loading && totalSelected === 0

  if (loading) {
    return (
      <div className="mx-auto max-w-2xl">
        <Card className="p-8">
          <p className="text-center text-text-subtle">Calculating scope...</p>
        </Card>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-2xl">
      <Card className="p-6">
        <h2 className="mb-4 text-xl font-bold">Ready to run AutoRemedy?</h2>
        <p className="mb-6 text-sm text-text-muted">
          The full pipeline will run with the selections you made. Review the scope
          below before confirming.
        </p>

        <div className="mb-6 space-y-3">
          {scope.map((s) => (
            <div key={s.apiValue} className="rounded border border-border p-3">
              <div className="flex items-center justify-between">
                <span className="font-medium">
                  {s.contentTypeLabel}
                  {!s.reviewed && (
                    <span className="ml-2 text-xs text-amber-700">(not reviewed)</span>
                  )}
                </span>
                <CheckCircle2
                  size={16}
                  className="text-green-500"
                  aria-hidden="true"
                />
              </div>
              <div className="mt-1 text-sm text-text-muted">
                {s.selected} of {s.total} will be processed
                {s.userDeselected > 0 && `, ${s.userDeselected} deselected by you`}
                {s.autoExcluded > 0 && `, ${s.autoExcluded} auto-excluded`}
              </div>
            </div>
          ))}
        </div>

        <div className="mb-6 rounded border border-amber-200 bg-amber-50 p-3 text-sm">
          <div className="flex items-start gap-2">
            <AlertTriangle
              size={16}
              className="mt-0.5 text-amber-600"
              aria-hidden="true"
            />
            <div>
              <p className="font-medium text-amber-900">
                Also processed with all items
              </p>
              <p className="mt-1 text-amber-800">
                Calendar events and rubrics will be processed automatically. There
                is no review surface for them in this version.
              </p>
            </div>
          </div>
        </div>

        <div className="mb-6 flex items-center gap-2 text-sm text-text-muted">
          <Clock size={16} aria-hidden="true" />
          <span>Estimated time: {estimatedMinutes} minutes</span>
        </div>

        {error && (
          <div className="mb-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">
            {error}
          </div>
        )}

        <div className="flex justify-end gap-3">
          <Button
            onClick={() => navigate(-1)}
            variant="outline"
            disabled={submitting}
          >
            Cancel
          </Button>
          <Button
            onClick={handleConfirm}
            disabled={submitting || nothingSelected}
            loading={submitting}
            title={
              nothingSelected
                ? 'Nothing selected — adjust your selections or cancel.'
                : undefined
            }
          >
            Run Remediation
          </Button>
        </div>
      </Card>
    </div>
  )
}
