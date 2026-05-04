import { useEffect, useState } from 'react'
import { Outlet } from 'react-router-dom'
import { useLTI } from '@/contexts/LTIContext'
import { SelectionProvider } from '@/contexts/SelectionContext'
import { getReport, type ReportSummary } from '@/api/client'

/**
 * CLU-85 Task 12: route-level wrapper that mounts SelectionProvider
 * keyed by the current scan_report_id. Used around content-type
 * review views and the confirmation modal so they share ephemeral
 * selection state keyed to the user's latest scan.
 *
 * Until the scan report resolves, the provider renders with an
 * empty scanReportId — children that consume useSelection() are
 * safe to mount but their state is not persisted to localStorage
 * (SelectionContext short-circuits persistence when the id is
 * empty). When the report arrives the provider re-hydrates.
 *
 * file_report_id is left null until a second endpoint surfaces it.
 * Tier 1 ephemeral selections for files piggyback on the scan
 * report id for now.
 */
export function SelectionProviderRoute() {
  const { session } = useLTI()
  const [report, setReport] = useState<ReportSummary | null>(null)

  useEffect(() => {
    if (!session?.canvasCourseId) return
    let cancelled = false
    getReport(session.canvasCourseId)
      .then((r) => {
        if (!cancelled) setReport(r)
      })
      .catch(() => {
        if (!cancelled) setReport(null)
      })
    return () => {
      cancelled = true
    }
  }, [session?.canvasCourseId])

  return (
    <SelectionProvider scanReportId={report?.id ?? ''} fileReportId={null}>
      <Outlet />
    </SelectionProvider>
  )
}
