import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useLTI } from '@/contexts/LTIContext'
import { usePageActions } from '@/components/layout/PageActionsContext'
import { Badge, Button, FilterChip } from '@/components/ui'
import {
  getReport,
  getIssues,
  type ReportSummary,
  type AccessibilityIssue,
  type IssuesResponse,
} from '@/api/client'

const CATEGORIES = [
  'images', 'headings', 'tables', 'links', 'contrast', 'structure', 'media', 'math',
] as const

const SEVERITY_VARIANT: Record<string, 'error' | 'warning' | 'info'> = {
  error: 'error',
  warning: 'warning',
  info: 'info',
}

export function ScanPage() {
  const { session } = useLTI()
  const [report, setReport] = useState<ReportSummary | null>(null)
  const [issues, setIssues] = useState<AccessibilityIssue[]>([])
  const [total, setTotal] = useState(0)
  const [currentPage, setCurrentPage] = useState(1)
  const [totalPages, setTotalPages] = useState(1)
  const [categoryFilter, setCategoryFilter] = useState<string | undefined>()
  const [severityFilter, setSeverityFilter] = useState<string | undefined>()
  const [viewMode, setViewMode] = useState<'flat' | 'grouped'>('flat')
  const [pageLoading, setPageLoading] = useState(true)

  const courseId = session?.canvasCourseId

  useEffect(() => {
    if (!courseId) return
    getReport(courseId)
      .then(setReport)
      .catch(() => setReport(null))
  }, [courseId])

  useEffect(() => {
    if (!courseId) return
    setPageLoading(true)
    getIssues(courseId, {
      category: categoryFilter,
      severity: severityFilter,
      page: currentPage,
      per_page: 50,
    })
      .then((data: IssuesResponse) => {
        setIssues(data.issues)
        setTotal(data.total)
        setTotalPages(data.total_pages)
      })
      .catch(() => setIssues([]))
      .finally(() => setPageLoading(false))
  }, [courseId, categoryFilter, severityFilter, currentPage])

  usePageActions(
    <div className="flex items-center gap-2">
      <Link to="/remediate">
        <Button size="sm">Fix These</Button>
      </Link>
    </div>,
  )

  const groupedIssues: Record<string, AccessibilityIssue[]> = {}
  for (const issue of issues) {
    const key = issue.page_id
    if (!groupedIssues[key]) groupedIssues[key] = []
    groupedIssues[key].push(issue)
  }

  return (
    <div className="space-y-6">
      {/* Summary */}
      {report && (
        <p className="text-sm text-text-subtle">
          {report.total_issues} issues across {report.pages_analyzed} pages
        </p>
      )}

      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-3">
        <FilterChip
          label="All"
          active={!categoryFilter}
          onClick={() => { setCategoryFilter(undefined); setCurrentPage(1) }}
        />
        {CATEGORIES.map((cat) => (
          <FilterChip
            key={cat}
            label={cat.charAt(0).toUpperCase() + cat.slice(1)}
            active={categoryFilter === cat}
            onClick={() => { setCategoryFilter(cat === categoryFilter ? undefined : cat); setCurrentPage(1) }}
          />
        ))}

        <div className="mx-2 h-6 w-px bg-gray-300" />

        <select
          value={severityFilter ?? ''}
          onChange={(e) => { setSeverityFilter(e.target.value || undefined); setCurrentPage(1) }}
          className="rounded-lg border border-border px-3 py-1 text-xs"
          aria-label="Filter by severity"
        >
          <option value="">All Severities</option>
          <option value="error">Errors</option>
          <option value="warning">Warnings</option>
          <option value="info">Info</option>
        </select>

        <div className="ml-auto flex gap-1 rounded-lg border border-border p-0.5">
          <button
            onClick={() => setViewMode('flat')}
            className={`rounded px-3 py-1 text-xs font-medium ${viewMode === 'flat' ? 'bg-gray-200' : ''}`}
          >
            All Issues
          </button>
          <button
            onClick={() => setViewMode('grouped')}
            className={`rounded px-3 py-1 text-xs font-medium ${viewMode === 'grouped' ? 'bg-gray-200' : ''}`}
          >
            By Page
          </button>
        </div>
      </div>

      {/* Issues */}
      {pageLoading ? (
        <div className="py-12 text-center text-text-subtle">Loading issues...</div>
      ) : issues.length === 0 ? (
        <div className="py-12 text-center text-text-subtle">
          No issues found{categoryFilter ? ` in ${categoryFilter}` : ''}.
        </div>
      ) : viewMode === 'flat' ? (
        <div className="overflow-hidden rounded-xl border border-border bg-surface shadow-sm">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-border bg-surface-muted">
              <tr>
                <th className="px-4 py-3 font-medium text-text-subtle">Rule</th>
                <th className="px-4 py-3 font-medium text-text-subtle">Severity</th>
                <th className="px-4 py-3 font-medium text-text-subtle">Message</th>
                <th className="px-4 py-3 font-medium text-text-subtle">WCAG</th>
                <th className="px-4 py-3 font-medium text-text-subtle">Fixable</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {issues.map((issue) => (
                <tr key={issue.id} className="hover:bg-surface-muted">
                  <td className="px-4 py-3 font-mono text-xs">{issue.rule_id}</td>
                  <td className="px-4 py-3">
                    <Badge variant={SEVERITY_VARIANT[issue.severity] ?? 'neutral'}>
                      {issue.severity}
                    </Badge>
                  </td>
                  <td className="max-w-md truncate px-4 py-3 text-text-muted">{issue.message}</td>
                  <td className="px-4 py-3 text-xs">
                    {issue.wcag_criterion ? (
                      <span className="text-brand-primary">{issue.wcag_criterion}</span>
                    ) : (
                      <span className="text-text-subtle">&mdash;</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-center">
                    {issue.can_auto_fix ? (
                      <span className="text-green-500">&#10003;</span>
                    ) : (
                      <span className="text-gray-300">&mdash;</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="space-y-4">
          {Object.entries(groupedIssues).map(([pageId, pageIssues]) => (
            <details key={pageId} className="rounded-xl border border-border bg-surface shadow-sm" open>
              <summary className="cursor-pointer px-4 py-3 font-medium text-text-muted">
                Page: {pageId}
                <span className="ml-2 text-sm text-text-subtle">
                  ({pageIssues.length} issue{pageIssues.length !== 1 ? 's' : ''})
                </span>
              </summary>
              <div className="border-t border-border-muted px-4 py-2">
                {pageIssues.map((issue) => (
                  <div key={issue.id} className="flex items-start gap-3 border-b border-gray-50 py-2 last:border-0">
                    <Badge variant={SEVERITY_VARIANT[issue.severity] ?? 'neutral'}>
                      {issue.severity}
                    </Badge>
                    <div>
                      <p className="text-sm text-text-muted">{issue.message}</p>
                      <p className="text-xs text-text-subtle">
                        {issue.rule_id} &middot; WCAG {issue.wcag_criterion}
                        {issue.can_auto_fix && ' · Auto-fixable'}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            </details>
          ))}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <p className="text-sm text-text-subtle">
            Showing {(currentPage - 1) * 50 + 1}–{Math.min(currentPage * 50, total)} of {total} issues
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
              disabled={currentPage === 1}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
              disabled={currentPage === totalPages}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
