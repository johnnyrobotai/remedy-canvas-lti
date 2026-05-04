import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useLTI } from '@/contexts/LTIContext'
import { Button, Badge, Spinner } from '@/components/ui'
import { usePageActions } from '@/components/layout/PageActionsContext'
import {
  getPreviews,
  getPreview,
  applyPreviews,
  regenerateAltText,
  startScan,
  type PreviewSummary,
  type RemediationPreview,
  type AltTextResult,
} from '@/api/client'

// NOTE: dangerouslySetInnerHTML is used intentionally in preview panels.
// Content originates from the instructor's own Canvas course and has been
// processed through server-side CanvasHTMLValidator.sanitize() before being
// stored. This page is accessible only to authenticated instructors/admins
// via LTI 1.3 sessions. See PRD.md Section 4 (4-layer pipeline, Layer 4).

export function PreviewPage() {
  const { session } = useLTI()
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()
  const [previews, setPreviews] = useState<PreviewSummary[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [expandedPreview, setExpandedPreview] = useState<RemediationPreview | null>(null)
  const [altTextEdits, setAltTextEdits] = useState<Record<string, string>>({})
  const [showSource, setShowSource] = useState(false)
  const [applying, setApplying] = useState(false)
  const [pageLoading, setPageLoading] = useState(true)
  const [regeneratingIds, setRegeneratingIds] = useState<Set<string>>(new Set())

  const courseId = session?.canvasCourseId

  // Load preview summaries
  useEffect(() => {
    if (!courseId || !jobId) return
    setPageLoading(true)
    getPreviews(courseId, jobId)
      .then((data) => {
        setPreviews(data.previews)
        // Select all by default
        setSelected(new Set(data.previews.map((p) => p.page_id)))
      })
      .catch(() => setPreviews([]))
      .finally(() => setPageLoading(false))
  }, [courseId, jobId])

  // Load full preview when expanding a row
  useEffect(() => {
    if (!courseId || !jobId || !expandedId) {
      setExpandedPreview(null)
      return
    }
    getPreview(courseId, jobId, expandedId)
      .then(setExpandedPreview)
      .catch(() => setExpandedPreview(null))
  }, [courseId, jobId, expandedId])

  const toggleSelect = (pageId: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(pageId)) {
        next.delete(pageId)
      } else {
        next.add(pageId)
      }
      return next
    })
  }

  const selectAll = () => setSelected(new Set(previews.map((p) => p.page_id)))
  const deselectAll = () => setSelected(new Set())

  const handleApply = useCallback(async () => {
    if (!courseId || !jobId || selected.size === 0) return
    setApplying(true)
    try {
      const overrides = Object.keys(altTextEdits).length > 0 ? altTextEdits : undefined
      await applyPreviews(courseId, jobId, Array.from(selected), overrides)
      // Auto re-scan to update dashboard numbers
      try {
        const newScan = await startScan(courseId)
        navigate('/dashboard', { state: { scanJobId: newScan.id, applied: true } })
      } catch {
        navigate('/dashboard', { state: { applied: true } })
      }
    } catch (err) {
      console.error('Failed to apply:', err)
      setApplying(false)
    }
  }, [courseId, jobId, selected, altTextEdits, navigate])

  // TopBar actions: show the Apply button
  usePageActions(
    !pageLoading && previews.length > 0 ? (
      applying ? (
        <div className="flex items-center gap-2 text-sm text-text-subtle">
          <Spinner size="sm" label="Applying changes" />
          Applying changes to Canvas...
        </div>
      ) : (
        <Button
          className="bg-green-600 hover:bg-green-700"
          onClick={handleApply}
          disabled={selected.size === 0}
        >
          Apply {selected.size} Change{selected.size !== 1 ? 's' : ''} to Canvas
        </Button>
      )
    ) : null,
  )

  return (
    <div>
        {pageLoading ? (
          <div className="py-12 text-center text-text-subtle">Loading previews...</div>
        ) : previews.length === 0 ? (
          <div className="py-12 text-center text-text-subtle">
            No changes to preview. All content may already be accessible.
          </div>
        ) : (
          <>
            {/* Select/Deselect controls */}
            <div className="mb-4 flex items-center gap-3">
              <Button variant="outline" size="sm" onClick={selectAll}>
                Select All
              </Button>
              <Button variant="outline" size="sm" onClick={deselectAll}>
                Deselect All
              </Button>
              <span className="ml-auto text-sm text-text-subtle">
                {selected.size} of {previews.length} selected
              </span>
            </div>

            {/* Preview table */}
            <div className="overflow-hidden rounded-lg border border-border bg-surface shadow-sm">
              <table className="w-full text-left text-sm">
                <thead className="border-b border-border bg-surface-muted">
                  <tr>
                    <th className="w-10 px-4 py-3" />
                    <th className="px-4 py-3 font-medium text-text-subtle">Page Title</th>
                    <th className="px-4 py-3 font-medium text-text-subtle">Type</th>
                    <th className="px-4 py-3 font-medium text-text-subtle">Issues Fixed</th>
                    <th className="px-4 py-3 font-medium text-text-subtle">Tags Stripped</th>
                    <th className="px-4 py-3 font-medium text-text-subtle">Alt Texts</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {previews.map((p) => (
                    <tr key={p.page_id}>
                      <td className="px-4 py-3">
                        <input
                          type="checkbox"
                          checked={selected.has(p.page_id)}
                          onChange={() => toggleSelect(p.page_id)}
                          className="h-4 w-4 rounded text-brand-primary"
                          aria-label={`Select ${p.page_title}`}
                        />
                      </td>
                      <td className="px-4 py-3">
                        <button
                          onClick={() =>
                            setExpandedId(expandedId === p.page_id ? null : p.page_id)
                          }
                          className="font-medium text-brand-primary hover:underline"
                        >
                          {p.page_title}
                        </button>
                      </td>
                      <td className="px-4 py-3 text-xs text-text-subtle">
                        {p.content_type.replace('_', ' ')}
                      </td>
                      <td className="px-4 py-3 text-center">
                        <Badge variant="success">{p.issues_fixed_count}</Badge>
                      </td>
                      <td className="px-4 py-3 text-center text-xs text-text-subtle">
                        {p.canvas_tags_stripped}
                      </td>
                      <td className="px-4 py-3 text-center">
                        {p.alt_texts_count > 0 ? (
                          <Badge variant="info">{p.alt_texts_count}</Badge>
                        ) : (
                          <span className="text-xs text-text-subtle">--</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Expanded preview -- content is CanvasHTMLValidator.sanitize() processed */}
            {expandedId && expandedPreview && (
              <div className="mt-4 rounded-lg border border-border bg-surface p-6 shadow-sm">
                <div className="mb-4 flex items-center justify-between">
                  <h3 className="font-medium text-text-muted">
                    {expandedPreview.page_title}
                  </h3>
                  <div className="flex gap-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setShowSource(!showSource)}
                      className={showSource ? 'bg-gray-200' : ''}
                    >
                      {showSource ? 'View Rendered' : 'View Source'}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setExpandedId(null)}
                    >
                      Close
                    </Button>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <p className="mb-2 text-xs font-medium uppercase tracking-wide text-red-500">
                      Before
                    </p>
                    <div className="max-h-80 overflow-auto rounded border border-red-200 bg-red-50 p-3">
                      {showSource ? (
                        <pre className="whitespace-pre-wrap text-xs text-text-muted">
                          {expandedPreview.original_html}
                        </pre>
                      ) : (
                        <div
                          className="prose prose-sm max-w-none"
                          dangerouslySetInnerHTML={{ __html: expandedPreview.original_html }}
                        />
                      )}
                    </div>
                  </div>
                  <div>
                    <p className="mb-2 text-xs font-medium uppercase tracking-wide text-green-600">
                      After
                    </p>
                    <div className="max-h-80 overflow-auto rounded border border-green-200 bg-green-50 p-3">
                      {showSource ? (
                        <pre className="whitespace-pre-wrap text-xs text-text-muted">
                          {expandedPreview.remediated_html}
                        </pre>
                      ) : (
                        <div
                          className="prose prose-sm max-w-none"
                          dangerouslySetInnerHTML={{ __html: expandedPreview.remediated_html }}
                        />
                      )}
                    </div>
                  </div>
                </div>

                {/* Alt Text Results */}
                {expandedPreview.alt_text_results &&
                  expandedPreview.alt_text_results.filter(
                    (r) => r.status === 'generated'
                  ).length > 0 && (
                    <div className="mt-4 rounded-lg border border-blue-200 bg-blue-50 p-4">
                      <h4 className="mb-3 text-sm font-medium text-blue-800">
                        Alt Text Results ({
                          expandedPreview.alt_text_results.filter(
                            (r) => r.status === 'generated'
                          ).length
                        })
                      </h4>
                      <div className="space-y-3">
                        {expandedPreview.alt_text_results
                          .filter((r) => r.status === 'generated')
                          .map((result: AltTextResult) => (
                            <div
                              key={result.image_id}
                              className="flex items-start gap-3 rounded border border-blue-100 bg-surface p-3"
                            >
                              <img
                                src={result.src}
                                alt=""
                                className="h-12 w-12 rounded border border-border object-cover"
                                onError={(e) => {
                                  ;(e.target as HTMLImageElement).style.display = 'none'
                                }}
                              />
                              <div className="flex-1">
                                <div className="flex items-center gap-2">
                                  <input
                                    type="text"
                                    value={
                                      altTextEdits[result.src] ??
                                      result.alt_text ??
                                      ''
                                    }
                                    onChange={(e) =>
                                      setAltTextEdits((prev) => ({
                                        ...prev,
                                        [result.src]: e.target.value,
                                      }))
                                    }
                                    className="w-full rounded border border-border px-2 py-1 text-sm"
                                    aria-label={`Alt text for ${result.src.split('/').pop()}`}
                                  />
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    disabled={regeneratingIds.has(result.image_id)}
                                    loading={regeneratingIds.has(result.image_id)}
                                    onClick={async () => {
                                      if (!courseId) return
                                      setRegeneratingIds((prev) => new Set(prev).add(result.image_id))
                                      try {
                                        const updated = await regenerateAltText(courseId, result.src, result.page_id)
                                        if (updated.alt_text) {
                                          setAltTextEdits((prev) => ({
                                            ...prev,
                                            [result.src]: updated.alt_text!,
                                          }))
                                        }
                                      } catch (err) {
                                        console.error('Failed to regenerate alt text:', err)
                                      } finally {
                                        setRegeneratingIds((prev) => {
                                          const next = new Set(prev)
                                          next.delete(result.image_id)
                                          return next
                                        })
                                      }
                                    }}
                                    title="Regenerate alt text"
                                    className="shrink-0"
                                  >
                                    Regenerate
                                  </Button>
                                </div>
                                <div className="mt-1 flex items-center gap-2 text-xs text-text-subtle">
                                  <Badge
                                    variant={
                                      result.confidence >= 0.8
                                        ? 'success'
                                        : result.confidence >= 0.6
                                          ? 'warning'
                                          : 'error'
                                    }
                                  >
                                    {Math.round(result.confidence * 100)}%
                                  </Badge>
                                  <span>
                                    {result.provider}/{result.model}
                                  </span>
                                  {result.fallback_used && (
                                    <span className="text-amber-600">
                                      (fallback)
                                    </span>
                                  )}
                                </div>
                              </div>
                            </div>
                          ))}
                      </div>
                    </div>
                  )}

                <div className="mt-3">
                  <p className="text-xs text-text-subtle">
                    Issues fixed:{' '}
                    {expandedPreview.issues_fixed.join(', ') || 'None'}
                  </p>
                </div>
              </div>
            )}

            {/* Apply button */}
            <div className="mt-6 flex items-center justify-end gap-4">
              {applying ? (
                <div className="flex items-center gap-2 text-sm text-text-subtle">
                  <Spinner size="sm" label="Applying changes" />
                  Applying changes to Canvas...
                </div>
              ) : (
                <Button
                  className="bg-green-600 hover:bg-green-700"
                  onClick={handleApply}
                  disabled={selected.size === 0}
                >
                  Apply {selected.size} Change{selected.size !== 1 ? 's' : ''} to Canvas
                </Button>
              )}
            </div>
          </>
        )}
    </div>
  )
}
