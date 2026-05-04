import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useLTI } from '@/contexts/LTIContext'
import { useSelection } from '@/contexts/SelectionContext'
import { Badge, Button, Card } from '@/components/ui'
import {
  getConversionCandidates,
  addExclusion,
  removeExclusion,
  type ConversionCandidate,
} from '@/api/client'
import {
  FolderOpen,
  CheckCircle2,
  MoreVertical,
  Info,
} from 'lucide-react'

const CONTENT_TYPE = 'file'

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/**
 * CLU-85 Files review view. Separate from ContentTypeIssuePage
 * because files have different semantics — every phase-4-eligible
 * file is shown regardless of audit status, not just files with
 * accessibility issues. Data comes from GET
 * /api/courses/{id}/autoremedy/conversion-candidates.
 */
export function FilesContentView() {
  const { session } = useLTI()
  const navigate = useNavigate()
  const {
    isUnchecked,
    isRechecked,
    markUnchecked,
    markRechecked,
    markContentTypeTouched,
  } = useSelection()

  const [candidates, setCandidates] = useState<ConversionCandidate[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [openMenu, setOpenMenu] = useState<number | null>(null)

  const courseId = session?.canvasCourseId

  useEffect(() => {
    if (!courseId) return
    setLoading(true)
    setError(null)
    getConversionCandidates(courseId)
      .then((res) => {
        setCandidates(res.candidates)
        markContentTypeTouched(CONTENT_TYPE)
      })
      .catch((err) =>
        setError(err instanceof Error ? err.message : 'Failed to load files'),
      )
      .finally(() => setLoading(false))
  }, [courseId, markContentTypeTouched])

  const isFileSelected = useCallback(
    (c: ConversionCandidate): boolean => {
      // Hard excluded: never selectable
      if (c.hard_excluded) return false
      const fileIdentifier = `file-${c.file_id}`
      // Explicit re-check overrides default-off
      if (isRechecked(CONTENT_TYPE, fileIdentifier)) return true
      // Explicit uncheck overrides default-on
      if (isUnchecked(CONTENT_TYPE, fileIdentifier)) return false
      // Permanent exclusions: default-off unless re-checked this run
      if (c.permanently_excluded) return false
      // Otherwise use the server's default
      return c.default_selected
    },
    [isRechecked, isUnchecked],
  )

  const toggleFile = useCallback(
    (c: ConversionCandidate) => {
      if (c.hard_excluded) return
      const fileIdentifier = `file-${c.file_id}`
      if (isFileSelected(c)) {
        markUnchecked(CONTENT_TYPE, fileIdentifier)
      } else {
        markRechecked(CONTENT_TYPE, fileIdentifier)
      }
    },
    [isFileSelected, markUnchecked, markRechecked],
  )

  const handleExcludePermanently = useCallback(
    async (c: ConversionCandidate) => {
      if (!courseId) return
      try {
        await addExclusion(courseId, {
          item_identifier: `file-${c.file_id}`,
          item_type: 'file',
        })
        setCandidates((prev) =>
          prev.map((x) =>
            x.file_id === c.file_id
              ? { ...x, permanently_excluded: true }
              : x,
          ),
        )
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        alert(`Failed to exclude: ${msg}`)
      }
      setOpenMenu(null)
    },
    [courseId],
  )

  const handleRemoveExclusion = useCallback(
    async (c: ConversionCandidate) => {
      if (!courseId) return
      try {
        await removeExclusion(courseId, `file-${c.file_id}`)
        setCandidates((prev) =>
          prev.map((x) =>
            x.file_id === c.file_id
              ? { ...x, permanently_excluded: false }
              : x,
          ),
        )
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        alert(`Failed to remove exclusion: ${msg}`)
      }
      setOpenMenu(null)
    },
    [courseId],
  )

  const selectedCount = candidates.filter(isFileSelected).length

  return (
    <div>
      <div className="mb-6 flex items-center gap-3">
        <FolderOpen
          className="h-6 w-6 text-brand-primary"
          aria-hidden="true"
        />
        <div>
          <h2 className="text-xl font-bold text-text">Files</h2>
          <p className="text-sm text-text-subtle">
            Files that AutoRemedy would convert to accessible pages
          </p>
        </div>
        {!loading && (
          <Badge
            variant={candidates.length === 0 ? 'success' : 'neutral'}
            className="ml-auto"
          >
            {candidates.length} convertible files
          </Badge>
        )}
      </div>

      {error && (
        <Card className="mb-4 text-sm text-red-600" role="alert">
          {error}
        </Card>
      )}

      {loading ? (
        <Card className="text-center">
          <div
            className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-brand-primary border-t-transparent"
            role="status"
            aria-label="Loading files"
          />
          <p className="mt-3 text-sm text-text-subtle">Loading files…</p>
        </Card>
      ) : candidates.length === 0 ? (
        <Card className="text-center">
          <CheckCircle2
            className="mx-auto h-12 w-12 text-green-500"
            aria-hidden="true"
          />
          <h3 className="mt-3 text-lg font-semibold text-text">
            No convertible files
          </h3>
          <p className="mt-1 text-sm text-text-subtle">
            No PDFs, Word documents, or slide decks were found that need
            conversion.
          </p>
        </Card>
      ) : (
        <>
          <div className="mb-4 flex items-center justify-between">
            <p className="text-sm text-text-muted">
              {selectedCount} of {candidates.length} selected for conversion
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
                    Filename
                  </th>
                  <th
                    className="px-4 py-3 font-medium text-text-subtle"
                    scope="col"
                  >
                    Size
                  </th>
                  <th
                    className="px-4 py-3 font-medium text-text-subtle"
                    scope="col"
                  >
                    Reason
                  </th>
                  <th className="w-10 px-4 py-3" scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {candidates.map((c) => {
                  const selected = isFileSelected(c)
                  const rowClass = c.hard_excluded
                    ? 'bg-surface-muted text-text-subtle'
                    : selected
                      ? ''
                      : 'opacity-60'
                  return (
                    <tr key={c.file_id} className={rowClass}>
                      <td className="px-4 py-3">
                        <input
                          type="checkbox"
                          checked={selected}
                          onChange={() => toggleFile(c)}
                          disabled={c.hard_excluded}
                          aria-label={`Select ${c.filename}`}
                        />
                      </td>
                      <td className="px-4 py-3">
                        {c.filename}
                        {c.permanently_excluded && (
                          <span className="ml-2 text-xs text-amber-700">
                            (permanently excluded)
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-text-subtle">
                        {formatSize(c.size_bytes)}
                        {c.page_count != null && (
                          <span className="ml-1 text-xs">
                            ({c.page_count}p)
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs">
                        {c.exclude_reason && (
                          <span className="flex items-center gap-1 text-text-muted">
                            <Info size={12} aria-hidden="true" />
                            {c.exclude_reason}
                          </span>
                        )}
                      </td>
                      <td className="relative px-4 py-3">
                        <button
                          onClick={() =>
                            setOpenMenu(
                              openMenu === c.file_id ? null : c.file_id,
                            )
                          }
                          className="rounded p-1 hover:bg-surface-muted"
                          aria-label="Actions"
                          disabled={c.hard_excluded}
                        >
                          <MoreVertical size={16} aria-hidden="true" />
                        </button>
                        {openMenu === c.file_id && (
                          <div className="absolute right-0 top-8 z-10 w-56 rounded border border-border bg-surface shadow-lg">
                            {c.permanently_excluded ? (
                              <button
                                onClick={() => handleRemoveExclusion(c)}
                                className="block w-full px-4 py-2 text-left text-sm hover:bg-surface-muted"
                              >
                                Remove permanent exclusion
                              </button>
                            ) : (
                              <button
                                onClick={() => handleExcludePermanently(c)}
                                className="block w-full px-4 py-2 text-left text-sm hover:bg-surface-muted"
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
