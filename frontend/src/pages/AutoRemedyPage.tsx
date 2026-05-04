import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useLTI } from '@/contexts/LTIContext'
import { Button, Card, ProgressBar, SessionExpiredBanner } from '@/components/ui'
import { usePageActions } from '@/components/layout/PageActionsContext'
import { useSessionExpired } from '@/hooks/useSessionExpired'
import { Download, ShieldCheck } from 'lucide-react'
import {
  cancelAutoRemedy,
  getAutoRemedyJob,
  getLatestAutoRemedyJob,
  getAutoRemedySummary,
  getRemediatedExportStatus,
  startAutoRemedy,
  startRemediatedExport,
  type AutoRemedyJob,
  type AutoRemedySummary,
} from '@/api/client'

// Human-readable phase labels for non-technical instructors
const PHASE_LABELS: Record<string, string> = {
  scanning: 'Scanning your course for accessibility issues...',
  remediating_html: 'Fixing accessibility issues...',
  auditing_files: 'Auditing course files...',
  converting_documents: 'Converting documents to accessible pages...',
  complete: 'Finishing up...',
}

function formatRelativeDate(dateStr: string): string {
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24))
  if (diffDays === 0) return 'today'
  if (diffDays === 1) return 'yesterday'
  if (diffDays < 7) return `${diffDays} days ago`
  if (diffDays < 30) return `${Math.floor(diffDays / 7)} week${Math.floor(diffDays / 7) > 1 ? 's' : ''} ago`
  return `${Math.floor(diffDays / 30)} month${Math.floor(diffDays / 30) > 1 ? 's' : ''} ago`
}

export function AutoRemedyPage() {
  const { session } = useLTI()
  const navigate = useNavigate()
  const sessionExpired = useSessionExpired()
  const [job, setJob] = useState<AutoRemedyJob | null>(null)
  const [lastJob, setLastJob] = useState<AutoRemedyJob | null>(null)
  const [summary, setSummary] = useState<AutoRemedySummary | null>(null)
  const [startError, setStartError] = useState<string | null>(null)
  const [exportPhase, setExportPhase] = useState<'idle' | 'starting' | 'exporting' | 'ready' | 'failed'>('idle')
  const [exportPatchedPages, setExportPatchedPages] = useState(0)
  const [exportError, setExportError] = useState<string | null>(null)
  const [exportSubmitting, setExportSubmitting] = useState(false)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const courseId = session?.canvasCourseId

  // Recover latest job on mount
  useEffect(() => {
    if (!courseId) return
    getLatestAutoRemedyJob(courseId)
      .then((latestJob) => {
        if (latestJob) {
          if (latestJob.status === 'running' || latestJob.status === 'pending') {
            // Active job — start polling
            setJob(latestJob)
          } else if (latestJob.status === 'failed') {
            // User cancellations are historical from the user's perspective —
            // they already saw "cancelled" when they clicked Cancel. Drop the
            // failure card on next visit so they land on the idle CTA instead
            // of being stuck on a "Try Again" screen. Unexpected failures
            // (orphaned by restart, RuntimeError, etc.) still surface as the
            // primary state so the user can see the error (CLU-57 intent).
            const wasCancelled = (latestJob.error || '').toLowerCase().includes('cancelled')
            if (wasCancelled) {
              setLastJob(latestJob)
            } else {
              setJob(latestJob)
              setLastJob(latestJob)
            }
          } else {
            // Completed — store as lastJob only, keep idle state
            setLastJob(latestJob)
            if (latestJob.status === 'completed') {
              getAutoRemedySummary(courseId, latestJob.id)
                .then(setSummary)
                .catch(() => {})
            }
          }
        }
      })
      .finally(() => setLoading(false))
  }, [courseId])

  // Poll job status every 2 seconds while running
  useEffect(() => {
    if (!job || !courseId) return
    if (job.status === 'completed' || job.status === 'failed') {
      if (job.status === 'completed') {
        setLastJob(job)
        getAutoRemedySummary(courseId, job.id)
          .then(setSummary)
          .catch(() => {})
      }
      return
    }
    // CLU-84: stop polling when the session has expired
    if (sessionExpired) return

    pollRef.current = setInterval(async () => {
      try {
        const updated = await getAutoRemedyJob(courseId, job.id)
        setJob(updated)
      } catch {
        // Will retry on next tick (unless session expired,
        // which the 401 interceptor will handle)
      }
    }, 2000)

    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [job?.id, job?.status, courseId, sessionExpired])

  const handleStart = useCallback(async () => {
    if (!courseId || submitting) return
    setSubmitting(true)
    setStartError(null)
    setExportPhase('idle')
    setExportPatchedPages(0)
    setExportError(null)
    try {
      const newJob = await startAutoRemedy(courseId)
      setJob(newJob)
    } catch (err) {
      setStartError(err instanceof Error ? err.message : 'Failed to start AutoRemedy')
    } finally {
      setSubmitting(false)
    }
  }, [courseId, submitting])

  const handleReset = useCallback(() => {
    setJob(null)
    setSummary(null)
    setStartError(null)
    setExportPhase('idle')
    setExportPatchedPages(0)
    setExportError(null)
    setCancelling(false)
  }, [])

  const handleCancel = useCallback(async () => {
    if (!courseId || !job || cancelling) return
    setCancelling(true)
    try {
      const updated = await cancelAutoRemedy(courseId, job.id)
      // Optimistic: keep the job in state and let polling pick up the
      // failed status. The orchestrator typically reacts within 2-3 seconds.
      setJob(updated)
    } catch (err) {
      setCancelling(false)
      setStartError(err instanceof Error ? err.message : 'Failed to cancel AutoRemedy')
    }
  }, [courseId, job, cancelling])

  const handleExport = useCallback(async () => {
    if (!courseId || exportSubmitting) return
    setExportSubmitting(true)
    setExportError(null)
    setExportPatchedPages(0)
    setExportPhase('starting')

    try {
      await startRemediatedExport(courseId)
      setExportPhase('exporting')
      const poll = async () => {
        try {
          const status = await getRemediatedExportStatus(courseId)
          if (status.status === 'complete') {
            setExportPatchedPages(status.patched_pages || 0)
            setExportPhase('ready')
            window.open(`/api/courses/${courseId}/exports/remediated/download`, '_blank')
          } else if (status.status === 'failed') {
            setExportError(status.error || 'Remediated export failed.')
            setExportPhase('failed')
          } else {
            setTimeout(poll, 2000)
          }
        } catch (err) {
          setExportError(err instanceof Error ? err.message : 'Failed to refresh export status')
          setExportPhase('failed')
        }
      }
      poll()
    } catch (err) {
      setExportError(err instanceof Error ? err.message : 'Failed to create remediated export')
      setExportPhase('failed')
    } finally {
      setExportSubmitting(false)
    }
  }, [courseId, exportSubmitting])

  const isRunning = job?.status === 'pending' || job?.status === 'running'
  const isCompleted = job?.status === 'completed'
  const isFailed = job?.status === 'failed'

  // Keep TopBar clean while running — no extra actions needed
  usePageActions(null)

  // Derived stats for completed state
  const issuesFixed = summary?.html.issues_fixed ?? job?.issues_fixed ?? 0
  const pagesUpdated = summary?.html.pages_remediated ?? job?.html_pages_remediated ?? 0
  const docsConverted = summary?.docs_converted ?? job?.docs_converted ?? 0
  const exportStatusLabel = (() => {
    switch (exportPhase) {
      case 'starting':
        return 'Starting remediated export...'
      case 'exporting':
        return 'Preparing your remediated course export...'
      case 'ready':
        return `Download starting (${exportPatchedPages} pages patched)...`
      case 'failed':
        return 'Remediated export failed.'
      default:
        return null
    }
  })()

  // Sub-label for running state — phase-aware
  const runningSubLabel = (() => {
    if (!isRunning || !job) return undefined
    switch (job.phase) {
      case 'scanning':
        return job.html_pages_total > 0 ? `Found ${job.html_pages_total} pages` : undefined
      case 'remediating_html':
        return job.issues_found > 0 ? `${job.issues_found} issues to fix` : 'Analyzing pages...'
      case 'auditing_files':
        return job.files_total > 0 ? `${job.files_audited} of ${job.files_total} files checked` : undefined
      case 'converting_documents':
        return job.docs_converted > 0 ? `${job.docs_converted} documents converted` : undefined
      default:
        return undefined
    }
  })()

  return (
    <div className="space-y-6">
      {/* CLU-84: Session expired banner */}
      {sessionExpired && <SessionExpiredBanner />}

      {/* Loading state */}
      {loading && (
        <Card className="py-12 text-center">
          <div
            className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-brand-primary border-t-transparent"
            role="status"
            aria-label="Loading"
          />
          <p className="mt-3 text-sm text-text-subtle">Checking for existing jobs...</p>
        </Card>
      )}

      {/* ── State 1: Idle ── */}
      {!loading && !job && (
        <Card>
          <h2 className="text-2xl font-bold text-text">Fix My Course</h2>
          <p className="mt-2 text-sm text-text-muted">
            AutoRemedy will scan your course and automatically fix accessibility issues to meet
            WCAG 2.2 AA standards. Runtime depends on course size and the number of images
            to caption — small courses finish in a few minutes; large courses can take 15–30.
          </p>

          {/* What gets fixed */}
          <ul className="mt-5 space-y-2" aria-label="What gets fixed">
            {[
              'Fix image alt text (so screen readers can describe images)',
              'Fix heading structure',
              'Fix table accessibility',
              'Fix link text',
              'Convert Word and PowerPoint files to accessible web pages',
            ].map((item) => (
              <li key={item} className="flex items-start gap-2 text-sm text-text-muted">
                <span className="mt-0.5 text-green-600 font-bold" aria-hidden="true">
                  ✓
                </span>
                {item}
              </li>
            ))}
          </ul>

          {/* Last run info — shown when a completed OR partially-remediated
              (cancelled/failed with applied fixes) run exists. CLU-79. */}
          {lastJob && (lastJob.status === 'completed' || (
            lastJob.status === 'failed' && (
              (lastJob.issues_fixed ?? 0) > 0 ||
              (lastJob.docs_converted ?? 0) > 0
            )
          )) && (
            <div className="mt-5">
              <p className="text-sm text-text-subtle text-center">
                Last run: {formatRelativeDate(lastJob.created_at)} — {lastJob.issues_fixed ?? 0} issues fixed
                {(lastJob.docs_converted ?? 0) > 0 && `, ${lastJob.docs_converted} documents converted`}
                {lastJob.status === 'failed' && ' (partial — run was cancelled or failed)'}
              </p>
              <div className="mt-4">
                <Button
                  variant="outline"
                  className="w-full"
                  onClick={handleExport}
                  disabled={exportSubmitting || exportPhase === 'starting' || exportPhase === 'exporting'}
                  loading={exportSubmitting || exportPhase === 'starting' || exportPhase === 'exporting'}
                  icon={Download}
                  aria-label="Download a fresh remediated Canvas export"
                >
                  Download Remediated Course
                </Button>
                <p className="mt-2 text-xs text-text-subtle text-center">
                  Creates a fresh Canvas export from the current remediated course state.
                </p>
                {exportStatusLabel && (
                  <p className="mt-2 text-xs text-text-subtle text-center">{exportStatusLabel}</p>
                )}
                {exportError && (
                  <p className="mt-2 text-sm text-red-600 text-center" role="alert">
                    {exportError}
                  </p>
                )}
              </div>
            </div>
          )}

          <div className="mt-8">
            <Button
              size="lg"
              className="w-full"
              onClick={handleStart}
              disabled={submitting}
              loading={submitting}
              aria-label="Start AutoRemedy to fix accessibility issues in this course"
            >
              Start AutoRemedy
            </Button>
          </div>

          {startError && (
            <p className="mt-3 text-sm text-red-600" role="alert">
              {startError}
            </p>
          )}
        </Card>
      )}

      {/* ── State 2: Running ── */}
      {isRunning && job && (
        <Card>
          <div aria-live="polite" aria-atomic="false">
            <div className="mb-4 flex items-start justify-between gap-4">
              <h2 className="text-lg font-semibold text-text">
                {(cancelling || job.cancel_requested)
                  ? 'Cancelling AutoRemedy...'
                  : (PHASE_LABELS[job.phase] ?? 'Working on your course...')}
              </h2>
              <Button
                variant="outline"
                size="sm"
                onClick={handleCancel}
                disabled={cancelling || job.cancel_requested}
                loading={cancelling || job.cancel_requested}
                aria-label="Cancel AutoRemedy job"
              >
                {(cancelling || job.cancel_requested) ? 'Cancelling...' : 'Cancel'}
              </Button>
            </div>

            <ProgressBar
              progress={job.progress ?? 0}
              label={PHASE_LABELS[job.phase] ?? 'Working...'}
              sublabel={runningSubLabel}
              className="mt-2"
              aria-label={`AutoRemedy progress: ${Math.round((job.progress ?? 0) * 100)}%`}
            />

            {runningSubLabel && (
              <p className="mt-2 text-sm text-text-subtle">{runningSubLabel}</p>
            )}
          </div>

          {/* Activity log */}
          {job.activity_log && job.activity_log.length > 0 && (
            <div className="mt-4 border-t border-border pt-4">
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-subtle">
                Activity
              </h3>
              <div className="max-h-[60vh] overflow-y-auto space-y-0.5 font-mono" aria-label="AutoRemedy activity log">
                {[...job.activity_log].reverse().map((entry, i) => {
                  // Style based on content
                  const isPageHeader = entry.match(/^\[\d+\/\d+\]/)
                  const isSuccess = entry.startsWith('  ✓')
                  const isWarning = entry.startsWith('  ⚠')
                  const isLayer = entry.startsWith('  ↳')
                  const isSummary = entry.startsWith('Applied:') || entry.startsWith('Scan complete:')

                  let style = 'text-text-subtle'
                  if (i === 0) style = 'text-text font-medium'
                  else if (isPageHeader) style = 'text-text-muted font-medium mt-1'
                  else if (isSuccess) style = 'text-green-600'
                  else if (isWarning) style = 'text-amber-600'
                  else if (isLayer) style = 'text-blue-500'
                  else if (isSummary) style = 'text-text-muted font-medium'

                  return (
                    <p key={i} className={`text-xs leading-relaxed ${style}`}>
                      {entry}
                    </p>
                  )
                })}
              </div>
            </div>
          )}
        </Card>
      )}

      {/* ── State 3: Completed ── */}
      {isCompleted && (
        <Card>
          <div className="flex flex-col items-center text-center py-4">
            <ShieldCheck
              className="h-16 w-16 text-green-500"
              aria-hidden="true"
            />
            <h2 className="mt-4 text-2xl font-bold text-text">Course Fixed!</h2>

            <div className="mt-4 space-y-1 text-sm text-text-muted">
              {issuesFixed > 0 && (
                <p>
                  <span className="font-semibold text-green-700">{issuesFixed} issues fixed</span>
                  {pagesUpdated > 0 && (
                    <> across <span className="font-semibold">{pagesUpdated} pages</span></>
                  )}
                </p>
              )}
              {docsConverted > 0 && (
                <p>
                  <span className="font-semibold text-blue-700">
                    {docsConverted} document{docsConverted === 1 ? '' : 's'}
                  </span>{' '}
                  converted to accessible pages
                </p>
              )}
            </div>

            <div className="mt-8 flex flex-col items-center gap-3 w-full max-w-xs">
              <Button
                size="lg"
                variant="outline"
                className="w-full"
                onClick={handleExport}
                disabled={exportSubmitting || exportPhase === 'starting' || exportPhase === 'exporting'}
                loading={exportSubmitting || exportPhase === 'starting' || exportPhase === 'exporting'}
                icon={Download}
                aria-label="Download a fresh remediated Canvas export"
              >
                Download Remediated Course
              </Button>

              <Button
                size="lg"
                className="w-full"
                onClick={() => navigate('/dashboard')}
              >
                Return to Dashboard
              </Button>

              {job.remediation_job_id && (
                <Link
                  to={`/remediate/${job.remediation_job_id}/changes`}
                  className={
                    'text-sm text-brand-primary hover:underline focus:outline-none ' +
                    'focus:ring-2 focus:ring-brand-primary focus:ring-offset-2 rounded'
                  }
                >
                  See what changed &rarr;
                </Link>
              )}
            </div>

            {exportStatusLabel && (
              <p className="mt-3 text-xs text-text-subtle">{exportStatusLabel}</p>
            )}
            {exportError && (
              <p className="mt-2 text-sm text-red-600" role="alert">
                {exportError}
              </p>
            )}
          </div>
        </Card>
      )}

      {/* ── State 4: Failed ── */}
      {isFailed && job && (
        <Card role="alert">
          <h2 className="text-lg font-semibold text-text">
            {job.error === 'Cancelled by user request'
              ? 'AutoRemedy cancelled.'
              : "AutoRemedy didn't finish."}
          </h2>
          <p className="mt-1 text-sm text-text-muted">
            {job.error === 'Cancelled by user request'
              ? 'You cancelled this run. No further changes were made to your course. Click Try Again to start a new run.'
              : job.error?.startsWith('Job orphaned by app restart')
              ? 'The previous run was interrupted by a server restart. Click Try Again to start over.'
              : 'Something went wrong while fixing your course. Try running it again — most issues self-resolve on a retry.'}
          </p>
          {job.error && (
            <details className="mt-3">
              <summary className="cursor-pointer text-xs text-text-subtle hover:text-text-muted">
                Technical detail
              </summary>
              <p className="mt-1 text-xs text-text-subtle font-mono break-words">{job.error}</p>
            </details>
          )}
          <div className="mt-6">
            <Button
              size="lg"
              className="w-full"
              onClick={handleReset}
              aria-label="Try AutoRemedy again"
            >
              Try Again
            </Button>
          </div>
        </Card>
      )}
    </div>
  )
}
