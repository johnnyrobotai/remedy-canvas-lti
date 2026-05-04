import { useState, useEffect, useCallback, useRef } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useLTI } from '@/contexts/LTIContext'
import { usePageActions } from '@/components/layout/PageActionsContext'
import { Button, Card, ScoreGauge, ProgressBar, SessionExpiredBanner } from '@/components/ui'
import { useSessionExpired } from '@/hooks/useSessionExpired'
import {
  Sparkles,
  FileText,
  ClipboardList,
  MessageSquare,
  HelpCircle,
  FolderOpen,
  BookOpen,
  Megaphone,
  CheckCircle2,
  Download,
  Trash2,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import {
  startScan,
  getScanJob,
  getReport,
  getActivity,
  getIssues,
  getLatestAutoRemedyJob,
  startRemediatedExport,
  getRemediatedExportStatus,
  listOrphanPages,
  deleteOrphanPages,
  type ScanJob,
  type ReportSummary,
  type ActivityEvent,
  type AccessibilityIssue,
  type AutoRemedyJob,
  type OrphanPage,
} from '@/api/client'

function StatsRow({ report }: { report: ReportSummary }) {
  const contentErrors = report.errors
  const contentWarnings = report.warnings
  const contentInfo = report.info
  const platformCount = report.platform_issues ?? 0

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-4">
        <div className="rounded-xl border border-red-200 bg-surface p-5 shadow-sm">
          <p className="text-xs font-medium uppercase tracking-wide text-red-500">Errors</p>
          <p className="mt-1 text-3xl font-bold text-red-600">{contentErrors}</p>
        </div>
        <div className="rounded-xl border border-yellow-200 bg-surface p-5 shadow-sm">
          <p className="text-xs font-medium uppercase tracking-wide text-yellow-600">Warnings</p>
          <p className="mt-1 text-3xl font-bold text-yellow-600">{contentWarnings}</p>
        </div>
        <div className="rounded-xl border border-blue-200 bg-surface p-5 shadow-sm">
          <p className="text-xs font-medium uppercase tracking-wide text-blue-500">Info</p>
          <p className="mt-1 text-3xl font-bold text-blue-600">{contentInfo}</p>
        </div>
      </div>
      {platformCount > 0 && (
        <div className="rounded-lg bg-surface-muted border border-border px-4 py-2 text-sm text-text-muted">
          + {platformCount} Canvas Platform issues (not counted in score — requires Canvas admin)
        </div>
      )}
    </div>
  )
}

const CATEGORY_LABELS: Record<string, string> = {
  images: 'Images',
  headings: 'Headings',
  tables: 'Tables',
  links: 'Links',
  contrast: 'Contrast',
  structure: 'Structure',
  media: 'Media',
  math: 'Math',
}

const SEVERITY_ORDER: Record<string, number> = { error: 0, warning: 1, info: 2 }

export function DashboardPage() {
  const { session } = useLTI()
  const navigate = useNavigate()
  const [report, setReport] = useState<ReportSummary | null>(null)
  const [reportLoading, setReportLoading] = useState(true)
  const [scanJob, setScanJob] = useState<ScanJob | null>(null)
  const [activity, setActivity] = useState<ActivityEvent[]>([])
  const [issues, setIssues] = useState<AccessibilityIssue[]>([])
  // issueFilter and showAllIssues removed — issues now shown as content-type cards
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [exportStatus, setExportStatus] = useState<string>('idle')
  const [exportPatchedPages, setExportPatchedPages] = useState(0)
  const [latestAutoRemedy, setLatestAutoRemedy] = useState<AutoRemedyJob | null>(null)
  // Orphan chapter pages from previous killed AutoRemedy runs (CLU-67
  // textbook chapter splits left behind by mid-phase-4 kills).
  const [orphanPages, setOrphanPages] = useState<OrphanPage[] | null>(null)
  const [orphanCleanupStatus, setOrphanCleanupStatus] = useState<
    'idle' | 'cleaning' | 'done'
  >('idle')
  const [orphanError, setOrphanError] = useState<string | null>(null)

  const sessionExpired = useSessionExpired()

  const courseId = session?.canvasCourseId
  const location = useLocation()
  const locationState = location.state as { scanJobId?: string; applied?: boolean } | null
  const [appliedBanner, setAppliedBanner] = useState(!!locationState?.applied)

  // Pick up a scan job passed from PreviewPage after apply
  useEffect(() => {
    if (!courseId || !locationState?.scanJobId) return
    getScanJob(courseId, locationState.scanJobId)
      .then(setScanJob)
      .catch(() => {})
    // Clear location state to avoid re-triggering
    window.history.replaceState({}, '')
  }, [courseId, locationState?.scanJobId])

  useEffect(() => {
    if (!courseId) return
    setReportLoading(true)
    getReport(courseId)
      .then(setReport)
      .catch(() => setReport(null))
      .finally(() => setReportLoading(false))
    getActivity(courseId)
      .then(setActivity)
      .catch(() => setActivity([]))
    // Latest AutoRemedy state — used to gate the Download Remediated Course
    // button (CLU-63). The button promises remediated content; without a
    // completed run we'd hand back the unremediated original.
    getLatestAutoRemedyJob(courseId)
      .then(setLatestAutoRemedy)
      .catch(() => setLatestAutoRemedy(null))
    // Look for orphan textbook chapter pages from killed AutoRemedy
    // runs. The button only renders when count > 0.
    listOrphanPages(courseId)
      .then((res) => setOrphanPages(res.pages))
      .catch(() => setOrphanPages(null))
  }, [courseId])

  const handleCleanupOrphans = useCallback(async () => {
    if (!courseId) return
    if (!orphanPages || orphanPages.length === 0) return
    const confirmed = window.confirm(
      `Delete ${orphanPages.length} orphan chapter page${
        orphanPages.length === 1 ? '' : 's'
      } left over from a previous run?\n\n` +
        orphanPages.slice(0, 5).map((p) => `• ${p.title}`).join('\n') +
        (orphanPages.length > 5 ? `\n…and ${orphanPages.length - 5} more` : ''),
    )
    if (!confirmed) return
    setOrphanCleanupStatus('cleaning')
    setOrphanError(null)
    try {
      const result = await deleteOrphanPages(courseId)
      setOrphanCleanupStatus('done')
      setOrphanPages([])
      // Refresh activity log so the cleanup shows up
      getActivity(courseId).then(setActivity).catch(() => {})
      window.alert(
        `Deleted ${result.deleted} of ${result.found} orphan chapter pages.`,
      )
    } catch (err) {
      setOrphanCleanupStatus('idle')
      setOrphanError(
        err instanceof Error ? err.message : 'Failed to delete orphan pages',
      )
    }
  }, [courseId, orphanPages])

  // Fetch up to 200 issues for inline display whenever a report is available
  useEffect(() => {
    if (!courseId || !report) return
    getIssues(courseId, { per_page: 200, page: 1 })
      .then((data) => {
        const sorted = [...data.issues].sort(
          (a, b) => (SEVERITY_ORDER[a.severity] ?? 3) - (SEVERITY_ORDER[b.severity] ?? 3),
        )
        setIssues(sorted)
      })
      .catch(() => setIssues([]))
  }, [courseId, report])

  useEffect(() => {
    if (!scanJob || !courseId) return
    if (scanJob.status === 'completed' || scanJob.status === 'failed') return
    // CLU-84: stop polling when the session has expired
    if (sessionExpired) return

    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const updated = await getScanJob(courseId, scanJob.id)
        setScanJob(updated)
        if (updated.status === 'completed') {
          const newReport = await getReport(courseId)
          setReport(newReport)
        }
      } catch {
        // Polling failure — will retry next interval (unless session expired,
        // which the 401 interceptor will handle)
      }
    }, 2000)

    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [scanJob?.id, scanJob?.status, courseId, sessionExpired])

  const handleStartScan = useCallback(async (scanMode: string = 'content_only') => {
    if (!courseId) return
    try {
      const job = await startScan(courseId, scanMode)
      setScanJob(job)
    } catch (err) {
      console.error('Failed to start scan:', err)
    }
  }, [courseId])

  const handleExport = useCallback(async () => {
    if (!courseId) return
    setExportStatus('starting')
    try {
      await startRemediatedExport(courseId)
      const poll = async () => {
        try {
          const status = await getRemediatedExportStatus(courseId)
          if (status.status === 'complete') {
            setExportStatus('ready')
            setExportPatchedPages(status.patched_pages || 0)
            window.open(`/api/courses/${courseId}/exports/remediated/download`, '_blank')
          } else if (status.status === 'failed') {
            setExportStatus('failed')
          } else {
            setTimeout(poll, 2000)
          }
        } catch {
          setExportStatus('failed')
        }
      }
      poll()
      setExportStatus('exporting')
    } catch {
      setExportStatus('failed')
    }
  }, [courseId])

  const isScanning =
    scanJob?.status === 'pending' || scanJob?.status === 'running'
  const scanFailed = scanJob?.status === 'failed'

  // CLU-85: warn about pending ephemeral selections before Fix My Course.
  // Reads localStorage directly because the Dashboard is outside the
  // SelectionProvider.
  const pendingSelectionsCount = (() => {
    if (!report?.id) return 0
    try {
      const raw = localStorage.getItem(`clu-selections-${report.id}`)
      if (!raw) return 0
      const parsed = JSON.parse(raw)
      let total = 0
      const unchecked = parsed?.unchecked_items ?? {}
      for (const list of Object.values(unchecked)) {
        if (Array.isArray(list)) total += list.length
      }
      return total
    } catch {
      return 0
    }
  })()

  usePageActions(
    isScanning ? (
      <ProgressBar
        progress={scanJob?.progress ?? 0}
        label="Scanning..."
        sublabel={`${scanJob?.pages_scanned ?? 0}/${scanJob?.pages_total ?? '?'}`}
        className="w-48"
      />
    ) : (
      <div className="flex items-center gap-2">
        <Button
          onClick={() => handleStartScan('content_only')}
          variant="outline"
          size="sm"
          disabled={reportLoading}
        >
          Quick Scan
        </Button>
        <Button
          onClick={() => handleStartScan('full')}
          disabled={reportLoading}
        >
          Full Scan
        </Button>
      </div>
    ),
  )

  return (
    <div className="space-y-6">
      {/* CLU-84: Session expired banner */}
      {sessionExpired && <SessionExpiredBanner />}

      {/* Applied banner */}
      {appliedBanner && isScanning && (
        <div className="rounded-lg border border-green-200 bg-green-50 px-4 py-3">
          <p className="text-sm font-medium text-green-800">
            Remediation applied! Re-scanning to update results...
          </p>
        </div>
      )}
      {appliedBanner && !isScanning && (
        <div className="rounded-lg border border-green-200 bg-green-50 px-4 py-3 flex items-center justify-between">
          <p className="text-sm font-medium text-green-800">
            Changes applied and re-scan complete.
          </p>
          <button onClick={() => setAppliedBanner(false)} className="text-green-600 hover:text-green-800 text-sm">
            Dismiss
          </button>
        </div>
      )}

      {/* Hero score card */}
      <Card className="flex items-center justify-between">
        <div className="flex items-center gap-6">
          {report ? (
            <ScoreGauge score={report.score} />
          ) : (
            <div className="flex h-32 w-32 items-center justify-center">
              <span className="text-5xl font-bold text-gray-300" aria-label="Score not yet calculated">--</span>
            </div>
          )}
          <div>
            <h2 className="text-sm font-medium uppercase tracking-wide text-text-subtle">
              Accessibility Score
            </h2>
            {report ? (
              <>
                <p className="mt-1 text-sm text-text-muted">
                  {report.pages_analyzed} pages analyzed
                </p>
                <p className="text-xs text-text-subtle">
                  Last scanned {new Date(report.analyzed_at).toLocaleString()}
                </p>
              </>
            ) : scanFailed ? (
              // CLU-6: when the scan failed and there's no prior report,
              // show the failure prominently instead of the pre-scan
              // "Run a scan..." placeholder. Users had no idea the
              // scan had even tried before this fix.
              <div className="mt-1 space-y-2">
                <p className="text-sm font-medium text-red-700">
                  Scan failed
                </p>
                <p className="max-w-md text-xs text-red-600">
                  {scanJob?.error ?? 'Unknown error. Check the network tab or contact support.'}
                </p>
                <Button
                  onClick={() => handleStartScan('content_only')}
                  variant="outline"
                  size="sm"
                >
                  Try Again
                </Button>
              </div>
            ) : (
              <p className="mt-1 text-sm text-text-subtle">
                Run a scan to see your course accessibility score.
              </p>
            )}
          </div>
        </div>

        <div className="flex flex-col items-end gap-2">
          {report && (
            <Link
              to="/scan"
              className="text-sm font-medium text-brand-primary hover:underline"
            >
              View Details &rarr;
            </Link>
          )}
          {isScanning && (
            <div className="flex flex-col items-end gap-1">
              <div className="flex items-center gap-3">
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-brand-primary border-t-transparent" />
                <span className="text-sm text-text-subtle">
                  Scanning {scanJob?.pages_scanned ?? 0}/{scanJob?.pages_total || '...'}
                </span>
              </div>
              {scanJob?.phase === 'content' && (
                <p className="text-xs text-text-subtle">Phase 1: Scanning content...</p>
              )}
              {scanJob?.phase === 'rendered' && (scanJob?.queue_position ?? 0) > 0 && (
                <div className="rounded-lg bg-amber-50 px-3 py-1.5 text-center text-xs">
                  <span className="font-semibold text-amber-800">Queued</span>
                  <span className="text-amber-700"> — #{scanJob.queue_position} in line</span>
                </div>
              )}
              {scanJob?.phase === 'rendered' && (scanJob?.queue_position ?? 0) === 0 && (
                <p className="text-xs text-text-subtle">
                  Phase 2: Rendered scan — {scanJob?.current_page || '...'}
                </p>
              )}
              {scanJob?.phase === 'merging' && (
                <p className="text-xs text-text-subtle">Merging results...</p>
              )}
            </div>
          )}
          {scanFailed && report && (
            // CLU-6: when there's a prior report, the failure indicator
            // sits in the corner of the score card so the user can still
            // see their last successful score. Made larger and added
            // a Retry button so it's actually actionable.
            <div className="flex flex-col items-end gap-1 rounded-lg border border-red-200 bg-red-50 px-3 py-2">
              <p className="text-sm font-medium text-red-700">
                Latest scan failed
              </p>
              <p className="max-w-xs text-right text-xs text-red-600">
                {scanJob?.error ?? 'Unknown error'}
              </p>
              <Button
                onClick={() => handleStartScan('content_only')}
                variant="outline"
                size="sm"
                className="mt-1"
              >
                Retry
              </Button>
            </div>
          )}
        </div>
      </Card>

      {/* Stats row — course content only (excludes Canvas platform issues) */}
      {report ? (
        <StatsRow report={report} />
      ) : (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          {Object.values(CATEGORY_LABELS).map((label) => (
            <div key={label} className="rounded-xl border border-border bg-surface p-5 shadow-sm">
              <p className="text-xs font-medium uppercase tracking-wide text-text-subtle">{label}</p>
              <p className="mt-1 text-2xl font-bold text-gray-200">--</p>
            </div>
          ))}
        </div>
      )}

      {/* Fix My Course CTA — only when there are issues */}
      {issues.length > 0 && (
        <div>
          {pendingSelectionsCount > 0 && (
            <div
              className="mb-4 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm"
              role="status"
            >
              <p className="font-semibold text-amber-900">
                You have {pendingSelectionsCount} pending selection
                {pendingSelectionsCount === 1 ? '' : 's'}.
              </p>
              <p className="mt-1 text-amber-800">
                Clicking &quot;Fix My Course&quot; below will run with ALL items — it
                ignores draft selections. To run with your selections, open a
                content-type review view and use its &quot;Run Remediation&quot; button.
              </p>
            </div>
          )}
          <button
            type="button"
            onClick={() => navigate('/autoremedy')}
            className="flex w-full items-center justify-center gap-3 rounded-xl bg-gray-900 px-6 py-4 text-white shadow-md transition hover:bg-gray-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-gray-900 focus-visible:ring-offset-2"
            aria-label="Fix My Course — Make it Accessible: navigate to AutoRemedy"
          >
            <Sparkles className="h-5 w-5 shrink-0" aria-hidden="true" />
            <span className="text-base font-semibold">Fix My Course — Make it Accessible</span>
          </button>
        </div>
      )}

      {/* Download Remediated Course — gated on an AutoRemedy run that
          has actually applied fixes. Without remediation the export is
          just the unremediated original; the label "Remediated Course"
          would be misleading (CLU-63).

          CLU-79: we also allow downloads when the last run ended in
          `failed` status but did real work — specifically cancellation
          of a large conversion phase after HTML fixes + some docs were
          already written to Canvas. Those fixes are persistent in
          Canvas even though the job itself didn't reach `complete`. */}
      {report && (() => {
        const fixesApplied =
          (latestAutoRemedy?.issues_fixed ?? 0) > 0 ||
          (latestAutoRemedy?.docs_converted ?? 0) > 0
        const isPartial =
          latestAutoRemedy?.status === 'failed' && fixesApplied
        const hasCompletedRemediation =
          latestAutoRemedy?.status === 'completed' || isPartial
        const disabled =
          !hasCompletedRemediation ||
          exportStatus === 'exporting' ||
          exportStatus === 'starting'
        return (
          <div className="flex flex-col items-center gap-2">
            <Button
              onClick={handleExport}
              disabled={disabled}
              loading={exportStatus === 'exporting' || exportStatus === 'starting'}
              variant="secondary"
              icon={Download}
              title={
                !hasCompletedRemediation
                  ? 'Run AutoRemedy first to generate the remediated content'
                  : undefined
              }
            >
              {exportStatus === 'idle' && 'Download Remediated Course'}
              {exportStatus === 'starting' && 'Starting export...'}
              {exportStatus === 'exporting' && 'Preparing export...'}
              {exportStatus === 'ready' && `Download Ready (${exportPatchedPages} pages patched)`}
              {exportStatus === 'failed' && 'Export failed — try again'}
            </Button>
            {!hasCompletedRemediation && (
              <p className="text-xs text-text-subtle">
                Run AutoRemedy first to generate the remediated content.
              </p>
            )}
            {isPartial && (
              <p className="text-xs text-amber-700">
                Partial remediation — last run was cancelled or failed.
                Includes {latestAutoRemedy?.issues_fixed ?? 0} HTML fixes
                {(latestAutoRemedy?.docs_converted ?? 0) > 0 &&
                  ` and ${latestAutoRemedy?.docs_converted} converted documents`}
                .
              </p>
            )}
          </div>
        )
      })()}

      {/* Orphan Chapter Cleanup — only renders when there are orphans
          left over from a previous killed run. Calls
          /api/courses/{id}/cleanup/orphan-pages to delete CLU-67
          textbook chapter pages that the killed run didn't archive. */}
      {orphanPages && orphanPages.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
          <div className="flex items-start gap-3">
            <Trash2 className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" aria-hidden="true" />
            <div className="flex-1">
              <p className="text-sm font-medium text-amber-900">
                {orphanPages.length} orphan chapter page
                {orphanPages.length === 1 ? '' : 's'} from a previous run
              </p>
              <p className="mt-0.5 text-xs text-amber-800">
                These textbook chapter pages were created when AutoRemedy was
                interrupted mid-conversion. They contribute to PopeTech alert
                counts but students never see them (unpublished). Safe to delete.
              </p>
              {orphanError && (
                <p className="mt-1 text-xs text-red-700">{orphanError}</p>
              )}
            </div>
            <Button
              onClick={handleCleanupOrphans}
              disabled={orphanCleanupStatus === 'cleaning'}
              loading={orphanCleanupStatus === 'cleaning'}
              variant="secondary"
              icon={Trash2}
            >
              {orphanCleanupStatus === 'cleaning' ? 'Deleting…' : 'Delete'}
            </Button>
          </div>
        </div>
      )}

      {/* Content Overview — issues grouped by content type.
          Reads server-computed counts from report.issues_by_content_type so the
          cards reflect the FULL issue list, not the truncated 200-issue slice
          we use for inline display (CLU-53). */}
      {report && (() => {
        const CONTENT_CARDS: { key: string; label: string; icon: LucideIcon; apiValues: string[] }[] = [
          { key: 'pages', label: 'Pages', icon: FileText, apiValues: ['wiki_page'] },
          { key: 'assignments', label: 'Assignments', icon: ClipboardList, apiValues: ['assignment'] },
          { key: 'discussions', label: 'Discussions', icon: MessageSquare, apiValues: ['discussion'] },
          { key: 'quizzes', label: 'Quizzes', icon: HelpCircle, apiValues: ['quiz', 'quiz_question', 'new_quiz', 'new_quiz_item'] },
          { key: 'announcements', label: 'Announcements', icon: Megaphone, apiValues: ['announcement'] },
          { key: 'syllabus', label: 'Syllabus', icon: BookOpen, apiValues: ['syllabus'] },
          { key: 'files', label: 'Files', icon: FolderOpen, apiValues: ['file'] },
        ]

        const breakdown = report.issues_by_content_type || {}

        return (
          <section aria-labelledby="content-heading">
            <h2
              id="content-heading"
              className="mb-3 border-t border-border pt-5 text-sm font-semibold uppercase tracking-wide text-text-muted"
            >
              Content Overview
            </h2>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
              {CONTENT_CARDS.map(({ key, label, icon: Icon, apiValues }) => {
                let errors = 0
                let warnings = 0
                for (const ct of apiValues) {
                  const bucket = breakdown[ct]
                  if (bucket) {
                    errors += bucket.errors
                    warnings += bucket.warnings
                  }
                }
                // Canvas platform issues aren't broken out per-type by the
                // backend yet — leave them out of the card counts. They show
                // on the platform-issues banner above.
                const platformIssues: AccessibilityIssue[] = []
                const clean = errors === 0 && warnings === 0 && platformIssues.length === 0

                return (
                  <Link
                    key={key}
                    to={`/content/${key}`}
                    className="flex flex-col rounded-xl border border-border bg-surface p-4 shadow-sm transition hover:border-brand-primary hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
                  >
                    <div className="mb-3 flex items-center gap-2">
                      <Icon className="h-5 w-5 text-text-subtle" aria-hidden="true" />
                      <span className="text-sm font-semibold text-text">{label}</span>
                    </div>
                    {clean ? (
                      <div className="flex items-center gap-1.5">
                        <CheckCircle2 className="h-4 w-4 text-green-500" aria-hidden="true" />
                        <span className="text-xs text-green-600">No issues</span>
                      </div>
                    ) : (
                      <div className="space-y-1">
                        {errors > 0 && (
                          <div className="flex items-center gap-1.5">
                            <span className="h-2 w-2 rounded-full bg-red-500" aria-hidden="true" />
                            <span className="text-xs text-red-600">{errors} error{errors !== 1 ? 's' : ''}</span>
                          </div>
                        )}
                        {warnings > 0 && (
                          <div className="flex items-center gap-1.5">
                            <span className="h-2 w-2 rounded-full bg-yellow-500" aria-hidden="true" />
                            <span className="text-xs text-yellow-600">{warnings} warning{warnings !== 1 ? 's' : ''}</span>
                          </div>
                        )}
                        {platformIssues.length > 0 && (
                          <div className="flex items-center gap-1.5">
                            <span className="rounded-full bg-surface-muted px-1.5 py-0.5 text-[10px] text-text-subtle border border-border">
                              Canvas Platform
                            </span>
                            <span className="text-xs text-text-subtle">{platformIssues.length}</span>
                          </div>
                        )}
                      </div>
                    )}
                    <span className="mt-auto pt-2 text-xs font-medium text-brand-primary">
                      View &rarr;
                    </span>
                  </Link>
                )
              })}
            </div>
          </section>
        )
      })()}

      {/* Activity Feed */}
      {activity.length > 0 && (
        <Card title="Recent Activity">
          <div className="divide-y divide-gray-100">
            {activity.map((event, i) => {
              const icon = event.type === 'scan' ? '🔍' : event.type === 'remediation' ? '🔧' : event.type === 'autoremedy' ? '✨' : '🤖'
              const timeAgo = formatTimeAgo(event.timestamp)
              return (
                <div key={i} className="flex items-start justify-between py-3 first:pt-0 last:pb-0">
                  <div className="flex items-start gap-3">
                    <span className="text-lg">{icon}</span>
                    <div>
                      <p className="text-sm font-medium text-text">{event.title}</p>
                      <p className="text-xs text-text-subtle">{event.description}</p>
                    </div>
                  </div>
                  <span className="whitespace-nowrap text-xs text-text-subtle">{timeAgo}</span>
                </div>
              )
            })}
          </div>
        </Card>
      )}
    </div>
  )
}

function formatTimeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}
