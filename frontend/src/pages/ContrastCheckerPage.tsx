import { useState, useEffect, useCallback } from 'react'
import { apiClient } from '@/api/client'
import { Card, Badge as SharedBadge } from '@/components/ui'

interface ContrastResult {
  ratio: number
  aa_pass: boolean
  aaa_pass: boolean
  large_text: boolean
  aa_threshold: number
  aaa_threshold: number
  suggestion: string
}

export function ContrastCheckerPage() {
  const [foreground, setForeground] = useState('#333333')
  const [background, setBackground] = useState('#FFFFFF')
  const [fontSize, setFontSize] = useState(16)
  const [isBold, setIsBold] = useState(false)
  const [result, setResult] = useState<ContrastResult | null>(null)

  const checkContrast = useCallback(async () => {
    try {
      const data = await apiClient.post<ContrastResult>(
        '/accessibility/contrast-check',
        {
          foreground,
          background,
          font_size: fontSize,
          is_bold: isBold,
        },
      )
      setResult(data)
    } catch {
      // Silently handle errors during live check
    }
  }, [foreground, background, fontSize, isBold])

  useEffect(() => {
    const timeout = setTimeout(checkContrast, 200)
    return () => clearTimeout(timeout)
  }, [checkContrast])

  const ratioColor =
    result && result.aa_pass
      ? 'text-green-600'
      : result
        ? 'text-red-600'
        : 'text-text-subtle'

  return (
    <div className="mx-auto max-w-3xl">
        <Card>
          {/* Color pickers */}
          <div className="grid grid-cols-2 gap-6">
            <div>
              <label
                htmlFor="fg-color"
                className="mb-2 block text-sm font-medium text-text-muted"
              >
                Foreground (text) color
              </label>
              <div className="flex items-center gap-3">
                <input
                  id="fg-color"
                  type="color"
                  value={foreground}
                  onChange={(e) => setForeground(e.target.value)}
                  className="h-10 w-14 cursor-pointer rounded border border-border"
                />
                <input
                  type="text"
                  value={foreground}
                  onChange={(e) => setForeground(e.target.value)}
                  className="w-28 rounded-lg border border-border px-3 py-2 text-sm font-mono"
                  maxLength={7}
                  aria-label="Foreground hex color"
                />
              </div>
            </div>
            <div>
              <label
                htmlFor="bg-color"
                className="mb-2 block text-sm font-medium text-text-muted"
              >
                Background color
              </label>
              <div className="flex items-center gap-3">
                <input
                  id="bg-color"
                  type="color"
                  value={background}
                  onChange={(e) => setBackground(e.target.value)}
                  className="h-10 w-14 cursor-pointer rounded border border-border"
                />
                <input
                  type="text"
                  value={background}
                  onChange={(e) => setBackground(e.target.value)}
                  className="w-28 rounded-lg border border-border px-3 py-2 text-sm font-mono"
                  maxLength={7}
                  aria-label="Background hex color"
                />
              </div>
            </div>
          </div>

          {/* Font size + bold */}
          <div className="mt-6 flex items-center gap-6">
            <div>
              <label
                htmlFor="font-size"
                className="mb-1 block text-sm font-medium text-text-muted"
              >
                Font size (pt)
              </label>
              <input
                id="font-size"
                type="number"
                min={1}
                max={200}
                value={fontSize}
                onChange={(e) => setFontSize(Number(e.target.value))}
                className="w-24 rounded-lg border border-border px-3 py-2 text-sm"
              />
            </div>
            <div className="flex items-center gap-2 pt-5">
              <input
                id="is-bold"
                type="checkbox"
                checked={isBold}
                onChange={(e) => setIsBold(e.target.checked)}
                className="h-4 w-4 rounded border-border text-brand-primary focus:ring-brand-primary"
              />
              <label htmlFor="is-bold" className="text-sm text-text-muted">
                Bold
              </label>
            </div>
          </div>

          {/* Results */}
          {result && (
            <div className="mt-8 space-y-6">
              {/* Ratio display */}
              <div className="text-center">
                <p className="text-sm font-medium uppercase tracking-wide text-text-subtle">
                  Contrast Ratio
                </p>
                <p className={`mt-1 text-5xl font-bold ${ratioColor}`}>
                  {result.ratio}:1
                </p>
                {result.large_text && (
                  <p className="mt-1 text-xs text-text-subtle">
                    Large text (lower threshold applies)
                  </p>
                )}
              </div>

              {/* Pass/fail badges */}
              <div className="flex justify-center gap-4">
                <SharedBadge variant={result.aa_pass ? 'success' : 'error'}>
                  {result.aa_pass ? '\u2713' : '\u2717'} AA ({result.aa_threshold}:1)
                </SharedBadge>
                <SharedBadge variant={result.aaa_pass ? 'success' : 'error'}>
                  {result.aaa_pass ? '\u2713' : '\u2717'} AAA ({result.aaa_threshold}:1)
                </SharedBadge>
              </div>

              {/* Suggestion */}
              {result.suggestion && (
                <div className="rounded-lg border border-yellow-200 bg-yellow-50 p-4">
                  <p className="text-sm text-yellow-800">{result.suggestion}</p>
                </div>
              )}

              {/* Preview */}
              <div className="mt-6">
                <p className="mb-2 text-sm font-medium text-text-muted">
                  Preview
                </p>
                <div
                  className="rounded-lg border border-border p-6"
                  style={{ backgroundColor: background }}
                >
                  <p
                    className="text-lg"
                    style={{
                      color: foreground,
                      fontSize: `${fontSize}pt`,
                      fontWeight: isBold ? 'bold' : 'normal',
                    }}
                  >
                    The quick brown fox jumps over the lazy dog.
                  </p>
                  <p
                    className="mt-2 text-sm"
                    style={{ color: foreground }}
                  >
                    WCAG 2.2 requires sufficient contrast between foreground
                    and background colors so that text is readable by people
                    with low vision.
                  </p>
                </div>
              </div>
            </div>
          )}
        </Card>
    </div>
  )
}
