/**
 * Parse an ISO 8601 string defensively as UTC when no timezone is present.
 *
 * Background (CLU-77): Some backend timestamps in our database are stored as
 * naive datetimes (no `Z` or offset suffix) — e.g. `2026-04-08T04:38:03.629302`.
 * `new Date(...)` interprets timezone-less ISO strings as **local time**, which
 * causes timestamps written by the server in UTC to appear shifted by the
 * user's timezone offset (7 hours in PDT). Comparisons against
 * properly-tagged UTC timestamps then break.
 *
 * `parseIsoUtc` adds a `Z` suffix to any ISO string that doesn't already have
 * a timezone designator (`Z` or `±HH:MM`/`±HHMM`), so the value is parsed as
 * UTC instead of local time.
 *
 * Backend has been fixed forward (analyzer.py uses `datetime.now(UTC)`), but
 * legacy rows in `scan_reports` still hold naive timestamps — this helper
 * keeps them rendering correctly.
 */
export function parseIsoUtc(value: string | null | undefined): Date {
  if (!value) return new Date(NaN)
  // Detect existing timezone designator: trailing Z, or ±HH:MM / ±HHMM after the time portion.
  const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(value)
  return new Date(hasTz ? value : `${value}Z`)
}
