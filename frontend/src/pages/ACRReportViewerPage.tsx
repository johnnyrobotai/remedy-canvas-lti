import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useLTI } from '@/contexts/LTIContext'
import { apiClient, type CourseACR } from '@/api/client'
import { ConformanceGauge } from '@/components/ui/ConformanceGauge'
import { ConformanceBadge } from '@/components/ui/ConformanceBadge'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { Spinner } from '@/components/ui/Spinner'
import {
  ChevronLeft,
  ChevronDown,
  ChevronRight,
  FileText,
  Code,
  FileCode,
  Calendar,
  User,
  Shield,
  AlertCircle,
} from 'lucide-react'

interface CriterionGroup {
  category: string
  criteria: CourseACR['criteria']
}

export function ACRReportViewerPage() {
  const { acrId } = useParams<{ acrId: string }>()
  const navigate = useNavigate()
  const { session } = useLTI()
  const courseId = session?.canvasCourseId

  const [acr, setACR] = useState<CourseACR | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(
    new Set()
  )
  const [showEvidence, setShowEvidence] = useState(false)

  useEffect(() => {
    if (courseId && acrId) {
      loadACR()
    }
  }, [courseId, acrId])

  const loadACR = async () => {
    if (!courseId || !acrId) return

    setIsLoading(true)
    setError(null)

    try {
      const res = await apiClient.get<{ data: CourseACR }>(
        `/courses/${courseId}/acr/reports/${acrId}?format=json`
      )
      if (res.data) {
        setACR(res.data)
        // Expand first category by default
        const categories = groupCriteriaByCategory(res.data.criteria)
        if (categories.length > 0) {
          setExpandedCategories(new Set([categories[0].category]))
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load ACR')
    } finally {
      setIsLoading(false)
    }
  }

  const groupCriteriaByCategory = (
    criteria: CourseACR['criteria']
  ): CriterionGroup[] => {
    const groups: Record<string, CourseACR['criteria']> = {}

    criteria.forEach((criterion) => {
      const categoryId = criterion.criterion_id.split('.').slice(0, 2).join('.')
      const categoryName = getCategoryName(categoryId)
      const key = `${categoryId} ${categoryName}`

      if (!groups[key]) {
        groups[key] = []
      }
      groups[key].push(criterion)
    })

    return Object.entries(groups)
      .map(([category, criteria]) => ({ category, criteria }))
      .sort((a, b) => a.category.localeCompare(b.category))
  }

  const getCategoryName = (categoryId: string): string => {
    const names: Record<string, string> = {
      '1.1': 'Text Alternatives',
      '1.2': 'Time-based Media',
      '1.3': 'Adaptable',
      '1.4': 'Distinguishable',
      '2.1': 'Keyboard Accessible',
      '2.2': 'Enough Time',
      '2.3': 'Seizures',
      '2.4': 'Navigable',
      '2.5': 'Input Modalities',
      '3.1': 'Readable',
      '3.2': 'Predictable',
      '3.3': 'Input Assistance',
      '4.1': 'Compatible',
    }
    return names[categoryId] || 'Other'
  }

  const toggleCategory = (category: string) => {
    setExpandedCategories((prev) => {
      const next = new Set(prev)
      if (next.has(category)) {
        next.delete(category)
      } else {
        next.add(category)
      }
      return next
    })
  }

  const handleDownload = (format: 'html' | 'json' | 'markdown') => {
    if (!courseId || !acrId) return
    const url = `/api/courses/${courseId}/acr/reports/${acrId}/download?format=${format}`
    window.open(url, '_blank')
  }

  const handleBack = () => {
    navigate('/acr')
  }

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner size="lg" />
      </div>
    )
  }

  if (error || !acr) {
    return (
      <div className="space-y-4">
        <Button onClick={handleBack} variant="secondary" icon={ChevronLeft}>
          Back to ACR Dashboard
        </Button>
        <div className="rounded-lg bg-red-50 p-8 text-center">
          <AlertCircle className="mx-auto h-12 w-12 text-red-500" />
          <h3 className="mt-4 text-lg font-semibold text-red-900">
            Failed to Load Report
          </h3>
          <p className="mt-2 text-red-700">{error || 'Report not found'}</p>
        </div>
      </div>
    )
  }

  const groupedCriteria = groupCriteriaByCategory(acr.criteria)

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Button onClick={handleBack} variant="secondary" icon={ChevronLeft}>
            Back
          </Button>
          <div>
            <h1 className="text-2xl font-bold text-gray-900">
              {acr.course_name}
            </h1>
            <p className="text-sm text-gray-500">
              Accessibility Conformance Report
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            onClick={() => handleDownload('html')}
            variant="secondary"
            icon={FileText}
          >
            HTML
          </Button>
          <Button
            onClick={() => handleDownload('json')}
            variant="secondary"
            icon={Code}
          >
            JSON
          </Button>
          <Button
            onClick={() => handleDownload('markdown')}
            variant="secondary"
            icon={FileCode}
          >
            Markdown
          </Button>
        </div>
      </div>

      {/* Summary Cards */}
      <div className="grid gap-6 md:grid-cols-3">
        {/* Overall Conformance */}
        <Card>
          <div className="p-6">
            <ConformanceGauge
              percentage={acr.conformance_percentage}
              band={acr.score_band}
              size={140}
            />
            <div className="mt-4 flex items-center justify-center gap-2">
              <ConformanceBadge level={acr.overall_status} size="md" />
            </div>
          </div>
        </Card>

        {/* Report Info */}
        <Card>
          <div className="p-6">
            <h3 className="mb-4 text-lg font-semibold">Report Information</h3>
            <div className="space-y-3 text-sm">
              <div className="flex items-center gap-2 text-gray-600">
                <User size={16} />
                <span>Evaluator: {acr.evaluator}</span>
              </div>
              <div className="flex items-center gap-2 text-gray-600">
                <Calendar size={16} />
                <span>Generated: {formatDate(acr.generated_at)}</span>
              </div>
              <div className="flex items-center gap-2 text-gray-600">
                <Shield size={16} />
                <span>
                  {acr.vpat_edition} | WCAG {acr.wcag_version} Level{' '}
                  {acr.conformance_level}
                </span>
              </div>
            </div>
          </div>
        </Card>

        {/* Remediation Impact */}
        {acr.issues_before > 0 && (
          <Card>
            <div className="p-6">
              <h3 className="mb-4 text-lg font-semibold">Remediation Impact</h3>
              <div className="grid grid-cols-2 gap-4">
                <div className="rounded-lg bg-red-50 p-3 text-center">
                  <p className="text-xl font-bold text-red-700">
                    {acr.issues_before}
                  </p>
                  <p className="text-xs text-red-600">Before</p>
                </div>
                <div className="rounded-lg bg-green-50 p-3 text-center">
                  <p className="text-xl font-bold text-green-700">
                    {acr.issues_after}
                  </p>
                  <p className="text-xs text-green-600">After</p>
                </div>
              </div>
              <div className="mt-4 text-center">
                <span className="text-2xl font-bold text-green-600">
                  {((acr.issues_fixed / acr.issues_before) * 100).toFixed(0)}%
                </span>
                <span className="text-sm text-gray-600"> issues resolved</span>
              </div>
            </div>
          </Card>
        )}
      </div>

      {/* WCAG Criteria by Category */}
      <Card>
        <div className="p-6">
          <h3 className="mb-4 text-lg font-semibold">
            WCAG 2.2 Criteria Assessment
          </h3>

          <div className="space-y-2">
            {groupedCriteria.map(({ category, criteria }) => (
              <div
                key={category}
                className="rounded-lg border border-gray-200"
              >
                <button
                  onClick={() => toggleCategory(category)}
                  className="flex w-full items-center justify-between bg-gray-50 px-4 py-3 text-left hover:bg-gray-100"
                >
                  <div className="flex items-center gap-3">
                    {expandedCategories.has(category) ? (
                      <ChevronDown size={20} className="text-gray-500" />
                    ) : (
                      <ChevronRight size={20} className="text-gray-500" />
                    )}
                    <span className="font-semibold text-gray-900">
                      Principle {category}
                    </span>
                    <span className="text-sm text-gray-500">
                      ({criteria.length} criteria)
                    </span>
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
                </button>

                {expandedCategories.has(category) && (
                  <div className="p-4">
                    <table className="w-full text-left text-sm">
                      <thead>
                        <tr className="border-b border-gray-200">
                          <th className="pb-2 font-semibold text-gray-900">
                            Criterion
                          </th>
                          <th className="pb-2 font-semibold text-gray-900">
                            Level
                          </th>
                          <th className="pb-2 font-semibold text-gray-900">
                            Status
                          </th>
                          <th className="pb-2 font-semibold text-gray-900">
                            Issues
                          </th>
                          <th className="pb-2 font-semibold text-gray-900">
                            Pages
                          </th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-100">
                        {criteria.map((criterion) => (
                          <tr key={criterion.criterion_id}>
                            <td className="py-3">
                              <div className="font-medium text-gray-900">
                                {criterion.criterion_id} {criterion.name}
                              </div>
                              {criterion.remarks && (
                                <div className="mt-1 text-xs text-gray-500">
                                  {criterion.remarks}
                                </div>
                              )}
                            </td>
                            <td className="py-3">
                              <span
                                className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${
                                  criterion.level === 'A'
                                    ? 'bg-blue-100 text-blue-800'
                                    : 'bg-indigo-100 text-indigo-800'
                                }`}
                              >
                                Level {criterion.level}
                              </span>
                            </td>
                            <td className="py-3">
                              <ConformanceBadge
                                level={criterion.conformance}
                                size="sm"
                              />
                            </td>
                            <td className="py-3">
                              {criterion.issue_count.toLocaleString()}
                            </td>
                            <td className="py-3">
                              {criterion.pages_affected.toLocaleString()}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
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
              {showEvidence ? (
                <ChevronDown size={20} />
              ) : (
                <ChevronRight size={20} />
              )}
            </button>

            {showEvidence && (
              <div className="mt-4 space-y-4">
                {acr.evidence.slice(0, 10).map((artifact) => (
                  <div
                    key={artifact.artifact_id}
                    className="rounded-lg border border-gray-200 p-4"
                  >
                    <div className="flex items-start justify-between">
                      <div>
                        <h4 className="font-medium text-gray-900">
                          {artifact.title}
                        </h4>
                        <a
                          href={artifact.canvas_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-sm text-[#003D66] hover:underline"
                        >
                          View in Canvas →
                        </a>
                      </div>
                      <span className="text-xs text-gray-500">
                        {artifact.artifact_type}
                      </span>
                    </div>

                    {artifact.findings.length > 0 && (
                      <div className="mt-3 space-y-2">
                        {artifact.findings.map((finding, idx) => (
                          <div
                            key={idx}
                            className={`rounded p-2 text-sm ${
                              finding.severity === 'error'
                                ? 'bg-red-50 text-red-800'
                                : finding.severity === 'warning'
                                ? 'bg-amber-50 text-amber-800'
                                : 'bg-blue-50 text-blue-800'
                            }`}
                          >
                            <span className="font-semibold">
                              {finding.rule_id}
                            </span>{' '}
                            ({finding.wcag_criterion}) - {finding.message}
                            {finding.remediation_applied && (
                              <span className="ml-2 text-green-700">
                                ✓ Auto-remediated
                              </span>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}

                {acr.evidence.length > 10 && (
                  <p className="text-center text-sm text-gray-500">
                    + {acr.evidence.length - 10} more artifacts
                  </p>
                )}
              </div>
            )}
          </div>
        </Card>
      )}

      {/* Footer */}
      <div className="text-center text-sm text-gray-500">
        <p>
          Report generated by Remedy Canvas LTI on {formatDate(acr.generated_at)}. This
          Accessibility Conformance Report is based on automated WCAG 2.2 Level
          AA testing. Manual testing may reveal additional accessibility
          considerations.
        </p>
      </div>
    </div>
  )
}
