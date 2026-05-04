import { useState, useEffect, useCallback, useRef } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useLTI } from '@/contexts/LTIContext'
import { Button, Card, ProgressBar, ScoreGauge } from '@/components/ui'
import { usePageActions } from '@/components/layout/PageActionsContext'
import {
  getReport,
  getIssues,
  startRemediation,
  getRemediationJob,
  type ReportSummary,
  type RemediationJob,
  type RemediationRequest,
  type IssuesResponse,
} from '@/api/client'

const FIX_CATEGORIES = [
  { key: 'fix_images', label: 'Images', category: 'images' },
  { key: 'fix_headings', label: 'Headings', category: 'headings' },
  { key: 'fix_tables', label: 'Tables', category: 'tables' },
  { key: 'fix_links', label: 'Links', category: 'links' },
  { key: 'fix_contrast', label: 'Contrast', category: 'contrast' },
  { key: 'fix_structure', label: 'Structure', category: 'structure' },
  { key: 'fix_media', label: 'Media', category: 'media' },
  { key: 'fix_math', label: 'Math', category: 'math' },
] as const

const CAMPUSES = [
  { value: 'laccd', label: 'LACCD (Default)' },
  { value: 'elac', label: 'East LA College' },
  { value: 'lacc', label: 'LA City College' },
  { value: 'lahc', label: 'LA Harbor College' },
  { value: 'lamc', label: 'LA Mission College' },
  { value: 'lapc', label: 'LA Pierce College' },
  { value: 'lasc', label: 'LA Southwest College' },
  { value: 'lattc', label: 'LA Trade-Tech College' },
  { value: 'lavc', label: 'LA Valley College' },
  { value: 'wlac', label: 'West LA College' },
]

export function RemediatePage() {
  const { session } = useLTI()
  const navigate = useNavigate()
  const [report, setReport] = useState<ReportSummary | null>(null)
  const [categoryCounts, setCategoryCounts] = useState<Record<string, number>>({})
  const [fixes, setFixes] = useState<Record<string, boolean>>(() => {
    const initial: Record<string, boolean> = {}
    FIX_CATEGORIES.forEach((c) => {
      initial[c.key] = true
    })
    return initial
  })
  const [campus, setCampus] = useState('laccd')
  const [generateAltText, setGenerateAltText] = useState(true)
  const [useAiRemediation, setUseAiRemediation] = useState(false)
  const [applyToAll, setApplyToAll] = useState(false)
  const [job, setJob] = useState<RemediationJob | null>(null)
  const [pageLoading, setPageLoading] = useState(true)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const courseId = session?.canvasCourseId

  // Load report + category counts
  useEffect(() => {
    if (!courseId) return
    setPageLoading(true)

    Promise.all([
      getReport(courseId).catch(() => null),
      getIssues(courseId, { per_page: 200 }).catch(() => null),
    ]).then(([rpt, issuesData]: [ReportSummary | null, IssuesResponse | null]) => {
      setReport(rpt)
      if (issuesData) {
        const counts: Record<string, number> = {}
        for (const issue of issuesData.issues) {
          if (issue.can_auto_fix) {
            counts[issue.category] = (counts[issue.category] || 0) + 1
          }
        }
        setCategoryCounts(counts)
      }
      setPageLoading(false)
    })
  }, [courseId])

  // Poll remediation job
  useEffect(() => {
    if (!job || !courseId) return
    if (job.status === 'completed' || job.status === 'failed') {
      if (job.status === 'completed') {
        navigate(`/remediate/${job.id}/preview`)
      }
      return
    }

    pollRef.current = setInterval(async () => {
      try {
        const updated = await getRemediationJob(courseId, job.id)
        setJob(updated)
      } catch {
        // Will retry
      }
    }, 2000)

    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [job?.id, job?.status, courseId, navigate])

  const handleStart = useCallback(async () => {
    if (!courseId) return

    const request: RemediationRequest = {
      campus,
      generate_alt_text: generateAltText,
      use_ai_remediation: useAiRemediation,
      apply_to_all_instances: applyToAll,
      ...fixes,
    }

    try {
      const newJob = await startRemediation(courseId, request)
      setJob(newJob)
    } catch (err) {
      console.error('Failed to start remediation:', err)
    }
  }, [courseId, campus, generateAltText, useAiRemediation, applyToAll, fixes])

  const toggleFix = (key: string) => {
    setFixes((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  const isRunning = job?.status === 'pending' || job?.status === 'running'
  const totalFixable = Object.values(categoryCounts).reduce((a, b) => a + b, 0)

  // TopBar actions: Start button when idle, ProgressBar when running
  usePageActions(
    isRunning && job ? (
      <ProgressBar
        progress={job.progress ?? 0}
        label="Remediating..."
        sublabel={`${job.pages_remediated ?? 0}/${job.pages_total ?? '?'}`}
        className="w-48"
      />
    ) : report ? (
      <Button size="lg" onClick={handleStart} disabled={totalFixable === 0}>
        Start Remediation
      </Button>
    ) : null,
  )

  return (
    <div>
        {pageLoading ? (
          <div className="py-12 text-center text-text-subtle">Loading scan data...</div>
        ) : !report ? (
          <div className="py-12 text-center text-text-subtle">
            No scan report found.{' '}
            <Link to="/dashboard" className="text-brand-primary hover:underline">
              Run a scan first
            </Link>
            .
          </div>
        ) : (
          <div className="space-y-6">
            {/* Score summary */}
            <Card>
              <div className="flex items-center gap-6">
                <ScoreGauge score={report.score} size={96} />
                <div className="flex-1">
                  <h2 className="text-sm font-medium uppercase tracking-wide text-text-subtle">
                    Current Score
                  </h2>
                  <p className="mt-1 text-3xl font-bold text-text">{report.score}/100</p>
                  <div className="mt-1 text-sm text-text-subtle">
                    <p>{report.errors} errors / {report.warnings} warnings</p>
                    <p>{totalFixable} auto-fixable issues</p>
                  </div>
                </div>
              </div>
            </Card>

            {/* Fix category toggles */}
            <Card title="Fix Categories">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {FIX_CATEGORIES.map((cat) => {
                  const count = categoryCounts[cat.category] || 0
                  return (
                    <label
                      key={cat.key}
                      className={`flex cursor-pointer items-center gap-2 rounded-lg border p-3 transition ${
                        fixes[cat.key]
                          ? 'border-brand-primary bg-blue-50'
                          : 'border-border bg-surface-muted'
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={fixes[cat.key]}
                        onChange={() => toggleFix(cat.key)}
                        className="h-4 w-4 rounded text-brand-primary"
                      />
                      <div>
                        <span className="text-sm font-medium text-text-muted">
                          {cat.label}
                        </span>
                        {count > 0 && (
                          <span className="ml-1 text-xs text-text-subtle">({count})</span>
                        )}
                      </div>
                    </label>
                  )
                })}
              </div>
            </Card>

            {/* Campus selector */}
            <Card title="Campus Color Scheme">
              <select
                value={campus}
                onChange={(e) => setCampus(e.target.value)}
                className="w-full max-w-xs rounded-lg border border-border px-3 py-2 text-sm"
                aria-label="Select campus"
              >
                {CAMPUSES.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </Card>

            {/* AI options */}
            <Card title="AI Options">
              <div className="space-y-3">
                <label className="flex items-start gap-3">
                  <input
                    type="checkbox"
                    checked={generateAltText}
                    onChange={() => setGenerateAltText(!generateAltText)}
                    className="mt-0.5 h-4 w-4 rounded text-brand-primary"
                  />
                  <div>
                    <span className="text-sm font-medium text-text-muted">
                      AI Alt Text
                    </span>
                    {report && report.images_needing_alt > 0 && (
                      <span className="ml-1 text-xs text-text-subtle">
                        ({report.images_needing_alt} images)
                      </span>
                    )}
                    <p className="text-xs text-text-subtle">
                      Generate descriptive alt text for images missing it
                    </p>
                  </div>
                </label>

                <label
                  className={`flex items-start gap-3 ${
                    !generateAltText ? 'opacity-50' : ''
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={useAiRemediation}
                    onChange={() => setUseAiRemediation(!useAiRemediation)}
                    disabled={!generateAltText}
                    className="mt-0.5 h-4 w-4 rounded text-brand-primary"
                  />
                  <div>
                    <span className="text-sm font-medium text-text-muted">
                      AI Remediation
                    </span>
                    <p className="text-xs text-text-subtle">
                      Use AI for advanced fixes (headings, links)
                    </p>
                  </div>
                </label>
                <label className="flex items-start gap-3">
                  <input
                    type="checkbox"
                    checked={applyToAll}
                    onChange={() => setApplyToAll(!applyToAll)}
                    className="mt-0.5 h-4 w-4 rounded text-brand-primary"
                  />
                  <div>
                    <span className="text-sm font-medium text-text-muted">
                      Apply to All Instances
                    </span>
                    <p className="text-xs text-text-subtle">
                      Fix every occurrence of matching issues across the course
                    </p>
                  </div>
                </label>
              </div>
              <p className="mt-3 text-xs text-text-subtle">
                Using: Ollama Cloud (kimi-k2.6)
              </p>
            </Card>

            {/* Start button / progress */}
            <div className="flex items-center justify-end gap-4">
              {isRunning ? (
                <ProgressBar
                  progress={job?.progress ?? 0}
                  label="Remediating..."
                  sublabel={`${job?.pages_remediated ?? 0}/${job?.pages_total ?? '?'}`}
                  className="w-64"
                />
              ) : (
                <Button
                  size="lg"
                  onClick={handleStart}
                  disabled={totalFixable === 0}
                >
                  Start Remediation
                </Button>
              )}
              {job?.status === 'failed' && (
                <p className="text-xs text-red-500">
                  Failed: {job.error ?? 'Unknown error'}
                </p>
              )}
            </div>
          </div>
        )}
    </div>
  )
}
