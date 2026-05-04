import { useState, useEffect, useCallback, useRef } from 'react'
import { useLTI } from '@/contexts/LTIContext'
import { Button, Card, Badge } from '@/components/ui'
import { usePageActions } from '@/components/layout/PageActionsContext'
import {
  getAdminRules,
  updateAdminRule,
  getAdminSettings,
  updateAdminSettings,
  startBatchJob,
  getBatchJob,
  getInstitutionDashboard,
  getRemediationGuides,
  getScheduledScans,
  upsertScheduledScan,
  getCanvasCustomJS,
  getBlueprintInfo,
  triggerBlueprintSync,
  getWebhookStatus,
  type RuleInfo,
  type AdminSettingsData,
  type BatchJobData,
  type InstitutionDashboardData,
  type GuidesResponse,
  type ScheduledScanData,
} from '@/api/client'

type TabId = 'rules' | 'batch' | 'dashboard' | 'guides' | 'scheduled' | 'blueprint' | 'webhooks'

const TABS: { id: TabId; label: string }[] = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'rules', label: 'Rules' },
  { id: 'batch', label: 'Batch' },
  { id: 'guides', label: 'Guides' },
  { id: 'scheduled', label: 'Scheduled' },
  { id: 'blueprint', label: 'Blueprint' },
  { id: 'webhooks', label: 'Webhooks' },
]

const SEVERITY_OPTIONS = [
  { value: '', label: 'Default' },
  { value: 'error', label: 'Error' },
  { value: 'warning', label: 'Warning' },
  { value: 'info', label: 'Info' },
]

const SEVERITY_BADGE_VARIANT: Record<string, 'error' | 'warning' | 'info'> = {
  error: 'error',
  warning: 'warning',
  info: 'info',
}

// ---------------------------------------------------------------------------
// Rules tab
// ---------------------------------------------------------------------------

function RulesTab() {
  const [rules, setRules] = useState<RuleInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState('')

  useEffect(() => {
    getAdminRules()
      .then(setRules)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const handleSeverityChange = useCallback(async (rule: RuleInfo, value: string) => {
    try {
      const updated = await updateAdminRule(rule.rule_id, {
        severity_override: value || null,
        enabled: rule.enabled,
        notes: rule.notes,
      })
      setRules(prev => prev.map(r => (r.rule_id === updated.rule_id ? updated : r)))
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to update rule')
    }
  }, [])

  const handleToggle = useCallback(async (rule: RuleInfo) => {
    try {
      const updated = await updateAdminRule(rule.rule_id, {
        severity_override: rule.severity_override,
        enabled: !rule.enabled,
        notes: rule.notes,
      })
      setRules(prev => prev.map(r => (r.rule_id === updated.rule_id ? updated : r)))
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to toggle rule')
    }
  }, [])

  if (loading) return <p className="text-text-subtle">Loading rules...</p>
  if (error) return <p className="text-red-600">{error}</p>

  const filtered = rules.filter(
    r =>
      r.rule_id.toLowerCase().includes(filter.toLowerCase()) ||
      r.category.toLowerCase().includes(filter.toLowerCase()) ||
      r.wcag_criterion.toLowerCase().includes(filter.toLowerCase())
  )

  return (
    <div>
      <input
        type="text"
        placeholder="Filter rules by ID, category, or WCAG..."
        value={filter}
        onChange={e => setFilter(e.target.value)}
        className="mb-4 w-full rounded-lg border border-border px-3 py-2 text-sm"
      />
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b text-left text-text-subtle">
              <th className="pb-2 pr-4">Rule</th>
              <th className="pb-2 pr-4">Category</th>
              <th className="pb-2 pr-4">WCAG</th>
              <th className="pb-2 pr-4">Default</th>
              <th className="pb-2 pr-4">Override</th>
              <th className="pb-2 pr-4">Auto-Fix</th>
              <th className="pb-2">Enabled</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(rule => (
              <tr key={rule.rule_id} className="border-b hover:bg-surface-muted">
                <td className="py-2 pr-4 font-mono text-xs">{rule.rule_id}</td>
                <td className="py-2 pr-4 capitalize">{rule.category}</td>
                <td className="py-2 pr-4">{rule.wcag_criterion}</td>
                <td className="py-2 pr-4">
                  <Badge variant={SEVERITY_BADGE_VARIANT[rule.severity] ?? 'neutral'}>
                    {rule.severity}
                  </Badge>
                </td>
                <td className="py-2 pr-4">
                  <select
                    value={rule.severity_override ?? ''}
                    onChange={e => handleSeverityChange(rule, e.target.value)}
                    className="rounded-lg border border-border px-2 py-1 text-xs"
                  >
                    {SEVERITY_OPTIONS.map(o => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="py-2 pr-4 text-center">{rule.can_auto_fix ? 'Yes' : '-'}</td>
                <td className="py-2">
                  <Button
                    size="sm"
                    variant={rule.enabled ? 'primary' : 'ghost'}
                    className={rule.enabled ? 'bg-green-600 hover:bg-green-700' : ''}
                    onClick={() => handleToggle(rule)}
                  >
                    {rule.enabled ? 'On' : 'Off'}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-text-subtle">
        {filtered.length} of {rules.length} rules shown
      </p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Batch tab
// ---------------------------------------------------------------------------

function BatchTab() {
  const [courseIds, setCourseIds] = useState('')
  const [operation, setOperation] = useState<'scan' | 'remediate' | 'audit_files'>('scan')
  const [job, setJob] = useState<BatchJobData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const handleStart = useCallback(async () => {
    const ids = courseIds
      .split(/[\s,]+/)
      .map(s => s.trim())
      .filter(Boolean)
    if (ids.length === 0) {
      setError('Enter at least one course ID')
      return
    }
    setError(null)
    setSubmitting(true)
    try {
      const created = await startBatchJob({ course_ids: ids, operation })
      setJob(created)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to start batch')
    } finally {
      setSubmitting(false)
    }
  }, [courseIds, operation])

  useEffect(() => {
    if (!job || job.status === 'completed' || job.status === 'failed') {
      if (pollRef.current) clearInterval(pollRef.current)
      return
    }
    pollRef.current = setInterval(async () => {
      try {
        const updated = await getBatchJob(job.id)
        setJob(updated)
        if (updated.status === 'completed' || updated.status === 'failed') {
          if (pollRef.current) clearInterval(pollRef.current)
        }
      } catch { /* ignore poll errors */ }
    }, 3000)
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [job?.id, job?.status])

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
        <h3 className="mb-3 text-sm font-semibold text-text-muted">New Batch Operation</h3>
        <label className="mb-2 block text-xs text-text-subtle">
          Course IDs (comma or newline separated)
        </label>
        <textarea
          rows={3}
          className="mb-3 w-full rounded-lg border border-border px-3 py-2 text-sm"
          value={courseIds}
          onChange={e => setCourseIds(e.target.value)}
          placeholder="12345, 67890, 11111"
        />
        <div className="mb-3 flex items-center gap-3">
          <label className="text-xs text-text-subtle">Operation:</label>
          <select
            value={operation}
            onChange={e => setOperation(e.target.value as typeof operation)}
            className="rounded-lg border border-border px-2 py-1 text-sm"
          >
            <option value="scan">Scan</option>
            <option value="remediate">Remediate</option>
            <option value="audit_files">Audit Files</option>
          </select>
        </div>
        <Button onClick={handleStart} loading={submitting}>
          Start Batch
        </Button>
        {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
      </div>

      {job && (
        <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
          <h3 className="mb-2 text-sm font-semibold text-text-muted">
            Batch Job: {job.id.slice(0, 8)}...
          </h3>
          <div className="mb-2 flex items-center gap-4 text-sm">
            <span>
              Status:{' '}
              <span
                className={`font-medium ${
                  job.status === 'completed'
                    ? 'text-green-600'
                    : job.status === 'failed'
                    ? 'text-red-600'
                    : 'text-blue-600'
                }`}
              >
                {job.status}
              </span>
            </span>
            <span>
              Progress: {job.courses_completed}/{job.courses_total}
            </span>
          </div>
          <div className="mb-3 h-2 w-full rounded-full bg-gray-200">
            <div
              className="h-2 rounded-full bg-blue-500 transition-all"
              style={{ width: `${job.progress * 100}%` }}
            />
          </div>
          {Object.keys(job.results).length > 0 && (
            <div className="text-xs">
              <p className="mb-1 font-medium text-text-muted">Results:</p>
              {Object.entries(job.results).map(([cid, status]) => (
                <div key={cid} className="flex gap-2">
                  <span className="font-mono">{cid}</span>
                  <span className={status.startsWith('failed') ? 'text-red-600' : 'text-green-600'}>
                    {status}
                  </span>
                </div>
              ))}
            </div>
          )}
          {job.error && <p className="mt-2 text-sm text-red-600">{job.error}</p>}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Dashboard tab
// ---------------------------------------------------------------------------

function DashboardTab() {
  const [data, setData] = useState<InstitutionDashboardData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getInstitutionDashboard()
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <p className="text-text-subtle">Loading dashboard...</p>
  if (error) return <p className="text-red-600">{error}</p>
  if (!data) return null

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
        <Card>
          <div className="text-center">
            <div className="text-2xl font-bold text-text">{data.courses_scanned}</div>
            <div className="text-xs text-text-subtle">Courses Scanned</div>
          </div>
        </Card>
        <Card>
          <div className="text-center">
            <div className="text-2xl font-bold text-text">{data.total_pages_scanned}</div>
            <div className="text-xs text-text-subtle">Pages Scanned</div>
          </div>
        </Card>
        <Card>
          <div className="text-center">
            <div className="text-2xl font-bold text-text">{data.total_issues}</div>
            <div className="text-xs text-text-subtle">Total Issues</div>
          </div>
        </Card>
        <Card>
          <div className="text-center">
            <div className="text-2xl font-bold text-text">{data.total_issues_fixed}</div>
            <div className="text-xs text-text-subtle">Issues Fixed</div>
          </div>
        </Card>
        <Card>
          <div className="text-center">
            <div className="text-2xl font-bold text-brand-primary">{data.avg_score}%</div>
            <div className="text-xs text-text-subtle">Avg Score</div>
          </div>
        </Card>
      </div>

      {data.top_issues.length > 0 && (
        <div>
          <h3 className="mb-2 text-sm font-semibold text-text-muted">Top Issues</h3>
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b text-left text-text-subtle">
                  <th className="pb-2 pr-4">Rule</th>
                  <th className="pb-2 pr-4">Severity</th>
                  <th className="pb-2">Count</th>
                </tr>
              </thead>
              <tbody>
                {data.top_issues.map((issue, i) => (
                  <tr key={i} className="border-b">
                    <td className="py-1 pr-4 font-mono text-xs">{issue.rule_id}</td>
                    <td className="py-1 pr-4">
                      <Badge variant={SEVERITY_BADGE_VARIANT[issue.severity] ?? 'neutral'}>
                        {issue.severity}
                      </Badge>
                    </td>
                    <td className="py-1">{issue.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Guides tab
// ---------------------------------------------------------------------------

function GuidesTab() {
  const [data, setData] = useState<GuidesResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)

  useEffect(() => {
    getRemediationGuides()
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <p className="text-text-subtle">Loading guides...</p>
  if (error) return <p className="text-red-600">{error}</p>
  if (!data) return null

  const entries = Object.entries(data.guides).filter(([ruleId, guide]) => {
    const q = search.toLowerCase()
    return (
      ruleId.toLowerCase().includes(q) ||
      guide.title.toLowerCase().includes(q) ||
      guide.category.toLowerCase().includes(q) ||
      guide.wcag.includes(q)
    )
  })

  return (
    <div>
      <input
        type="text"
        placeholder="Search guides by rule ID, title, category, or WCAG..."
        value={search}
        onChange={e => setSearch(e.target.value)}
        className="mb-4 w-full rounded-lg border border-border px-3 py-2 text-sm"
      />
      <div className="space-y-2">
        {entries.map(([ruleId, guide]) => (
          <div key={ruleId} className="rounded-xl border border-border">
            <button
              onClick={() => setExpanded(expanded === ruleId ? null : ruleId)}
              className="flex w-full items-center justify-between px-4 py-3 text-left text-sm hover:bg-surface-muted"
            >
              <div className="flex items-center gap-3">
                <span className="font-mono text-xs text-text-subtle">{ruleId}</span>
                <span className="font-medium text-text">{guide.title}</span>
                <Badge variant={SEVERITY_BADGE_VARIANT[guide.severity] ?? 'neutral'}>
                  {guide.severity}
                </Badge>
              </div>
              <span className="text-text-subtle">{expanded === ruleId ? '-' : '+'}</span>
            </button>
            {expanded === ruleId && (
              <div className="border-t px-4 py-3 text-sm">
                <p className="mb-2 text-text-muted">{guide.description}</p>
                <p className="mb-1 text-xs font-medium text-text-subtle">WCAG {guide.wcag} | {guide.category}</p>
                <p className="mb-2 text-xs text-text-subtle">
                  Auto-fixable: {guide.auto_fixable ? 'Yes' : 'No'}
                </p>
                <h4 className="mb-1 text-xs font-semibold text-text-muted">Steps:</h4>
                <ol className="mb-3 list-inside list-decimal space-y-1 text-xs text-text-muted">
                  {guide.steps.map((step, i) => (
                    <li key={i}>{step}</li>
                  ))}
                </ol>
                {guide.resources.length > 0 && (
                  <>
                    <h4 className="mb-1 text-xs font-semibold text-text-muted">Resources:</h4>
                    <ul className="list-inside list-disc space-y-1 text-xs">
                      {guide.resources.map((url, i) => (
                        <li key={i}>
                          <a
                            href={url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-blue-600 underline hover:text-blue-800"
                          >
                            {url}
                          </a>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
      <p className="mt-2 text-xs text-text-subtle">
        {entries.length} of {data.total} guides shown
      </p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Scheduled scans tab
// ---------------------------------------------------------------------------

function ScheduledTab() {
  const [scans, setScans] = useState<ScheduledScanData[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [newCourseId, setNewCourseId] = useState('')
  const [settings, setSettings] = useState<AdminSettingsData | null>(null)

  useEffect(() => {
    Promise.all([getScheduledScans(), getAdminSettings()])
      .then(([s, a]) => {
        setScans(s)
        setSettings(a)
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const handleToggle = useCallback(
    async (scan: ScheduledScanData) => {
      try {
        const updated = await upsertScheduledScan(scan.course_id, !scan.enabled)
        setScans(prev => prev.map(s => (s.course_id === updated.course_id ? updated : s)))
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Failed to toggle scan')
      }
    },
    []
  )

  const handleAdd = useCallback(async () => {
    if (!newCourseId.trim()) return
    try {
      const created = await upsertScheduledScan(newCourseId.trim(), true)
      setScans(prev => {
        const existing = prev.find(s => s.course_id === created.course_id)
        if (existing) return prev.map(s => (s.course_id === created.course_id ? created : s))
        return [...prev, created]
      })
      setNewCourseId('')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to add scan')
    }
  }, [newCourseId])

  const handleIntervalChange = useCallback(
    async (hours: number) => {
      try {
        const updated = await updateAdminSettings({ auto_scan_interval_hours: hours })
        setSettings(updated)
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Failed to update interval')
      }
    },
    []
  )

  const handleAutoScanToggle = useCallback(async () => {
    if (!settings) return
    try {
      const updated = await updateAdminSettings({
        auto_scan_enabled: !settings.auto_scan_enabled,
      })
      setSettings(updated)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to toggle auto scan')
    }
  }, [settings])

  if (loading) return <p className="text-text-subtle">Loading scheduled scans...</p>
  if (error) return <p className="text-red-600">{error}</p>

  return (
    <div className="space-y-6">
      {settings && (
        <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
          <h3 className="mb-3 text-sm font-semibold text-text-muted">Global Settings</h3>
          <div className="flex items-center gap-4">
            <Button
              size="sm"
              variant={settings.auto_scan_enabled ? 'primary' : 'ghost'}
              className={settings.auto_scan_enabled ? 'bg-green-600 hover:bg-green-700' : ''}
              onClick={handleAutoScanToggle}
            >
              Auto-Scan: {settings.auto_scan_enabled ? 'Enabled' : 'Disabled'}
            </Button>
            <label className="flex items-center gap-2 text-xs text-text-subtle">
              Interval (hours):
              <input
                type="number"
                min={1}
                max={168}
                value={settings.auto_scan_interval_hours}
                onChange={e => handleIntervalChange(Number(e.target.value))}
                className="w-16 rounded-lg border border-border px-2 py-1 text-xs"
              />
            </label>
          </div>
        </div>
      )}

      <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
        <h3 className="mb-3 text-sm font-semibold text-text-muted">Course Schedules</h3>
        <div className="mb-3 flex gap-2">
          <input
            type="text"
            placeholder="Course ID"
            value={newCourseId}
            onChange={e => setNewCourseId(e.target.value)}
            className="rounded-lg border border-border px-3 py-1 text-sm"
          />
          <Button size="sm" onClick={handleAdd}>
            Add
          </Button>
        </div>
        {scans.length === 0 ? (
          <p className="text-xs text-text-subtle">No scheduled scans configured yet.</p>
        ) : (
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b text-left text-text-subtle">
                <th className="pb-2 pr-4">Course ID</th>
                <th className="pb-2 pr-4">Last Scan</th>
                <th className="pb-2 pr-4">Next Scan</th>
                <th className="pb-2">Enabled</th>
              </tr>
            </thead>
            <tbody>
              {scans.map(scan => (
                <tr key={scan.course_id} className="border-b">
                  <td className="py-2 pr-4 font-mono text-xs">{scan.course_id}</td>
                  <td className="py-2 pr-4 text-xs text-text-subtle">
                    {scan.last_scan_at ? new Date(scan.last_scan_at).toLocaleString() : '-'}
                  </td>
                  <td className="py-2 pr-4 text-xs text-text-subtle">
                    {scan.next_scan_at ? new Date(scan.next_scan_at).toLocaleString() : '-'}
                  </td>
                  <td className="py-2">
                    <Button
                      size="sm"
                      variant={scan.enabled ? 'primary' : 'ghost'}
                      className={scan.enabled ? 'bg-green-600 hover:bg-green-700' : ''}
                      onClick={() => handleToggle(scan)}
                    >
                      {scan.enabled ? 'On' : 'Off'}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Blueprint tab
// ---------------------------------------------------------------------------

function BlueprintTab() {
  const [courseId, setCourseId] = useState('')
  const [info, setInfo] = useState<{ is_blueprint: boolean; associated_courses: string[] } | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [syncResult, setSyncResult] = useState<{ synced: number; errors: string[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [jsSnippet, setJsSnippet] = useState<{ js: string; css: string } | null>(null)

  const handleCheck = useCallback(async () => {
    if (!courseId.trim()) return
    setLoading(true)
    setError(null)
    setInfo(null)
    setSyncResult(null)
    try {
      const data = await getBlueprintInfo(Number(courseId))
      setInfo(data)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to check blueprint')
    } finally {
      setLoading(false)
    }
  }, [courseId])

  const handleSync = useCallback(async () => {
    if (!courseId.trim()) return
    setSyncing(true)
    setError(null)
    setSyncResult(null)
    try {
      const result = await triggerBlueprintSync(Number(courseId))
      setSyncResult(result)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to sync')
    } finally {
      setSyncing(false)
    }
  }, [courseId])

  const handleFetchJS = useCallback(async () => {
    try {
      const data = await getCanvasCustomJS()
      setJsSnippet(data)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to fetch JS snippet')
    }
  }, [])

  return (
    <div className="space-y-6">
      {/* Blueprint check */}
      <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
        <h3 className="mb-3 text-sm font-semibold text-text-muted">Blueprint Course Check</h3>
        <div className="mb-3 flex gap-2">
          <input
            type="text"
            placeholder="Course ID"
            value={courseId}
            onChange={e => setCourseId(e.target.value)}
            className="rounded-lg border border-border px-3 py-1 text-sm"
          />
          <Button size="sm" onClick={handleCheck} loading={loading}>
            Check
          </Button>
        </div>

        {info && (
          <div className="mb-3 rounded-xl bg-surface-muted p-3 text-sm">
            <p>
              Blueprint:{' '}
              <span className={info.is_blueprint ? 'font-medium text-green-700' : 'text-text-subtle'}>
                {info.is_blueprint ? 'Yes' : 'No'}
              </span>
            </p>
            {info.is_blueprint && info.associated_courses.length > 0 && (
              <div className="mt-2">
                <p className="text-xs text-text-subtle">
                  Associated courses: {info.associated_courses.join(', ')}
                </p>
                <Button
                  size="sm"
                  className="mt-2 bg-green-600 hover:bg-green-700"
                  onClick={handleSync}
                  loading={syncing}
                >
                  Trigger Sync
                </Button>
              </div>
            )}
          </div>
        )}

        {syncResult && (
          <div className="rounded-xl bg-green-50 p-3 text-sm text-green-700">
            Sync triggered. {syncResult.errors.length > 0 && `Errors: ${syncResult.errors.join(', ')}`}
          </div>
        )}
      </div>

      {/* Canvas JS injection */}
      <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
        <h3 className="mb-3 text-sm font-semibold text-text-muted">Canvas Custom JS/CSS</h3>
        <p className="mb-3 text-xs text-text-subtle">
          Get the JavaScript snippet to add to your Canvas theme for inline accessibility score badges.
        </p>
        <Button size="sm" onClick={handleFetchJS}>
          Generate Snippet
        </Button>
        {jsSnippet && (
          <div className="mt-3 space-y-3">
            <div>
              <div className="mb-1 flex items-center justify-between">
                <label className="text-xs font-medium text-text-muted">JavaScript</label>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => navigator.clipboard.writeText(jsSnippet.js)}
                >
                  Copy
                </Button>
              </div>
              <pre className="max-h-48 overflow-auto rounded-xl bg-surface-muted p-3 text-xs text-text-muted">
                {jsSnippet.js}
              </pre>
            </div>
            <div>
              <div className="mb-1 flex items-center justify-between">
                <label className="text-xs font-medium text-text-muted">CSS</label>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => navigator.clipboard.writeText(jsSnippet.css)}
                >
                  Copy
                </Button>
              </div>
              <pre className="overflow-auto rounded-xl bg-surface-muted p-3 text-xs text-text-muted">
                {jsSnippet.css}
              </pre>
            </div>
          </div>
        )}
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Webhooks tab
// ---------------------------------------------------------------------------

function WebhooksTab() {
  const [status, setStatus] = useState<{ status: string; endpoint: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getWebhookStatus()
      .then(setStatus)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <p className="text-text-subtle">Loading webhook status...</p>
  if (error) return <p className="text-red-600">{error}</p>

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
        <h3 className="mb-3 text-sm font-semibold text-text-muted">Canvas Webhook Endpoint</h3>
        {status && (
          <div className="space-y-2 text-sm">
            <p>
              Status:{' '}
              <span
                className={`font-medium ${
                  status.status === 'active' ? 'text-green-600' : 'text-text-subtle'
                }`}
              >
                {status.status}
              </span>
            </p>
            <p>
              Endpoint: <code className="rounded bg-surface-muted px-2 py-0.5 text-xs">{status.endpoint}</code>
            </p>
            <div className="mt-4 rounded-xl bg-blue-50 p-3 text-xs text-blue-800">
              <p className="mb-1 font-medium">Setup Instructions:</p>
              <ol className="list-inside list-decimal space-y-1">
                <li>Go to Canvas Admin &gt; Developer Keys &gt; Data Subscriptions</li>
                <li>Create a new subscription for content change events</li>
                <li>Set the delivery URL to your app base URL + <code>{status.endpoint}</code></li>
                <li>Subscribe to: wiki_page_created, wiki_page_updated, assignment_created, assignment_updated</li>
              </ol>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main admin page
// ---------------------------------------------------------------------------

export function AdminPage() {
  const { session } = useLTI()
  const [activeTab, setActiveTab] = useState<TabId>('dashboard')

  // Inject tab-dependent action into TopBar
  usePageActions(
    activeTab === 'dashboard' ? (
      <span className="text-xs text-text-subtle">Institution overview</span>
    ) : activeTab === 'rules' ? (
      <span className="text-xs text-text-subtle">Manage WCAG rules</span>
    ) : activeTab === 'batch' ? (
      <span className="text-xs text-text-subtle">Batch operations</span>
    ) : activeTab === 'scheduled' ? (
      <span className="text-xs text-text-subtle">Scheduled scans</span>
    ) : null,
  )

  // Admin pages require admin role
  if (session && !session.isAdmin && !session.isInstructor) {
    return (
      <div className="flex items-center justify-center py-12">
        <p className="text-red-600">Admin access required.</p>
      </div>
    )
  }

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold text-text">Admin Panel</h1>

      {/* Tab navigation */}
      <div className="mb-6 flex gap-1 rounded-xl border border-border bg-surface p-1 shadow-sm">
        {TABS.map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
              activeTab === tab.id
                ? 'bg-brand-primary text-white'
                : 'text-text-subtle hover:bg-surface-muted hover:text-text-muted'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === 'dashboard' && <DashboardTab />}
      {activeTab === 'rules' && <RulesTab />}
      {activeTab === 'batch' && <BatchTab />}
      {activeTab === 'guides' && <GuidesTab />}
      {activeTab === 'scheduled' && <ScheduledTab />}
      {activeTab === 'blueprint' && <BlueprintTab />}
      {activeTab === 'webhooks' && <WebhooksTab />}
    </div>
  )
}
