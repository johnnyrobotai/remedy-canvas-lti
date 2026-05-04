import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useLTI } from '@/contexts/LTIContext'
import { useSessionExpired } from '@/hooks/useSessionExpired'
import {
  apiClient,
  getReport,
  type ACRSummaryResponse,
  type CourseACR,
  type ConformanceLevel,
  type ReportSummary,
} from '@/api/client'
import { ConformanceGauge } from '@/components/ui/ConformanceGauge'
import { ConformanceBadge } from '@/components/ui/ConformanceBadge'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { SessionExpiredBanner } from '@/components/ui/SessionExpiredBanner'
import { Spinner } from '@/components/ui/Spinner'
import { parseIsoUtc } from '@/utils/datetime'
import {
  Calendar,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  TrendingDown,
  FileCheck,
  User,
  Shield,
  Download,
  AlertTriangle,
} from 'lucide-react'

interface CriterionGroup {
  category: string
  criteria: CourseACR['criteria']
}

export function ACRDashboardPage() {
  const navigate = useNavigate()
  const { session } = useLTI()
  const sessionExpired = useSessionExpired()
  const courseId = session?.canvasCourseId

  const [reports, setReports] = useState<ACRSummaryResponse[]>([])
  const [latestACR, setLatestACR] = useState<CourseACR | null>(null)
  const [latestScanReport, setLatestScanReport] = useState<ReportSummary | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isGenerating, setIsGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showEvidence, setShowEvidence] = useState(false)
  const [showHistory, setShowHistory] = useState(false)

  useEffect(() => {
    if (courseId) loadData()
  }, [courseId])

  const loadData = async () => {
    if (!courseId) return
    setIsLoading(true)
    setError(null)
    try {
      const reportsRes = await apiClient.get<ACRSummaryResponse[]>(
        `/courses/${courseId}/acr/reports`
      )
      setReports(reportsRes)
      try {
        const latestRes = await apiClient.get<{ data: CourseACR }>(
          `/courses/${courseId}/acr/reports/latest?format=json`
        )
        if (latestRes.data) {
          setLatestACR(latestRes.data)
        }
      } catch {
        // No latest report yet
      }
      // Latest scan report — used to detect ACR staleness (CLU-61).
      try {
        const scan = await getReport(courseId)
        setLatestScanReport(scan)
      } catch {
        setLatestScanReport(null)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load ACR data')
    } finally {
      setIsLoading(false)
    }
  }

  const handleGenerateACR = async () => {
    if (!courseId) return
    setIsGenerating(true)
    try {
      const res = await apiClient.post<{ job_id: string; message: string }>(
        `/courses/${courseId}/acr/generate`,
        { evaluator: 'Remedy Canvas LTI Automated Scanner', include_evidence: true }
      )
      pollJob(res.job_id)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to generate ACR'
      try { setError(JSON.parse(msg).detail) } catch { setError(msg) }
      setIsGenerating(false)
    }
  }

  const pollJob = async (jobId: string) => {
    if (!courseId) return
    const checkStatus = async () => {
      // CLU-84: stop polling when the session has expired
      if (sessionExpired) {
        setIsGenerating(false)
        return
      }
      try {
        const job = await apiClient.get<{ job_id: string; status: string; acr_id?: string }>(
          `/courses/${courseId}/acr/jobs/${jobId}`
        )
        if (job.status === 'completed' && job.acr_id) {
          setIsGenerating(false)
          loadData()
        } else if (job.status === 'failed') {
          setIsGenerating(false)
          setError('ACR generation failed')
        } else {
          setTimeout(checkStatus, 2000)
        }
      } catch {
        setIsGenerating(false)
        setError('Failed to check ACR generation status')
      }
    }
    checkStatus()
  }

  const handleDownload = (format: 'html' | 'markdown') => {
    if (!courseId || !latestACR) return
    window.open(`/api/courses/${courseId}/acr/reports/${latestACR.id}/download?format=${format}`, '_blank')
  }

  const formatDate = (dateStr: string) =>
    parseIsoUtc(dateStr).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })

  const formatDateTime = (dateStr: string) =>
    parseIsoUtc(dateStr).toLocaleDateString('en-US', {
      year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit',
    })

  // --- Criteria grouping (from ACRReportViewerPage) ---

  const groupCriteriaByCategory = (criteria: CourseACR['criteria']): CriterionGroup[] => {
    const groups: Record<string, CourseACR['criteria']> = {}
    criteria.forEach((criterion) => {
      const categoryId = criterion.criterion_id.split('.').slice(0, 2).join('.')
      const categoryName = getCategoryName(categoryId)
      const key = `${categoryId} ${categoryName}`
      if (!groups[key]) groups[key] = []
      groups[key].push(criterion)
    })
    return Object.entries(groups)
      .map(([category, criteria]) => ({ category, criteria }))
      .sort((a, b) => a.category.localeCompare(b.category))
  }

  const getCategoryName = (categoryId: string): string => {
    const names: Record<string, string> = {
      '1.1': 'Text Alternatives', '1.2': 'Time-based Media', '1.3': 'Adaptable',
      '1.4': 'Distinguishable', '2.1': 'Keyboard Accessible', '2.2': 'Enough Time',
      '2.3': 'Seizures', '2.4': 'Navigable', '2.5': 'Input Modalities',
      '3.1': 'Readable', '3.2': 'Predictable', '3.3': 'Input Assistance', '4.1': 'Compatible',
    }
    return names[categoryId] || 'Other'
  }

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner size="lg" />
      </div>
    )
  }

  const acr = latestACR
  const groupedCriteria = acr ? groupCriteriaByCategory(acr.criteria) : []

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-text">
            Accessibility Conformance Report
          </h1>
          <p className="text-sm text-text-subtle">VPAT 2.5 WCAG 2.2 Level AA</p>
        </div>
        <div className="flex flex-col items-end gap-2">
          <Button
            onClick={handleGenerateACR}
            disabled={isGenerating}
            loading={isGenerating}
            icon={Calendar}
          >
            {isGenerating ? 'Generating...' : 'Generate New ACR'}
          </Button>
          {acr && (
            <div className="flex items-center gap-2">
              <Button onClick={() => handleDownload('html')} variant="secondary" size="sm" icon={Download}>
                Download HTML
              </Button>
              <Button onClick={() => handleDownload('markdown')} variant="secondary" size="sm" icon={Download}>
                Download Markdown
              </Button>
            </div>
          )}
        </div>
      </div>

      {/* CLU-84: Session expired banner */}
      {sessionExpired && <SessionExpiredBanner />}

      {error && (
        <div className="rounded-lg bg-red-50 p-4 text-sm text-red-800">{error}</div>
      )}

      {/* Staleness banner — ACR is older than the latest scan (CLU-61).
          The user is looking at conformance data computed against an
          out-of-date snapshot of the course. */}
      {acr && latestScanReport && (() => {
        // CLU-77: parse defensively as UTC — legacy rows in scan_reports
        // store naive timestamps which `new Date()` would parse as local time.
        const acrTime = parseIsoUtc(acr.generated_at).getTime()
        const scanTime = parseIsoUtc(latestScanReport.analyzed_at).getTime()
        if (!Number.isFinite(acrTime) || !Number.isFinite(scanTime)) return null
        if (scanTime <= acrTime) return null
        return (
          <div
            role="status"
            className="flex items-start gap-3 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm"
          >
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" aria-hidden="true" />
            <div className="flex-1">
              <p className="font-semibold text-amber-900">This report is out of date.</p>
              <p className="mt-1 text-amber-800">
                A newer scan was run on {formatDateTime(latestScanReport.analyzed_at)},
                after this ACR was generated on {formatDateTime(acr.generated_at)}.
                The numbers below reflect the older data.
              </p>
            </div>
            <Button
              onClick={handleGenerateACR}
              disabled={isGenerating}
              loading={isGenerating}
              size="sm"
              icon={RefreshCw}
            >
              Regenerate
            </Button>
          </div>
        )
      })()}

      {acr ? (
        <>
          {/* Summary: Gauge + Report Info */}
          <div className="grid gap-6 md:grid-cols-2">
            <Card>
              <div className="p-6">
                <ConformanceGauge
                  percentage={acr.conformance_percentage}
                  band={acr.score_band}
                  size={200}
                />
                <div className="mt-6 flex items-center justify-center gap-2">
                  <ConformanceBadge level={acr.overall_status} size="md" />
                </div>
                <p className="mt-2 text-center text-sm text-text-subtle">
                  Generated {formatDate(acr.generated_at)}
                </p>
              </div>
            </Card>

            <Card>
              <div className="p-6">
                <h3 className="mb-4 text-lg font-semibold">Report Information</h3>
                <div className="space-y-3 text-sm">
                  <div className="flex items-center gap-2 text-text-muted">
                    <User size={16} />
                    <span>Evaluator: {acr.evaluator}</span>
                  </div>
                  <div className="flex items-center gap-2 text-text-muted">
                    <Calendar size={16} />
                    <span>Generated: {formatDateTime(acr.generated_at)}</span>
                  </div>
                  <div className="flex items-center gap-2 text-text-muted">
                    <Shield size={16} />
                    <span>{acr.vpat_edition} | WCAG {acr.wcag_version} Level {acr.conformance_level}</span>
                  </div>
                </div>
              </div>
            </Card>
          </div>

          {/* Remediation Impact */}
          {acr.issues_before > 0 && (
            <Card>
              <div className="p-6">
                <div className="mb-4 flex items-center gap-2">
                  <TrendingDown className="h-5 w-5 text-green-600" />
                  <h3 className="text-lg font-semibold">Remediation Impact</h3>
                </div>
                <div className="grid gap-4 sm:grid-cols-4">
                  <div className="rounded-lg bg-red-50 p-4">
                    <p className="text-2xl font-bold text-red-700">{acr.issues_before.toLocaleString()}</p>
                    <p className="text-sm text-red-600">Issues Before</p>
                  </div>
                  <div className="rounded-lg bg-green-50 p-4">
                    <p className="text-2xl font-bold text-green-700">{acr.issues_after.toLocaleString()}</p>
                    <p className="text-sm text-green-600">Issues After</p>
                  </div>
                  <div className="rounded-lg bg-blue-50 p-4">
                    <p className="text-2xl font-bold text-blue-700">{acr.issues_fixed.toLocaleString()}</p>
                    <p className="text-sm text-blue-600">Issues Fixed</p>
                  </div>
                  <div className="rounded-lg bg-purple-50 p-4">
                    <p className="text-2xl font-bold text-purple-700">{acr.pages_remediated.toLocaleString()}</p>
                    <p className="text-sm text-purple-600">Pages Remediated</p>
                  </div>
                </div>
                <div className="mt-4 rounded-lg bg-green-100 p-3 text-center">
                  <span className="font-semibold text-green-800">
                    {((acr.issues_before - acr.issues_after) / acr.issues_before * 100).toFixed(0)}%
                  </span>
                  <span className="text-green-700"> reduction in accessibility issues</span>
                </div>
              </div>
            </Card>
          )}

          {/* WCAG 2.2 Criteria Assessment */}
          <Card>
            <div className="p-6">
              <h3 className="mb-4 text-lg font-semibold">WCAG 2.2 Criteria Assessment</h3>
              <div className="space-y-4">
                {groupedCriteria.map(({ category, criteria }) => (
                  <div key={category} className="rounded-lg border border-border">
                    <div className="flex items-center justify-between bg-surface-muted px-4 py-3">
                      <div className="flex items-center gap-3">
                        <span className="font-semibold text-text">Principle {category}</span>
                        <span className="text-sm text-text-subtle">({criteria.length} criteria)</span>
                      </div>
                      <div className="flex items-center gap-4 text-sm">
                        <span className="text-green-600">
                          {criteria.filter((c) => c.conformance === 'Supports').length} Supports
                        </span>
                        <span className="text-amber-600">
                          {criteria.filter((c) => c.conformance === 'Partially Supports').length} Partial
                        </span>
                        <span className="text-red-600">
                          {criteria.filter((c) => c.conformance === 'Does Not Support').length} Fail
                        </span>
                      </div>
                    </div>
                    <div className="p-4">
                      <table className="w-full text-left text-sm">
                        <thead>
                          <tr className="border-b border-border">
                            <th className="pb-2 font-semibold text-text">Criterion</th>
                            <th className="pb-2 font-semibold text-text">Level</th>
                            <th className="pb-2 font-semibold text-text">Status</th>
                            <th className="pb-2 font-semibold text-text">Issues</th>
                            <th className="pb-2 font-semibold text-text">Pages</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100">
                          {criteria.map((criterion) => (
                            <tr key={criterion.criterion_id}>
                              <td className="py-3">
                                <div className="font-medium text-text">
                                  {criterion.criterion_id} {criterion.name}
                                </div>
                                {criterion.remarks && (
                                  <div className="mt-1 text-xs text-text-subtle">{criterion.remarks}</div>
                                )}
                              </td>
                              <td className="py-3">
                                <span className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${
                                  criterion.level === 'A' ? 'bg-blue-100 text-blue-800' : 'bg-indigo-100 text-indigo-800'
                                }`}>
                                  Level {criterion.level}
                                </span>
                              </td>
                              <td className="py-3">
                                <ConformanceBadge level={criterion.conformance} size="sm" />
                              </td>
                              <td className="py-3">{criterion.issue_count.toLocaleString()}</td>
                              <td className="py-3">{criterion.pages_affected.toLocaleString()}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </Card>

          {/* Artifact Evidence */}
          {acr.evidence && acr.evidence.length > 0 && (
            <Card>
              <div className="p-6">
                <button
                  onClick={() => setShowEvidence(!showEvidence)}
                  className="flex w-full items-center justify-between"
                >
                  <h3 className="text-lg font-semibold">
                    Artifact Evidence ({acr.evidence.length} items)
                  </h3>
                  {showEvidence ? <ChevronDown size={20} /> : <ChevronRight size={20} />}
                </button>
                {showEvidence && (
                  <div className="mt-4 space-y-4">
                    {acr.evidence.slice(0, 10).map((artifact) => (
                      <div key={artifact.artifact_id} className="rounded-lg border border-border p-4">
                        <div className="flex items-start justify-between">
                          <div>
                            <h4 className="font-medium text-text">{artifact.title}</h4>
                            <a
                              href={artifact.canvas_url.startsWith('http') ? artifact.canvas_url : `${session?.canvasBaseUrl || ''}${artifact.canvas_url}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-sm text-[#003D66] hover:underline"
                            >
                              View in Canvas
                            </a>
                          </div>
                          <span className="text-xs text-text-subtle">{artifact.artifact_type}</span>
                        </div>
                        {artifact.findings.length > 0 && (
                          <div className="mt-3 space-y-2">
                            {artifact.findings.map((finding, idx) => (
                              <div
                                key={idx}
                                className={`rounded p-2 text-sm ${
                                  finding.severity === 'error' ? 'bg-red-50 text-red-800'
                                    : finding.severity === 'warning' ? 'bg-amber-50 text-amber-800'
                                    : 'bg-blue-50 text-blue-800'
                                }`}
                              >
                                <span className="font-semibold">{finding.rule_id}</span>{' '}
                                ({finding.wcag_criterion}) - {finding.message}
                                {finding.remediation_applied && (
                                  <span className="ml-2 text-green-700">Auto-remediated</span>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                    {acr.evidence.length > 10 && (
                      <p className="text-center text-sm text-text-subtle">
                        + {acr.evidence.length - 10} more artifacts
                      </p>
                    )}
                  </div>
                )}
              </div>
            </Card>
          )}

          {/* Report History (collapsible, at bottom) */}
          {reports.length > 0 && (
            <Card>
              <div className="p-6">
                <button
                  onClick={() => setShowHistory(!showHistory)}
                  className="flex w-full items-center justify-between"
                >
                  <h3 className="text-lg font-semibold">
                    Report History ({reports.length} {reports.length === 1 ? 'report' : 'reports'})
                  </h3>
                  {showHistory ? <ChevronDown size={20} /> : <ChevronRight size={20} />}
                </button>
                {showHistory && (
                  <div className="mt-4 overflow-x-auto">
                    <table className="w-full text-left text-sm">
                      <thead>
                        <tr className="border-b border-border">
                          <th className="pb-3 font-semibold text-text">Date</th>
                          <th className="pb-3 font-semibold text-text">Conformance</th>
                          <th className="pb-3 font-semibold text-text">Status</th>
                          <th className="pb-3 font-semibold text-text">Issues</th>
                          <th className="pb-3 font-semibold text-text"></th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-100">
                        {reports.map((report) => (
                          <tr key={report.id}>
                            <td className="py-3">{formatDate(report.generated_at)}</td>
                            <td className="py-3">
                              <span className="font-semibold">{report.conformance_percentage.toFixed(1)}%</span>
                            </td>
                            <td className="py-3">
                              <ConformanceBadge level={report.overall_status as ConformanceLevel} size="sm" />
                            </td>
                            <td className="py-3">{report.issues_after.toLocaleString()}</td>
                            <td className="py-3">
                              <button
                                onClick={() => navigate(`/acr/${report.id}`)}
                                className="flex items-center gap-1 text-sm font-medium text-[#003D66] hover:underline"
                              >
                                View <ChevronRight size={16} />
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </Card>
          )}

          {/* Footer */}
          <div className="text-center text-sm text-text-subtle">
            <p>
              Report generated by Remedy Canvas LTI on {formatDateTime(acr.generated_at)}. This
              Accessibility Conformance Report is based on automated WCAG 2.2 Level AA
              testing. Manual testing may reveal additional accessibility considerations.
            </p>
          </div>
        </>
      ) : (
        <Card>
          <div className="p-12 text-center">
            <FileCheck className="mx-auto h-12 w-12 text-text-subtle" />
            <h3 className="mt-4 text-lg font-semibold text-text">No ACR Reports Yet</h3>
            <p className="mt-2 text-text-subtle">
              Generate your first Accessibility Conformance Report to see WCAG 2.2 compliance data.
            </p>
            <Button
              onClick={handleGenerateACR}
              disabled={isGenerating}
              loading={isGenerating}
              icon={RefreshCw}
              className="mt-6"
            >
              {isGenerating ? 'Generating...' : 'Generate ACR'}
            </Button>
          </div>
        </Card>
      )}
    </div>
  )
}
