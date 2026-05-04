import { useState, useCallback, useRef } from 'react'
import { Button, Card, Badge, Spinner } from '@/components/ui'

interface DocHubResult {
  id: string
  filename: string
  file_type: string
  size: number
  action: string
  checks_before: { passed: number; failed: number; total: number; pass_rate: number } | null
  checks_after: { passed: number; failed: number; total: number; pass_rate: number } | null
  fixes_applied: string[]
  converted_html: string
  extracted_markdown: string
}

export function DocHubPage() {
  const [result, setResult] = useState<DocHubResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dragActive, setDragActive] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleUpload = useCallback(async (file: File) => {
    setLoading(true)
    setError(null)
    setResult(null)

    const formData = new FormData()
    formData.append('file', file)

    try {
      const resp = await fetch('/api/dochub/upload', {
        method: 'POST',
        body: formData,
        credentials: 'include',
      })
      if (!resp.ok) {
        const text = await resp.text()
        throw new Error(text || resp.statusText)
      }
      const data: DocHubResult = await resp.json()
      setResult(data)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setLoading(false)
    }
  }, [])

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setDragActive(false)
      const file = e.dataTransfer.files[0]
      if (file) handleUpload(file)
    },
    [handleUpload],
  )

  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0]
      if (file) handleUpload(file)
    },
    [handleUpload],
  )

  return (
    <div className="mx-auto max-w-4xl">
      <h1 className="mb-2 text-2xl font-bold text-text">DocHub</h1>
      <p className="mb-6 text-sm text-text-subtle">
        Upload a document for standalone accessibility check and remediation. No Canvas required.
      </p>

      {/* Drop zone */}
      <div
        onDragOver={e => {
          e.preventDefault()
          setDragActive(true)
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        className={`mb-6 flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed p-12 transition ${
          dragActive
            ? 'border-blue-500 bg-blue-50'
            : 'border-border bg-surface-muted hover:border-gray-400'
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.docx,.pptx,.xlsx"
          onChange={handleFileChange}
          className="hidden"
        />
        {loading ? (
          <div className="flex items-center gap-2">
            <Spinner size="sm" />
            <span className="text-sm font-medium text-text-muted">Processing...</span>
          </div>
        ) : (
          <p className="text-sm font-medium text-text-muted">
            Drop a file here or click to upload
          </p>
        )}
        <p className="mt-1 text-xs text-text-subtle">Supported: PDF, DOCX, PPTX, XLSX</p>
      </div>

      {error && (
        <div className="mb-6 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {result && <ResultCard result={result} />}
    </div>
  )
}

function ResultCard({ result }: { result: DocHubResult }) {
  const [showHtml, setShowHtml] = useState(false)

  const actionVariant: 'success' | 'info' | 'neutral' =
    result.action === 'fixed'
      ? 'success'
      : result.action === 'converted'
        ? 'info'
        : 'neutral'

  return (
    <Card>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-text">{result.filename}</h2>
          <p className="text-xs text-text-subtle">
            {result.file_type.toUpperCase()} &middot; {(result.size / 1024).toFixed(1)} KB &middot;
            Action: {result.action}
          </p>
        </div>
        <Badge variant={actionVariant}>
          {result.action}
        </Badge>
      </div>

      {/* PDF results */}
      {result.checks_before && (
        <div className="mb-4 grid grid-cols-2 gap-4">
          <CheckCard label="Before Fix" data={result.checks_before} />
          {result.checks_after && <CheckCard label="After Fix" data={result.checks_after} />}
        </div>
      )}

      {result.fixes_applied.length > 0 && (
        <div className="mb-4">
          <h3 className="mb-2 text-sm font-semibold text-text-muted">Fixes Applied</h3>
          <ul className="list-inside list-disc space-y-1 text-xs text-text-muted">
            {result.fixes_applied.map((fix, i) => (
              <li key={i}>{fix}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Document conversion results */}
      {result.converted_html && (
        <div className="mb-4">
          <div className="mb-2 flex items-center gap-3">
            <h3 className="text-sm font-semibold text-text-muted">Converted HTML</h3>
            <Button variant="ghost" size="sm" onClick={() => setShowHtml(!showHtml)}>
              {showHtml ? 'Hide Source' : 'Show Source'}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => navigator.clipboard.writeText(result.converted_html)}
            >
              Copy HTML
            </Button>
          </div>
          {showHtml && (
            <pre className="max-h-64 overflow-auto rounded-xl bg-surface-muted p-3 text-xs text-text-muted">
              {result.converted_html}
            </pre>
          )}
        </div>
      )}

      {result.extracted_markdown && (
        <div>
          <h3 className="mb-2 text-sm font-semibold text-text-muted">Extracted Markdown</h3>
          <pre className="max-h-48 overflow-auto rounded-xl bg-surface-muted p-3 text-xs text-text-muted">
            {result.extracted_markdown}
          </pre>
        </div>
      )}
    </Card>
  )
}

function CheckCard({
  label,
  data,
}: {
  label: string
  data: { passed: number; failed: number; total: number; pass_rate: number }
}) {
  return (
    <Card>
      <h4 className="mb-2 text-xs font-semibold text-text-subtle">{label}</h4>
      <div className="text-2xl font-bold text-text">{data.pass_rate.toFixed(0)}%</div>
      <div className="mt-1 text-xs text-text-subtle">
        {data.passed} passed / {data.failed} failed / {data.total} total
      </div>
    </Card>
  )
}
