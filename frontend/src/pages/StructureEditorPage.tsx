import { useState, useEffect, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useLTI } from '@/contexts/LTIContext'
import { Button, Badge, Spinner } from '@/components/ui'
import {
  getPDFStructure,
  updatePDFStructure,
  type StructureResponse,
  type StructureNode,
  type StructureEditItem,
} from '@/api/client'

const TAG_OPTIONS = [
  'P', 'Span',
  'H1', 'H2', 'H3', 'H4', 'H5', 'H6',
  'Table', 'TR', 'TH', 'TD',
  'L', 'LI', 'Lbl', 'LBody',
  'Figure', 'Caption',
  'Link',
  'BlockQuote', 'Code',
  'Div', 'Sect', 'Part',
  'Artifact',
]

const HEADING_TAGS = new Set(['H1', 'H2', 'H3', 'H4', 'H5', 'H6'])

function tagColor(tag: string): string {
  if (HEADING_TAGS.has(tag)) return 'text-blue-700 bg-blue-50'
  if (tag === 'P' || tag === 'Span') return 'text-text-muted bg-surface-muted'
  if (tag === 'Table' || tag === 'TR' || tag === 'TH' || tag === 'TD')
    return 'text-purple-700 bg-purple-50'
  if (tag === 'L' || tag === 'LI' || tag === 'Lbl' || tag === 'LBody')
    return 'text-orange-700 bg-orange-50'
  if (tag === 'Figure') return 'text-green-700 bg-green-50'
  if (tag === 'Link') return 'text-cyan-700 bg-cyan-50'
  if (tag === 'Artifact') return 'text-text-subtle bg-surface-muted italic'
  return 'text-text-muted bg-surface-muted'
}

const SEVERITY_BADGE_VARIANT: Record<string, 'error' | 'warning' | 'info'> = {
  error: 'error',
  warning: 'warning',
  info: 'info',
}

function TreeNode({
  node,
  editedTag,
  onChangeTag,
}: {
  node: StructureNode
  editedTag: string | undefined
  onChangeTag: (index: number, tag: string) => void
}) {
  const currentTag = editedTag ?? node.tag
  const indent = node.depth * 20
  const textPreview = node.alt_text
    ? `[alt: ${node.alt_text.slice(0, 80)}]`
    : node.text
      ? node.text.slice(0, 120)
      : ''

  return (
    <div
      className="flex items-center gap-2 border-b border-border-muted py-1.5 hover:bg-surface-muted"
      style={{ paddingLeft: `${indent + 8}px` }}
    >
      <select
        value={currentTag}
        onChange={(e) => onChangeTag(node.index, e.target.value)}
        className={`rounded px-2 py-0.5 text-xs font-mono font-semibold ${tagColor(currentTag)} border border-border`}
        aria-label={`Tag type for node ${node.index}`}
      >
        {TAG_OPTIONS.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
        {/* If current tag isn't in options, show it */}
        {!TAG_OPTIONS.includes(currentTag) && (
          <option value={currentTag}>{currentTag}</option>
        )}
      </select>

      <span className="text-xs text-text-subtle tabular-nums">p{node.page + 1}</span>

      {textPreview && (
        <span className="truncate text-xs text-text-muted" title={textPreview}>
          {textPreview}
        </span>
      )}

      {editedTag && editedTag !== node.tag && (
        <span className="ml-auto whitespace-nowrap rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-700">
          was {node.tag}
        </span>
      )}
    </div>
  )
}

export function StructureEditorPage() {
  const { fileId } = useParams<{ fileId: string }>()
  const { session } = useLTI()
  const courseId = session?.canvasCourseId

  const [structure, setStructure] = useState<StructureResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [edits, setEdits] = useState<Record<number, string>>({})
  const [saving, setSaving] = useState(false)
  const [saveResult, setSaveResult] = useState<string | null>(null)
  const [filterPage, setFilterPage] = useState<number | null>(null)

  useEffect(() => {
    if (!courseId || !fileId) return
    setLoading(true)
    setError(null)
    getPDFStructure(courseId, Number(fileId))
      .then(setStructure)
      .catch((err) => setError(err.message || 'Failed to load structure'))
      .finally(() => setLoading(false))
  }, [courseId, fileId])

  const handleChangeTag = useCallback(
    (index: number, tag: string) => {
      setEdits((prev) => {
        const node = structure?.nodes.find((n) => n.index === index)
        // If tag is same as original, remove the edit
        if (node && tag === node.tag) {
          const next = { ...prev }
          delete next[index]
          return next
        }
        return { ...prev, [index]: tag }
      })
      setSaveResult(null)
    },
    [structure],
  )

  const handleSave = useCallback(async () => {
    if (!courseId || !fileId || Object.keys(edits).length === 0) return
    setSaving(true)
    setSaveResult(null)

    const editItems: StructureEditItem[] = Object.entries(edits).map(
      ([idx, tag]) => ({ node_index: Number(idx), new_tag: tag }),
    )

    try {
      const result = await updatePDFStructure(courseId, Number(fileId), editItems)
      setSaveResult(
        `Applied ${result.edits_applied} edit(s).` +
          (result.errors.length > 0
            ? ` Errors: ${result.errors.join('; ')}`
            : ''),
      )
      setEdits({})
      // Reload structure
      const updated = await getPDFStructure(courseId, Number(fileId))
      setStructure(updated)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Save failed'
      setSaveResult(`Error: ${msg}`)
    } finally {
      setSaving(false)
    }
  }, [courseId, fileId, edits])

  const pendingCount = Object.keys(edits).length

  const filteredNodes = structure
    ? filterPage !== null
      ? structure.nodes.filter((n) => n.page === filterPage)
      : structure.nodes
    : []

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Spinner size="lg" label="Loading PDF structure" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center gap-4 py-12">
        <p className="text-red-600">{error}</p>
        <Link
          to="/files"
          className="text-sm font-medium text-brand-primary hover:underline"
        >
          Back to Files
        </Link>
      </div>
    )
  }

  return (
    <div>
        {/* Issues panel */}
        {structure && structure.issues.length > 0 && (
          <div className="mb-6 rounded-xl border border-border bg-surface p-4 shadow-sm">
            <h2 className="mb-3 text-sm font-semibold text-text-muted">
              Validation Issues ({structure.issues.length})
            </h2>
            <div className="max-h-48 space-y-1.5 overflow-y-auto">
              {structure.issues.map((issue, i) => (
                <div key={i} className="flex items-start gap-2 text-xs">
                  <Badge variant={SEVERITY_BADGE_VARIANT[issue.severity] ?? 'info'}>
                    {issue.severity}
                  </Badge>
                  <span className="font-mono text-text-subtle">
                    {issue.location}
                  </span>
                  <span className="text-text-muted">{issue.description}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {!structure?.has_structure_tree ? (
          <div className="rounded-xl border border-yellow-200 bg-yellow-50 p-6 text-center">
            <p className="text-yellow-800">
              This PDF has no structure tree. It is invisible to screen readers
              and cannot be edited here.
            </p>
          </div>
        ) : (
          <>
            {/* Toolbar */}
            <div className="mb-4 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <label
                  htmlFor="page-filter"
                  className="text-sm text-text-muted"
                >
                  Filter by page:
                </label>
                <select
                  id="page-filter"
                  value={filterPage ?? ''}
                  onChange={(e) =>
                    setFilterPage(
                      e.target.value === '' ? null : Number(e.target.value),
                    )
                  }
                  className="rounded-lg border border-border px-3 py-1.5 text-sm"
                >
                  <option value="">All pages</option>
                  {Array.from(
                    { length: structure?.page_count ?? 0 },
                    (_, i) => (
                      <option key={i} value={i}>
                        Page {i + 1}
                      </option>
                    ),
                  )}
                </select>
                <span className="text-xs text-text-subtle">
                  {filteredNodes.length} node(s) shown
                </span>
              </div>

              <div className="flex items-center gap-3">
                {pendingCount > 0 && (
                  <span className="text-sm text-amber-600">
                    {pendingCount} unsaved edit(s)
                  </span>
                )}
                <Button
                  onClick={handleSave}
                  disabled={pendingCount === 0 || saving}
                  loading={saving}
                >
                  Save Changes
                </Button>
              </div>
            </div>

            {saveResult && (
              <div
                className={`mb-4 rounded-lg p-3 text-sm ${
                  saveResult.startsWith('Error')
                    ? 'border border-red-200 bg-red-50 text-red-700'
                    : 'border border-green-200 bg-green-50 text-green-700'
                }`}
              >
                {saveResult}
              </div>
            )}

            {/* Tag tree */}
            <div className="rounded-xl border border-border bg-surface shadow-sm">
              <div className="max-h-[70vh] overflow-y-auto">
                {filteredNodes.map((node) => (
                  <TreeNode
                    key={node.index}
                    node={node}
                    editedTag={edits[node.index]}
                    onChangeTag={handleChangeTag}
                  />
                ))}
                {filteredNodes.length === 0 && (
                  <div className="p-8 text-center text-sm text-text-subtle">
                    No nodes to display.
                  </div>
                )}
              </div>
            </div>
          </>
        )}
    </div>
  )
}
