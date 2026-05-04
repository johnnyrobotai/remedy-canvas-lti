import { useState, useEffect, useCallback, useRef } from 'react'
import { useLTI } from '@/contexts/LTIContext'
import { Button, Badge, FilterChip, ProgressBar, Spinner } from '@/components/ui'
import { usePageActions } from '@/components/layout/PageActionsContext'
import {
  startFileAudit,
  getFileAuditJob,
  getFileReport,
  startPDFFix,
  getPDFFixJob,
  uploadFixedPDF,
  startConversion,
  getConversionJob,
  startOCR,
  getOCRJob,
  type FileAuditJob,
  type FileReport,
  type FileAuditEntry,
  type PDFFixJob,
  type ConversionJob,
  type OCRJobType,
} from '@/api/client'

const CONVERTIBLE_TYPES = new Set([
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'application/msword',
  'application/vnd.ms-powerpoint',
  'application/vnd.ms-excel',
])

function isConvertible(entry: FileAuditEntry): boolean {
  if (CONVERTIBLE_TYPES.has(entry.content_type)) return true
  if (!entry.is_pdf) {
    const ext = entry.filename.split('.').pop()?.toLowerCase() ?? ''
    return ['docx', 'pptx', 'xlsx'].includes(ext)
  }
  return false
}

function isScannedPDF(entry: FileAuditEntry): boolean {
  // A PDF flagged as failed with no text content (heuristic: failed audit = possible scanned)
  return entry.is_pdf && entry.status === 'failed'
}

const STATUS_BADGE_VARIANT: Record<string, 'success' | 'error' | 'neutral'> = {
  passed: 'success',
  failed: 'error',
  not_audited: 'neutral',
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

type FilterMode = 'all' | 'pdfs' | 'failed'

export function FilesPage() {
  const { session } = useLTI()
  const [report, setReport] = useState<FileReport | null>(null)
  const [reportLoading, setReportLoading] = useState(true)
  const [job, setJob] = useState<FileAuditJob | null>(null)
  const [filter, setFilter] = useState<FilterMode>('all')
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [fixJobs, setFixJobs] = useState<Record<number, PDFFixJob>>({})
  const [fixPolls, setFixPolls] = useState<Record<number, ReturnType<typeof setInterval>>>({})
  const [conversionJobs, setConversionJobs] = useState<Record<number, ConversionJob>>({})
  const [conversionPolls, setConversionPolls] = useState<Record<number, ReturnType<typeof setInterval>>>({})
  const [ocrJobs, setOcrJobs] = useState<Record<number, OCRJobType>>({})
  const [ocrPolls, setOcrPolls] = useState<Record<number, ReturnType<typeof setInterval>>>({})
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const courseId = session?.canvasCourseId

  // Load existing report
  useEffect(() => {
    if (!courseId) return
    setReportLoading(true)
    getFileReport(courseId)
      .then(setReport)
      .catch(() => setReport(null))
      .finally(() => setReportLoading(false))
  }, [courseId])

  // Poll scan job
  useEffect(() => {
    if (!job || !courseId) return
    if (job.status === 'completed' || job.status === 'failed') {
      if (job.status === 'completed') {
        getFileReport(courseId)
          .then(setReport)
          .catch(() => {})
      }
      return
    }

    pollRef.current = setInterval(async () => {
      try {
        const updated = await getFileAuditJob(courseId, job.id)
        setJob(updated)
      } catch {
        // Will retry
      }
    }, 2000)

    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [job?.id, job?.status, courseId])

  const handleStartScan = useCallback(async () => {
    if (!courseId) return
    try {
      const newJob = await startFileAudit(courseId)
      setJob(newJob)
    } catch (err) {
      console.error('Failed to start file audit:', err)
    }
  }, [courseId])

  const handleStartFix = useCallback(async (fileId: number) => {
    if (!courseId) return
    try {
      const fixJob = await startPDFFix(courseId, fileId)
      setFixJobs((prev) => ({ ...prev, [fileId]: fixJob }))

      const interval = setInterval(async () => {
        try {
          const updated = await getPDFFixJob(courseId, fixJob.id)
          setFixJobs((prev) => ({ ...prev, [fileId]: updated }))
          if (updated.status === 'completed' || updated.status === 'failed') {
            clearInterval(interval)
            setFixPolls((prev) => {
              const next = { ...prev }
              delete next[fileId]
              return next
            })
          }
        } catch {
          // Will retry
        }
      }, 2000)

      setFixPolls((prev) => ({ ...prev, [fileId]: interval }))
    } catch (err) {
      console.error('Failed to start PDF fix:', err)
    }
  }, [courseId])

  const handleUpload = useCallback(async (fileId: number, jobId: string, mode: 'replace' | 'alongside') => {
    if (!courseId) return
    try {
      await uploadFixedPDF(courseId, jobId, mode)
      setFixJobs((prev) => {
        const next = { ...prev }
        delete next[fileId]
        return next
      })
      // Refresh the file report
      getFileReport(courseId)
        .then(setReport)
        .catch(() => {})
    } catch (err) {
      console.error('Failed to upload fixed PDF:', err)
    }
  }, [courseId])

  const handleStartConversion = useCallback(async (fileId: number) => {
    if (!courseId) return
    try {
      const convJob = await startConversion(courseId, fileId)
      setConversionJobs((prev) => ({ ...prev, [fileId]: convJob }))

      const poll = setInterval(async () => {
        try {
          const updated = await getConversionJob(courseId, convJob.id)
          setConversionJobs((prev) => ({ ...prev, [fileId]: updated }))
          if (updated.status === 'completed' || updated.status === 'failed') {
            clearInterval(poll)
            setConversionPolls((prev) => {
              const next = { ...prev }
              delete next[fileId]
              return next
            })
          }
        } catch {
          // Will retry
        }
      }, 2000)

      setConversionPolls((prev) => ({ ...prev, [fileId]: poll }))
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Conversion failed'
      setConversionJobs((prev) => ({
        ...prev,
        [fileId]: { status: 'failed', error: msg } as ConversionJob,
      }))
    }
  }, [courseId])

  const handleStartOCR = useCallback(async (fileId: number) => {
    if (!courseId) return
    try {
      const ocrJob = await startOCR(courseId, fileId)
      setOcrJobs((prev) => ({ ...prev, [fileId]: ocrJob }))

      const poll = setInterval(async () => {
        try {
          const updated = await getOCRJob(courseId, ocrJob.id)
          setOcrJobs((prev) => ({ ...prev, [fileId]: updated }))
          if (updated.status === 'completed' || updated.status === 'failed') {
            clearInterval(poll)
            setOcrPolls((prev) => {
              const next = { ...prev }
              delete next[fileId]
              return next
            })
          }
        } catch {
          // Will retry
        }
      }, 2000)

      setOcrPolls((prev) => ({ ...prev, [fileId]: poll }))
    } catch (err) {
      console.error('Failed to start OCR:', err)
    }
  }, [courseId])

  // Cleanup fix polls on unmount
  useEffect(() => {
    return () => {
      Object.values(fixPolls).forEach(clearInterval)
    }
  }, [fixPolls])

  // Cleanup conversion polls on unmount
  useEffect(() => {
    return () => {
      Object.values(conversionPolls).forEach(clearInterval)
    }
  }, [conversionPolls])

  // Cleanup OCR polls on unmount
  useEffect(() => {
    return () => {
      Object.values(ocrPolls).forEach(clearInterval)
    }
  }, [ocrPolls])

  const isScanning = job?.status === 'pending' || job?.status === 'running'
  const scanFailed = job?.status === 'failed'

  const filteredEntries: FileAuditEntry[] = report
    ? report.entries.filter((e) => {
        if (filter === 'pdfs') return e.is_pdf
        if (filter === 'failed') return e.status === 'failed'
        return true
      })
    : []

  // Inject scan button / progress into TopBar
  usePageActions(
    isScanning ? (
      <ProgressBar
        progress={job?.progress ?? 0}
        label="Scanning files..."
        sublabel={`${job?.files_audited ?? 0}/${job?.files_total ?? '?'}`}
        className="w-64"
      />
    ) : (
      <Button onClick={handleStartScan} disabled={reportLoading} size="sm">
        {report ? 'Re-Scan Files' : 'Scan Course Files'}
      </Button>
    ),
  )

  return (
    <div>
        {/* Summary header */}
        {report && (
          <div className="mb-6 grid grid-cols-3 gap-4">
            <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
              <p className="text-xs font-medium uppercase tracking-wide text-text-subtle">
                Total Files
              </p>
              <p className="mt-1 text-3xl font-bold text-text">
                {report.total_files}
              </p>
            </div>
            <div className="rounded-xl border border-green-200 bg-surface p-4 shadow-sm">
              <p className="text-xs font-medium uppercase tracking-wide text-green-600">
                PDFs Passed
              </p>
              <p className="mt-1 text-3xl font-bold text-green-600">
                {report.pdfs_passed}
              </p>
            </div>
            <div className="rounded-xl border border-red-200 bg-surface p-4 shadow-sm">
              <p className="text-xs font-medium uppercase tracking-wide text-red-500">
                PDFs Failed
              </p>
              <p className="mt-1 text-3xl font-bold text-red-600">
                {report.pdfs_failed}
              </p>
            </div>
          </div>
        )}

        {/* Scan button + progress (inline fallback) */}
        <div className="mb-6 flex items-center gap-4">
          {isScanning ? (
            <ProgressBar
              progress={job?.progress ?? 0}
              label="Scanning files..."
              sublabel={`${job?.files_audited ?? 0}/${job?.files_total ?? '?'}`}
              className="w-64"
            />
          ) : (
            <Button onClick={handleStartScan} disabled={reportLoading}>
              {report ? 'Re-Scan Files' : 'Scan Course Files'}
            </Button>
          )}
          {scanFailed && (
            <p className="text-xs text-red-500">
              Scan failed: {job?.error ?? 'Unknown error'}
            </p>
          )}
        </div>

        {/* Filter bar */}
        {report && (
          <div className="mb-4 flex items-center gap-2">
            <FilterChip
              label="All"
              count={report.entries.length}
              active={filter === 'all'}
              onClick={() => setFilter('all')}
            />
            <FilterChip
              label="PDFs"
              count={report.pdf_count}
              active={filter === 'pdfs'}
              onClick={() => setFilter('pdfs')}
            />
            <FilterChip
              label="Failed"
              count={report.pdfs_failed}
              active={filter === 'failed'}
              onClick={() => setFilter('failed')}
            />
          </div>
        )}

        {/* File table */}
        {reportLoading ? (
          <div className="flex items-center justify-center gap-3 py-12 text-text-subtle">
            <Spinner size="sm" />
            <span>Loading file report...</span>
          </div>
        ) : !report ? (
          <div className="py-12 text-center text-text-subtle">
            No file report found. Run a scan to audit course files.
          </div>
        ) : filteredEntries.length === 0 ? (
          <div className="py-12 text-center text-text-subtle">
            No files match the current filter.
          </div>
        ) : (
          <div className="overflow-hidden rounded-xl border border-border bg-surface shadow-sm">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-border bg-surface-muted">
                <tr>
                  <th className="px-4 py-3 font-medium text-text-subtle">Filename</th>
                  <th className="px-4 py-3 font-medium text-text-subtle">Type</th>
                  <th className="px-4 py-3 font-medium text-text-subtle">Size</th>
                  <th className="px-4 py-3 font-medium text-text-subtle">Status</th>
                  <th className="px-4 py-3 font-medium text-text-subtle">Checks</th>
                  <th className="px-4 py-3 font-medium text-text-subtle">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {filteredEntries.map((entry) => (
                  <tr key={entry.file_id} className="hover:bg-surface-muted">
                    <td className="px-4 py-3">
                      {entry.is_pdf && entry.check_report ? (
                        <button
                          onClick={() =>
                            setExpandedId(
                              expandedId === entry.file_id ? null : entry.file_id
                            )
                          }
                          className="font-medium text-brand-primary hover:underline"
                        >
                          {entry.filename}
                        </button>
                      ) : (
                        <span className="text-text-muted">{entry.filename}</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-xs text-text-subtle">
                      {entry.content_type}
                    </td>
                    <td className="px-4 py-3 text-xs text-text-subtle">
                      {formatSize(entry.size)}
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant={STATUS_BADGE_VARIANT[entry.status] ?? 'neutral'}>
                        {entry.status.replace('_', ' ')}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-xs text-text-subtle">
                      {entry.check_report ? (
                        <span>
                          {entry.check_report.passed}/{entry.check_report.total_checks} passed
                        </span>
                      ) : (
                        <span className="text-gray-300">&mdash;</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap items-center gap-2">
                        {/* PDF fix button */}
                        {entry.status === 'failed' && entry.is_pdf && !fixJobs[entry.file_id] && (
                          <Button size="sm" onClick={() => handleStartFix(entry.file_id)}>
                            Fix PDF
                          </Button>
                        )}
                        {fixJobs[entry.file_id]?.status === 'pending' && (
                          <span className="text-xs text-text-subtle">Queued...</span>
                        )}
                        {fixJobs[entry.file_id]?.status === 'running' && (
                          <span className="text-xs text-brand-primary">
                            Fixing... {Math.round((fixJobs[entry.file_id].progress ?? 0) * 100)}%
                          </span>
                        )}
                        {fixJobs[entry.file_id]?.status === 'failed' && (
                          <span className="text-xs text-red-500">
                            Fix failed: {fixJobs[entry.file_id].error ?? 'Unknown'}
                          </span>
                        )}
                        {fixJobs[entry.file_id]?.status === 'completed' && (
                          <span className="text-xs text-green-600">Fixed</span>
                        )}

                        {/* Convert to Page button (DOCX/PPTX/XLSX) */}
                        {isConvertible(entry) && !conversionJobs[entry.file_id] && (
                          <Button
                            size="sm"
                            variant="secondary"
                            className="bg-blue-600 hover:bg-blue-700 text-white"
                            onClick={() => handleStartConversion(entry.file_id)}
                          >
                            Convert to Page
                          </Button>
                        )}
                        {conversionJobs[entry.file_id]?.status === 'pending' && (
                          <span className="text-xs text-text-subtle">Queued...</span>
                        )}
                        {conversionJobs[entry.file_id]?.status === 'running' && (
                          <span className="text-xs text-blue-600">
                            Converting... {Math.round((conversionJobs[entry.file_id].progress ?? 0) * 100)}%
                          </span>
                        )}
                        {conversionJobs[entry.file_id]?.status === 'completed' && (
                          <a
                            href={conversionJobs[entry.file_id].canvas_page_url ?? '#'}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs font-medium text-green-600 hover:underline"
                          >
                            Page created
                          </a>
                        )}
                        {conversionJobs[entry.file_id]?.status === 'failed' && (
                          <span className="text-xs text-red-500">
                            Conversion failed: {conversionJobs[entry.file_id].error ?? 'Unknown'}
                          </span>
                        )}

                        {/* OCR Extract button (scanned PDFs) */}
                        {isScannedPDF(entry) && !ocrJobs[entry.file_id] && (
                          <Button
                            size="sm"
                            variant="outline"
                            className="bg-purple-600 hover:bg-purple-700 text-white"
                            onClick={() => handleStartOCR(entry.file_id)}
                          >
                            OCR Extract
                          </Button>
                        )}
                        {ocrJobs[entry.file_id]?.status === 'pending' && (
                          <span className="text-xs text-text-subtle">OCR queued...</span>
                        )}
                        {ocrJobs[entry.file_id]?.status === 'running' && (
                          <span className="text-xs text-purple-600">
                            OCR {ocrJobs[entry.file_id].pages_processed}/{ocrJobs[entry.file_id].pages_total} pages...
                          </span>
                        )}
                        {ocrJobs[entry.file_id]?.status === 'completed' && (
                          <span className="text-xs text-green-600">
                            OCR done ({ocrJobs[entry.file_id].pages_total} pages)
                          </span>
                        )}
                        {ocrJobs[entry.file_id]?.status === 'failed' && (
                          <span className="text-xs text-red-500">
                            OCR failed: {ocrJobs[entry.file_id].error ?? 'Unknown'}
                          </span>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Completed fix verification reports */}
        {Object.entries(fixJobs)
          .filter(([, fj]) => fj.status === 'completed')
          .map(([fileIdStr, fj]) => {
            const fileId = Number(fileIdStr)
            return (
              <div key={fj.id} className="mt-4 rounded-xl border border-green-200 bg-surface p-6 shadow-sm">
                <div className="mb-4 flex items-center justify-between">
                  <h3 className="font-medium text-text-muted">
                    {fj.filename} &mdash; Fix Verification
                  </h3>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      setFixJobs((prev) => {
                        const next = { ...prev }
                        delete next[fileId]
                        return next
                      })
                    }
                  >
                    Dismiss
                  </Button>
                </div>

                {/* Before / After comparison */}
                <div className="mb-4 grid grid-cols-2 gap-4">
                  <div>
                    <p className="mb-1 text-xs font-medium uppercase text-text-subtle">Before</p>
                    {fj.checks_before ? (
                      <div className="rounded-xl border border-border bg-surface-muted p-3 text-sm">
                        <div className="flex justify-between">
                          <span className="text-text-subtle">Passed</span>
                          <span className="font-medium">{fj.checks_before.passed}/{fj.checks_before.total_checks}</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-text-subtle">Failed</span>
                          <span className="font-medium text-red-600">{fj.checks_before.failed}</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-text-subtle">Pass rate</span>
                          <span className="font-medium">{Math.round(fj.checks_before.pass_rate * 100)}%</span>
                        </div>
                      </div>
                    ) : (
                      <span className="text-xs text-gray-300">N/A</span>
                    )}
                  </div>
                  <div>
                    <p className="mb-1 text-xs font-medium uppercase text-text-subtle">After</p>
                    {fj.checks_after ? (
                      <div className="rounded-xl border border-green-200 bg-green-50 p-3 text-sm">
                        <div className="flex justify-between">
                          <span className="text-text-subtle">Passed</span>
                          <span className="font-medium">{fj.checks_after.passed}/{fj.checks_after.total_checks}</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-text-subtle">Failed</span>
                          <span className="font-medium text-red-600">{fj.checks_after.failed}</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-text-subtle">Pass rate</span>
                          <span className="font-medium text-green-700">{Math.round(fj.checks_after.pass_rate * 100)}%</span>
                        </div>
                      </div>
                    ) : (
                      <span className="text-xs text-gray-300">N/A</span>
                    )}
                  </div>
                </div>

                {/* Fixes applied / skipped */}
                {fj.fixes_applied.length > 0 && (
                  <div className="mb-3">
                    <p className="mb-1 text-xs font-medium uppercase text-green-600">Fixes Applied ({fj.fixes_applied.length})</p>
                    <ul className="list-inside list-disc text-xs text-text-muted">
                      {fj.fixes_applied.map((fix, i) => (
                        <li key={i}>{fix}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {fj.fixes_skipped.length > 0 && (
                  <div className="mb-3">
                    <p className="mb-1 text-xs font-medium uppercase text-yellow-600">Fixes Skipped ({fj.fixes_skipped.length})</p>
                    <ul className="list-inside list-disc text-xs text-text-subtle">
                      {fj.fixes_skipped.map((fix, i) => (
                        <li key={i}>{fix}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Upload buttons */}
                <div className="mt-4 flex items-center gap-3">
                  <Button size="sm" onClick={() => handleUpload(fileId, fj.id, 'replace')}>
                    Replace Original
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => handleUpload(fileId, fj.id, 'alongside')}
                  >
                    Save as New File
                  </Button>
                </div>
              </div>
            )
          })}

        {/* Expandable check breakdown */}
        {expandedId &&
          report &&
          (() => {
            const entry = report.entries.find((e) => e.file_id === expandedId)
            if (!entry || !entry.check_report) return null
            const cr = entry.check_report
            return (
              <div className="mt-4 rounded-xl border border-border bg-surface p-6 shadow-sm">
                <div className="mb-4 flex items-center justify-between">
                  <h3 className="font-medium text-text-muted">
                    {entry.filename} &mdash; Check Breakdown
                  </h3>
                  <Button variant="ghost" size="sm" onClick={() => setExpandedId(null)}>
                    Close
                  </Button>
                </div>
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
                  <div className="rounded-xl border border-border bg-surface-muted p-3 text-center">
                    <p className="text-xs font-medium uppercase text-text-subtle">Total</p>
                    <p className="mt-1 text-2xl font-bold text-text">
                      {cr.total_checks}
                    </p>
                  </div>
                  <div className="rounded-xl border border-green-200 bg-green-50 p-3 text-center">
                    <p className="text-xs font-medium uppercase text-green-600">Passed</p>
                    <p className="mt-1 text-2xl font-bold text-green-700">{cr.passed}</p>
                  </div>
                  <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-center">
                    <p className="text-xs font-medium uppercase text-red-500">Failed</p>
                    <p className="mt-1 text-2xl font-bold text-red-700">{cr.failed}</p>
                  </div>
                  <div className="rounded-xl border border-yellow-200 bg-yellow-50 p-3 text-center">
                    <p className="text-xs font-medium uppercase text-yellow-600">Errors</p>
                    <p className="mt-1 text-2xl font-bold text-yellow-700">{cr.errors}</p>
                  </div>
                  <div className="rounded-xl border border-blue-200 bg-blue-50 p-3 text-center">
                    <p className="text-xs font-medium uppercase text-blue-600">Pass Rate</p>
                    <p className="mt-1 text-2xl font-bold text-blue-700">
                      {Math.round(cr.pass_rate * 100)}%
                    </p>
                  </div>
                </div>
              </div>
            )
          })()}

        {/* Completed OCR results */}
        {Object.entries(ocrJobs)
          .filter(([, oj]) => oj.status === 'completed' && oj.extracted_markdown)
          .map(([fileIdStr, oj]) => {
            const fileId = Number(fileIdStr)
            return (
              <div key={oj.id} className="mt-4 rounded-xl border border-purple-200 bg-surface p-6 shadow-sm">
                <div className="mb-4 flex items-center justify-between">
                  <h3 className="font-medium text-text-muted">
                    {oj.filename} &mdash; OCR Extracted Text
                  </h3>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      setOcrJobs((prev) => {
                        const next = { ...prev }
                        delete next[fileId]
                        return next
                      })
                    }
                  >
                    Dismiss
                  </Button>
                </div>
                <p className="mb-2 text-xs text-text-subtle">
                  {oj.pages_total} page{oj.pages_total !== 1 ? 's' : ''} extracted
                </p>
                <pre className="max-h-64 overflow-auto rounded-xl border border-border bg-surface-muted p-3 text-xs text-text-muted whitespace-pre-wrap">
                  {oj.extracted_markdown.slice(0, 2000)}
                  {oj.extracted_markdown.length > 2000 ? '\n\n[...truncated]' : ''}
                </pre>
              </div>
            )
          })}

        {report && (
          <p className="mt-4 text-xs text-text-subtle">
            Last audited: {new Date(report.audited_at).toLocaleString()}
          </p>
        )}
    </div>
  )
}
