const API_BASE = '/api'

let _sessionToken: string | null = null

export function setToken(token: string): void {
  _sessionToken = token
}

export function clearToken(): void {
  _sessionToken = null
}

// --- CLU-84: Session expiration tracking ---
// When any API call returns 401, the session has expired. Polling pages
// subscribe to this flag to show a "session expired" banner and stop
// further polling.

let _sessionExpired = false
const _sessionExpiredListeners: Set<(expired: boolean) => void> = new Set()

/** Returns true if a 401 has been received from the API. */
export function isSessionExpired(): boolean {
  return _sessionExpired
}

/** Subscribe to session-expired state changes. Returns an unsubscribe function. */
export function onSessionExpired(listener: (expired: boolean) => void): () => void {
  _sessionExpiredListeners.add(listener)
  return () => { _sessionExpiredListeners.delete(listener) }
}

function _markSessionExpired(): void {
  if (_sessionExpired) return // already fired
  _sessionExpired = true
  _sessionExpiredListeners.forEach((fn) => fn(true))
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

interface ApiOptions extends RequestInit {
  params?: Record<string, string>
}

async function request<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const { params, ...fetchOptions } = options

  const url = new URL(`${API_BASE}${path}`, window.location.origin)
  if (params) {
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v))
  }

  const headers = new Headers(fetchOptions.headers)
  if (!headers.has('Content-Type') && fetchOptions.body) {
    headers.set('Content-Type', 'application/json')
  }
  if (_sessionToken) {
    headers.set('Authorization', `Bearer ${_sessionToken}`)
  }

  const response = await fetch(url.toString(), {
    ...fetchOptions,
    headers,
    credentials: 'include',
  })

  if (!response.ok) {
    // CLU-84: detect session expiration on any 401 response
    if (response.status === 401) {
      _markSessionExpired()
    }
    const text = await response.text().catch(() => response.statusText)
    throw new ApiError(response.status, text)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return response.json() as Promise<T>
}

export const apiClient = {
  get: <T>(path: string, options?: ApiOptions) =>
    request<T>(path, { method: 'GET', ...options }),

  post: <T>(path: string, body?: unknown, options?: ApiOptions) =>
    request<T>(path, {
      method: 'POST',
      body: body !== undefined ? JSON.stringify(body) : undefined,
      ...options,
    }),

  put: <T>(path: string, body?: unknown, options?: ApiOptions) =>
    request<T>(path, {
      method: 'PUT',
      body: body !== undefined ? JSON.stringify(body) : undefined,
      ...options,
    }),

  delete: (path: string, options?: ApiOptions) =>
    request<void>(path, { method: 'DELETE', ...options }),
}

// --- Phase 2: Scan + Report types ---

export interface ScanJob {
  id: string
  course_id: string
  session_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  progress: number
  pages_total: number
  pages_scanned: number
  created_at: string
  completed_at: string | null
  error: string | null
  report_id: string | null
  phase?: string             // "" | "content" | "rendered" | "merging"
  queue_position?: number    // 0 = running, 1+ = waiting
  current_page?: string
  scan_mode?: string
}

export interface ContentTypeBreakdown {
  errors: number
  warnings: number
  info: number
  total: number
}

export interface ReportSummary {
  // Optional scan_report_id the report was loaded from. Backend wiring
  // is landing with Task 11 of CLU-85; until then this is undefined on
  // the wire and any consumer must tolerate its absence. Used by the
  // CLU-85 selective-remediation flow to key ephemeral selection state
  // in localStorage.
  id?: string
  course_id: string
  analyzed_at: string
  total_issues: number
  errors: number
  warnings: number
  info: number
  pages_analyzed: number
  images_needing_alt: number
  score: number
  platform_issues?: number
  // Per-content-type counts computed server-side from the FULL issue list.
  // The dashboard reads this so its Content Overview cards reflect reality
  // instead of being filtered from a truncated 200-issue slice (CLU-53).
  issues_by_content_type?: Record<string, ContentTypeBreakdown>
}

export interface ActivityEvent {
  type: 'scan' | 'remediation' | 'autoremedy' | 'alt_text'
  title: string
  description: string
  timestamp: string
}

export function getActivity(courseId: number, limit = 10): Promise<ActivityEvent[]> {
  return apiClient.get<ActivityEvent[]>(`/courses/${courseId}/activity?limit=${limit}`)
}

export interface AccessibilityIssue {
  id: string
  rule_id: string
  severity: 'error' | 'warning' | 'info'
  category: string
  wcag_criterion: string
  message: string
  page_id: string
  page_identifier: string | null
  page_title: string | null
  content_type: string | null
  canvas_url: string | null
  element_html: string | null
  line_number: number | null
  can_auto_fix: boolean
  fix_description: string | null
  source?: string  // "course_content" | "canvas_platform"
}

export interface IssuesResponse {
  issues: AccessibilityIssue[]
  total: number
  page: number
  per_page: number
  total_pages: number
}

export function startScan(courseId: number, scanMode: string = 'content_only'): Promise<ScanJob> {
  return apiClient.post<ScanJob>(`/courses/${courseId}/scan?scan_mode=${scanMode}`)
}

export function getScanJob(courseId: number, jobId: string): Promise<ScanJob> {
  return apiClient.get<ScanJob>(`/courses/${courseId}/scan/${jobId}`)
}

export function getReport(courseId: number): Promise<ReportSummary> {
  return apiClient.get<ReportSummary>(`/courses/${courseId}/report`)
}

export function getIssues(
  courseId: number,
  params?: { category?: string; severity?: string; content_type?: string; page_id?: string; page?: number; per_page?: number },
): Promise<IssuesResponse> {
  const queryParams: Record<string, string> = {}
  if (params?.category) queryParams.category = params.category
  if (params?.severity) queryParams.severity = params.severity
  if (params?.content_type) queryParams.content_type = params.content_type
  if (params?.page_id) queryParams.page_id = params.page_id
  if (params?.page) queryParams.page = String(params.page)
  if (params?.per_page) queryParams.per_page = String(params.per_page)

  return apiClient.get<IssuesResponse>(`/courses/${courseId}/report/issues`, { params: queryParams })
}

export function getOAuth2Url(): Promise<{ url: string }> {
  return apiClient.get<{ url: string }>('/oauth2/url')
}

// --- Orphan cleanup (CLU-67 leftover removal) ---

export interface OrphanPage {
  page_id: number | null
  url: string | null
  title: string
  published: boolean
}

export interface OrphanPagesResponse {
  course_id: number
  count: number
  pages: OrphanPage[]
}

export interface OrphanCleanupResult {
  course_id: number
  found: number
  deleted: number
}

export function listOrphanPages(courseId: number): Promise<OrphanPagesResponse> {
  return apiClient.get<OrphanPagesResponse>(`/courses/${courseId}/cleanup/orphan-pages`)
}

export function deleteOrphanPages(courseId: number): Promise<OrphanCleanupResult> {
  return apiClient.post<OrphanCleanupResult>(`/courses/${courseId}/cleanup/orphan-pages`)
}

// --- Phase 3a: Remediation types ---

export interface RemediationRequest {
  campus?: string
  custom_colors?: { primary: string; secondary: string } | null
  selected_page_ids?: string[] | null
  apply_templates?: boolean
  generate_alt_text?: boolean
  use_ai_remediation?: boolean
  fix_headings?: boolean
  fix_tables?: boolean
  fix_links?: boolean
  fix_contrast?: boolean
  fix_structure?: boolean
  fix_media?: boolean
  fix_math?: boolean
  fix_images?: boolean
  apply_to_all_instances?: boolean
  target_rule_id?: string | null
}

export interface RemediationJob {
  id: string
  course_id: string
  session_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  progress: number
  pages_total: number
  pages_remediated: number
  created_at: string
  completed_at: string | null
  error: string | null
  request: RemediationRequest
}

export interface AltTextResult {
  image_id: string
  page_id: string
  src: string
  status: 'generated' | 'skipped' | 'manual_review' | 'error'
  alt_text: string | null
  provider: string | null
  model: string | null
  confidence: number
  candidate_count: number
  judge_model: string | null
  fallback_used: boolean
  fallback_reason: string | null
  reason: string | null
}

export interface PreviewSummary {
  page_id: string
  page_title: string
  content_type: string
  issues_fixed_count: number
  issues_fixed: string[]
  canvas_tags_stripped: number
  canvas_attributes_stripped: number
  alt_texts_count: number
}

export interface PreviewsResponse {
  previews: PreviewSummary[]
}

export interface RemediationPreview {
  job_id: string
  page_id: string
  page_title: string
  content_type: string
  original_html: string
  remediated_html: string
  issues_fixed: string[]
  canvas_tags_stripped: number
  canvas_attributes_stripped: number
  alt_text_results: AltTextResult[]
}

export interface RemediationResult {
  course_id: string
  remediated_at: string
  pages_updated: number
  issues_fixed: number
  alt_texts_generated: number
  canvas_tags_stripped: number
  canvas_attributes_stripped: number
}

export function startRemediation(
  courseId: number,
  request: RemediationRequest,
): Promise<RemediationJob> {
  return apiClient.post<RemediationJob>(`/courses/${courseId}/remediate`, request)
}

export function getRemediationJob(
  courseId: number,
  jobId: string,
): Promise<RemediationJob> {
  return apiClient.get<RemediationJob>(`/courses/${courseId}/remediate/${jobId}`)
}

export function getPreviews(
  courseId: number,
  jobId: string,
): Promise<PreviewsResponse> {
  return apiClient.get<PreviewsResponse>(
    `/courses/${courseId}/remediate/${jobId}/previews`,
  )
}

export function getPreview(
  courseId: number,
  jobId: string,
  pageId: string,
): Promise<RemediationPreview> {
  return apiClient.get<RemediationPreview>(
    `/courses/${courseId}/remediate/${jobId}/previews/${pageId}`,
  )
}

export function applyPreviews(
  courseId: number,
  jobId: string,
  pageIds: string[],
  altTextOverrides?: Record<string, string>,
): Promise<RemediationResult> {
  return apiClient.post<RemediationResult>(
    `/courses/${courseId}/remediate/${jobId}/apply`,
    { page_ids: pageIds, alt_text_overrides: altTextOverrides ?? {} },
  )
}

// --- Phase 6a: File Audit types ---

export interface CheckReportRef {
  total_checks: number
  passed: number
  failed: number
  not_applicable: number
  errors: number
  pass_rate: number
}

export interface FileAuditEntry {
  file_id: number
  filename: string
  content_type: string
  size: number
  is_pdf: boolean
  check_report: CheckReportRef | null
  status: 'passed' | 'failed' | 'not_audited'
}

export interface FileReport {
  course_id: string
  audited_at: string
  total_files: number
  pdf_count: number
  pdfs_passed: number
  pdfs_failed: number
  entries: FileAuditEntry[]
}

export interface FileAuditJob {
  id: string
  course_id: string
  session_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  progress: number
  files_total: number
  files_audited: number
  created_at: string
  completed_at: string | null
  error: string | null
  report_id: string | null
}

export function startFileAudit(courseId: number): Promise<FileAuditJob> {
  return apiClient.post<FileAuditJob>(`/courses/${courseId}/files/scan`)
}

export function getFileAuditJob(courseId: number, jobId: string): Promise<FileAuditJob> {
  return apiClient.get<FileAuditJob>(`/courses/${courseId}/files/scan/${jobId}`)
}

export function getFileReport(courseId: number): Promise<FileReport> {
  return apiClient.get<FileReport>(`/courses/${courseId}/files/report`)
}

export function getFileCheckReport(courseId: number, fileId: number): Promise<FileAuditEntry> {
  return apiClient.get<FileAuditEntry>(`/courses/${courseId}/files/report/${fileId}`)
}

// --- Phase 6b: Conversion + OCR types ---

export interface ConversionJob {
  id: string
  course_id: string
  session_id: string
  file_id: number
  filename: string
  source_format: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  progress: number
  extracted_markdown: string
  converted_html: string
  canvas_page_id: number | string | null
  canvas_page_url: string | null
  created_at: string
  completed_at: string | null
  error: string | null
}

export interface OCRJobType {
  id: string
  course_id: string
  session_id: string
  file_id: number
  filename: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  progress: number
  pages_total: number
  pages_processed: number
  extracted_markdown: string
  created_at: string
  completed_at: string | null
  error: string | null
}

export function startConversion(courseId: number, fileId: number): Promise<ConversionJob> {
  return apiClient.post<ConversionJob>(`/courses/${courseId}/files/${fileId}/convert`)
}

export function getConversionJob(courseId: number, jobId: string): Promise<ConversionJob> {
  return apiClient.get<ConversionJob>(`/courses/${courseId}/files/convert/${jobId}`)
}

export function startOCR(courseId: number, fileId: number): Promise<OCRJobType> {
  return apiClient.post<OCRJobType>(`/courses/${courseId}/files/${fileId}/ocr`)
}

export function getOCRJob(courseId: number, jobId: string): Promise<OCRJobType> {
  return apiClient.get<OCRJobType>(`/courses/${courseId}/files/ocr/${jobId}`)
}

// --- Phase 6a: AutoRemedy types ---

export interface AutoRemedyJob {
  id: string
  course_id: string
  session_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  progress: number
  phase: 'scanning' | 'remediating_html' | 'auditing_files' | 'converting_documents' | 'complete'
  html_pages_total: number
  html_pages_remediated: number
  files_total: number
  files_audited: number
  issues_found: number
  issues_fixed: number
  pdfs_fixed: number
  pdfs_fix_failed: number
  docs_converted: number
  links_replaced: number
  originals_archived: number
  created_at: string
  completed_at: string | null
  error: string | null
  activity_log: string[]
  remediation_job_id: string | null
  file_report_id: string | null
  cancel_requested?: boolean
}

export interface AutoRemedySummary {
  job_id: string
  status: string
  phase: string
  html: {
    pages_total: number
    pages_remediated: number
    issues_found: number
    issues_fixed: number
  }
  files: {
    files_total: number
    files_audited: number
  }
  pdfs: { fixed: number; fix_failed: number }
  docs_converted: number
  remediation_job_id: string | null
  file_report_id: string | null
}

export function startAutoRemedy(courseId: number): Promise<AutoRemedyJob> {
  return apiClient.post<AutoRemedyJob>(`/courses/${courseId}/autoremedy`)
}

export function getAutoRemedyJob(courseId: number, jobId: string): Promise<AutoRemedyJob> {
  return apiClient.get<AutoRemedyJob>(`/courses/${courseId}/autoremedy/${jobId}`)
}

export function getLatestAutoRemedyJob(courseId: number): Promise<AutoRemedyJob | null> {
  return apiClient.get<AutoRemedyJob>(`/courses/${courseId}/autoremedy/latest`).catch(() => null)
}

export function cancelAutoRemedy(courseId: number, jobId: string): Promise<AutoRemedyJob> {
  return apiClient.post<AutoRemedyJob>(`/courses/${courseId}/autoremedy/${jobId}/cancel`)
}

export function getAutoRemedySummary(courseId: number, jobId: string): Promise<AutoRemedySummary> {
  return apiClient.get<AutoRemedySummary>(`/courses/${courseId}/autoremedy/${jobId}/summary`)
}

export function applyAutoRemedy(courseId: number, jobId: string, pageIds: string[], altTextOverrides?: Record<string, string>): Promise<RemediationResult> {
  return apiClient.post<RemediationResult>(`/courses/${courseId}/autoremedy/${jobId}/apply`, { page_ids: pageIds, alt_text_overrides: altTextOverrides ?? {} })
}

export interface CourseExportStatus {
  export_id: number
  course_id: string
  export_type: 'common_cartridge' | 'qti' | 'zip'
  workflow_state: 'created' | 'exporting' | 'exported' | 'failed'
  created_at: string | null
  progress_url: string | null
  progress_completion: number | null
  progress_state: 'queued' | 'running' | 'completed' | 'failed' | null
  progress_message: string | null
  download_ready: boolean
  download_url: string | null
}

export function startCourseExport(courseId: number): Promise<CourseExportStatus> {
  return apiClient.post<CourseExportStatus>(`/courses/${courseId}/exports`)
}

export function getCourseExport(courseId: number, exportId: number): Promise<CourseExportStatus> {
  return apiClient.get<CourseExportStatus>(`/courses/${courseId}/exports/${exportId}`)
}

export function downloadCourseExport(courseId: number, exportId: number): string {
  return `/api/courses/${courseId}/exports/${exportId}/download`
}

// --- Phase 6a: PDF Fix types ---

export interface PDFFixJob {
  id: string
  course_id: string
  session_id: string
  file_id: number
  filename: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  progress: number
  checks_before: CheckReportRef | null
  checks_after: CheckReportRef | null
  fixes_applied: string[]
  fixes_skipped: string[]
  upload_mode: string
  fixed_file_id: number | null
  created_at: string
  completed_at: string | null
  error: string | null
}

export function startPDFFix(courseId: number, fileId: number): Promise<PDFFixJob> {
  return apiClient.post<PDFFixJob>(`/courses/${courseId}/files/${fileId}/fix`)
}

export function getPDFFixJob(courseId: number, jobId: string): Promise<PDFFixJob> {
  return apiClient.get<PDFFixJob>(`/courses/${courseId}/files/fix/${jobId}`)
}

export function uploadFixedPDF(courseId: number, jobId: string, mode: 'replace' | 'alongside'): Promise<{ file_id: number; filename: string; url: string }> {
  return apiClient.post(`/courses/${courseId}/files/fix/${jobId}/upload`, { mode })
}

// --- Phase 6a: Version history ---

export interface ContentVersion {
  id: string
  course_id: string
  page_id: string
  content_type: string
  html_content: string
  created_at: string
  created_by: string
  remediation_job_id: string | null
}

export interface VersionsResponse {
  versions: ContentVersion[]
}

export function getVersions(courseId: number, pageId: string): Promise<VersionsResponse> {
  return apiClient.get<VersionsResponse>(`/courses/${courseId}/versions/${pageId}`)
}

export function restoreVersion(courseId: number, versionId: string): Promise<{ restored: boolean }> {
  return apiClient.post<{ restored: boolean }>(`/courses/${courseId}/versions/${versionId}/restore`)
}

// --- Phase 6a: Alt text regenerate ---

export function regenerateAltText(courseId: number, imageSrc: string, pageId: string): Promise<AltTextResult> {
  return apiClient.post<AltTextResult>(`/courses/${courseId}/alt-text/regenerate`, { image_src: imageSrc, page_id: pageId })
}

// --- Phase 6b-3: Structure Editor types ---

export interface StructureNode {
  index: number
  tag: string
  depth: number
  page: number
  text: string
  alt_text: string
  lang: string
  children_count: number
  has_content: boolean
}

export interface StructureIssue {
  description: string
  severity: 'error' | 'warning' | 'info'
  location: string
}

export interface StructureResponse {
  file_id: number
  filename: string
  page_count: number
  has_structure_tree: boolean
  nodes: StructureNode[]
  issues: StructureIssue[]
}

export interface StructureEditItem {
  node_index: number
  new_tag: string
}

export interface StructureEditResponse {
  edits_applied: number
  errors: string[]
}

export function getPDFStructure(courseId: number, fileId: number): Promise<StructureResponse> {
  return apiClient.get<StructureResponse>(`/courses/${courseId}/files/${fileId}/structure`)
}

export function updatePDFStructure(
  courseId: number,
  fileId: number,
  edits: StructureEditItem[],
): Promise<StructureEditResponse> {
  return apiClient.post<StructureEditResponse>(
    `/courses/${courseId}/files/${fileId}/structure`,
    { edits },
  )
}

// --- Phase 6b-3: Contrast Checker types ---

export interface ContrastCheckRequest {
  foreground: string
  background: string
  font_size?: number
  is_bold?: boolean
}

export interface ContrastCheckResult {
  ratio: number
  aa_pass: boolean
  aaa_pass: boolean
  large_text: boolean
  aa_threshold: number
  aaa_threshold: number
  suggestion: string
}

export function checkContrast(body: ContrastCheckRequest): Promise<ContrastCheckResult> {
  return apiClient.post<ContrastCheckResult>('/accessibility/contrast-check', body)
}

// --- Phase 7: External Links + Alternative Formats ---

export interface ExternalDocLink {
  url: string
  page_id: string
  page_title: string
  filename: string
  extension: string
  is_external: boolean
}

export interface ExternalLinksResponse {
  course_id: string
  pages_scanned: number
  links: ExternalDocLink[]
  total: number
  external_count: number
  internal_count: number
}

export interface ExternalLinkJob {
  id: string
  course_id: string
  session_id: string
  url: string
  filename: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  action_taken: string
  canvas_file_id: number | null
  canvas_page_url: string | null
  created_at: string
  completed_at: string | null
  error: string | null
}

export function scanExternalLinks(courseId: number): Promise<ExternalLinksResponse> {
  return apiClient.get<ExternalLinksResponse>(`/courses/${courseId}/files/external-links`)
}

export function processExternalLink(
  courseId: number,
  url: string,
  filename: string,
): Promise<ExternalLinkJob> {
  return apiClient.post<ExternalLinkJob>(
    `/courses/${courseId}/files/external-links/process`,
    { url, filename },
  )
}

export interface AltFormatsResponse {
  formats: string[]
}

export function listAltFormats(courseId: number, pageId: string): Promise<AltFormatsResponse> {
  return apiClient.get<AltFormatsResponse>(`/courses/${courseId}/pages/${pageId}/alt-formats`)
}

export function getAltFormatUrl(courseId: number, pageId: string, format: string): string {
  return `/api/courses/${courseId}/pages/${pageId}/alt-formats/${format}`
}

// --- Phase 8: Admin Panel types ---

export interface RuleInfo {
  rule_id: string
  severity: string
  category: string
  wcag_criterion: string
  can_auto_fix: boolean
  message_template: string
  severity_override: string | null
  enabled: boolean
  notes: string
}

export interface RuleConfigUpdate {
  severity_override: string | null
  enabled: boolean
  notes: string
}

export interface AdminSettingsData {
  deployment_id: string
  rule_configs: Record<string, { rule_id: string; severity_override: string | null; enabled: boolean; notes: string }>
  auto_scan_enabled: boolean
  auto_scan_interval_hours: number
  updated_at: string
}

export interface AdminSettingsUpdate {
  auto_scan_enabled?: boolean
  auto_scan_interval_hours?: number
}

export interface BatchJobData {
  id: string
  deployment_id: string
  course_ids: string[]
  operation: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  progress: number
  courses_total: number
  courses_completed: number
  results: Record<string, string>
  created_at: string
  completed_at: string | null
  error: string | null
}

export interface BatchRequest {
  course_ids: string[]
  operation: 'scan' | 'remediate' | 'audit_files'
}

export interface InstitutionDashboardData {
  total_courses: number
  total_pages_scanned: number
  total_issues: number
  total_issues_fixed: number
  avg_score: number
  courses_scanned: number
  top_issues: { rule_id: string; count: number; severity: string }[]
}

export interface RemediationGuide {
  title: string
  wcag: string
  severity: string
  category: string
  description: string
  steps: string[]
  auto_fixable: boolean
  resources: string[]
}

export interface GuidesResponse {
  guides: Record<string, RemediationGuide>
  total: number
}

export interface ScheduledScanData {
  course_id: string
  last_scan_at: string | null
  next_scan_at: string | null
  enabled: boolean
}

// Admin API functions

export function getAdminRules(): Promise<RuleInfo[]> {
  return apiClient.get<RuleInfo[]>('/admin/rules')
}

export function updateAdminRule(ruleId: string, update: RuleConfigUpdate): Promise<RuleInfo> {
  return apiClient.put<RuleInfo>(`/admin/rules/${ruleId}`, update)
}

export function getAdminSettings(): Promise<AdminSettingsData> {
  return apiClient.get<AdminSettingsData>('/admin/settings')
}

export function updateAdminSettings(update: AdminSettingsUpdate): Promise<AdminSettingsData> {
  return apiClient.put<AdminSettingsData>('/admin/settings', update)
}

export function startBatchJob(request: BatchRequest): Promise<BatchJobData> {
  return apiClient.post<BatchJobData>('/admin/batch', request)
}

export function getBatchJob(jobId: string): Promise<BatchJobData> {
  return apiClient.get<BatchJobData>(`/admin/batch/${jobId}`)
}

export function getInstitutionDashboard(): Promise<InstitutionDashboardData> {
  return apiClient.get<InstitutionDashboardData>('/admin/dashboard')
}

export function getRemediationGuides(params?: { category?: string; severity?: string }): Promise<GuidesResponse> {
  const queryParams: Record<string, string> = {}
  if (params?.category) queryParams.category = params.category
  if (params?.severity) queryParams.severity = params.severity
  return apiClient.get<GuidesResponse>('/admin/guides', { params: queryParams })
}

export function getRemediationGuide(ruleId: string): Promise<RemediationGuide & { rule_id: string }> {
  return apiClient.get<RemediationGuide & { rule_id: string }>(`/admin/guides/${ruleId}`)
}

export function getScheduledScans(): Promise<ScheduledScanData[]> {
  return apiClient.get<ScheduledScanData[]>('/admin/scheduled-scans')
}

export function upsertScheduledScan(courseId: string, enabled: boolean): Promise<ScheduledScanData> {
  return apiClient.post<ScheduledScanData>(`/admin/scheduled-scans/${courseId}`, { enabled })
}

// --- Video Captions types ---

export interface YouTubeVideo {
  video_id: string
  video_url: string
  page_id: string
  page_title: string
  content_type: string
  thumbnail_url: string
  source_type: string // "youtube" or "studio"
}

export interface TranscriptionJob {
  id: string
  course_id: string
  video_id: string
  video_url: string
  video_title: string
  status: string
  phase: string
  progress: number
  chunks_total: number
  chunks_transcribed: number
  vtt_content: string
  srt_content: string
  error: string | null
  created_at: string
  completed_at: string | null
  page_ids_injected: string[]
}

export function scanCourseVideos(courseId: string): Promise<YouTubeVideo[]> {
  return apiClient.get<{ videos: YouTubeVideo[] }>(`/courses/${courseId}/captions/videos`).then((r) => r.videos)
}

export function startTranscription(courseId: string, videoUrl: string, videoId: string, sourceType: string = 'youtube'): Promise<TranscriptionJob> {
  return apiClient.post<TranscriptionJob>(`/courses/${courseId}/captions/transcribe`, { video_url: videoUrl, video_id: videoId, source_type: sourceType })
}

export function getTranscriptionJob(courseId: string, jobId: string): Promise<TranscriptionJob> {
  return apiClient.get<TranscriptionJob>(`/courses/${courseId}/captions/jobs/${jobId}`)
}

export function listTranscriptionJobs(courseId: string): Promise<TranscriptionJob[]> {
  return apiClient.get<TranscriptionJob[]>(`/courses/${courseId}/captions/jobs`)
}

export function injectCaptions(courseId: string, jobId: string, pageIds: string[]): Promise<void> {
  return apiClient.post<void>(`/courses/${courseId}/captions/jobs/${jobId}/inject`, { page_ids: pageIds })
}

// --- ACR (Accessibility Conformance Report) types ---

export type ConformanceLevel = 'Supports' | 'Partially Supports' | 'Does Not Support' | 'Not Applicable'

export interface CriterionRollup {
  criterion_id: string
  name: string
  level: 'A' | 'AA' | 'AAA'
  conformance: ConformanceLevel
  remarks: string
  issue_count: number
  pages_affected: number
  sample_artifacts: string[]
}

export interface FindingEvidence {
  rule_id: string
  wcag_criterion: string
  severity: 'error' | 'warning' | 'info'
  message: string
  element_html?: string
  remediation_applied?: boolean
  remediation_notes?: string
}

export interface ArtifactEvidence {
  artifact_id: string
  artifact_type: string
  title: string
  canvas_url: string
  content_type_mime?: string
  findings: FindingEvidence[]
  remediation_status: string
}

export interface CourseACR {
  id: string
  course_id: string
  scan_run_id: string
  generated_at: string
  vpat_edition: string
  wcag_version: string
  conformance_level: string
  course_name: string
  course_url: string
  evaluator: string
  overall_status: ConformanceLevel
  conformance_percentage: number
  score_band: 'excellent' | 'good' | 'needs_work' | 'poor'
  criteria: CriterionRollup[]
  evidence: ArtifactEvidence[]
  issues_before: number
  issues_after: number
  issues_fixed: number
  pages_remediated: number
}

export interface ACRJob {
  id: string
  course_id: string
  session_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  created_at: string
  completed_at?: string
  error?: string
  acr_id?: string
}

export interface ACRSummaryResponse {
  id: string
  course_id: string
  generated_at: string
  overall_status: string
  conformance_percentage: number
  issues_before: number
  issues_after: number
  issues_fixed: number
}

export interface GenerateACRRequest {
  evaluator?: string
  include_evidence?: boolean
  compare_scan_id?: string
}

export interface ACRComparison {
  acr_1: {
    id: string
    date: string
    conformance_percentage: number
    issues_count: number
    status: string
  }
  acr_2: {
    id: string
    date: string
    conformance_percentage: number
    issues_count: number
    status: string
  }
  improvement: {
    conformance_change: number
    issues_change: number
    status_change: boolean
  }
}

// ACR API functions

export function generateACR(courseId: string, request?: GenerateACRRequest): Promise<{ job_id: string; status: string; message: string }> {
  return apiClient.post(`/courses/${courseId}/acr/generate`, request)
}

export function getACRJob(courseId: string, jobId: string): Promise<ACRJob> {
  return apiClient.get(`/courses/${courseId}/acr/jobs/${jobId}`)
}

export function listACRReports(courseId: string): Promise<ACRSummaryResponse[]> {
  return apiClient.get(`/courses/${courseId}/acr/reports`)
}

export function getACRReport(courseId: string, acrId: string, format: 'json' | 'html' | 'markdown' = 'json'): Promise<{ acr_id: string; format: string; data?: CourseACR; content?: string }> {
  return apiClient.get(`/courses/${courseId}/acr/reports/${acrId}`, { params: { format } })
}

export function getLatestACR(courseId: string, format: 'json' | 'html' | 'markdown' = 'json'): Promise<{ acr_id: string; format: string; data?: CourseACR; content?: string }> {
  return apiClient.get(`/courses/${courseId}/acr/reports/latest`, { params: { format } })
}

export function downloadACR(courseId: string, acrId: string, format: 'html' | 'json' | 'markdown'): string {
  return `/api/courses/${courseId}/acr/reports/${acrId}/download?format=${format}`
}

export function compareACRs(courseId: string, acrId1: string, acrId2: string): Promise<ACRComparison> {
  return apiClient.get(`/courses/${courseId}/acr/comparison/${acrId1}/${acrId2}`)
}

// --- Remediated Course Export ---

export function startRemediatedExport(courseId: number): Promise<{ status: string; course_id: number }> {
  return apiClient.post(`/courses/${courseId}/exports/remediated`)
}

export function getRemediatedExportStatus(courseId: number): Promise<{ status: string; patched_pages?: number; error?: string }> {
  return apiClient.get(`/courses/${courseId}/exports/remediated/status`)
}

// --- Phase 9: Advanced Features types ---

export interface CanvasCustomJSData {
  js: string
  css: string
}

export interface BlueprintInfoData {
  is_blueprint: boolean
  associated_courses: string[]
}

export interface BlueprintSyncResultData {
  blueprint_course_id: string
  associated_courses: string[]
  synced: number
  errors: string[]
}

export interface WebhookStatusData {
  status: string
  endpoint: string
}

export interface DocHubUploadResult {
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

export interface CourseStructureReportData {
  total_modules: number
  total_items: number
  total_rubrics: number
  issues: { category: string; severity: string; message: string; location: string }[]
  score: number
}

// Phase 9 API functions

export function getCanvasCustomJS(): Promise<CanvasCustomJSData> {
  return apiClient.get<CanvasCustomJSData>('/admin/canvas-js')
}

export function getBlueprintInfo(courseId: number): Promise<BlueprintInfoData> {
  return apiClient.get<BlueprintInfoData>(`/admin/courses/${courseId}/blueprint`)
}

export function triggerBlueprintSync(courseId: number): Promise<BlueprintSyncResultData> {
  return apiClient.post<BlueprintSyncResultData>(`/admin/courses/${courseId}/blueprint/sync`)
}

export function getWebhookStatus(): Promise<WebhookStatusData> {
  return apiClient.get<WebhookStatusData>('/webhooks/canvas/status')
}

export function getCourseStructureAnalysis(courseId: number): Promise<CourseStructureReportData> {
  return apiClient.get<CourseStructureReportData>(`/courses/${courseId}/structure-analysis`)
}

// ============================================================
// CLU-85 — selective remediation
// ============================================================

export interface ContentItemSummary {
  identifier: string
  title: string
  content_type: string
  canvas_url: string | null
  issue_count: number
  issue_severities: {
    error: number
    warning: number
    info: number
  }
  permanently_excluded: boolean
}

export interface ItemsResponse {
  scan_report_id: string
  items: ContentItemSummary[]
}

export interface ConversionCandidate {
  file_id: number
  filename: string
  size_bytes: number
  page_count: number | null
  content_type: string
  phase4_eligible: boolean
  default_selected: boolean
  hard_excluded: boolean
  exclude_reason: string | null
  has_audit_issues: boolean
  permanently_excluded: boolean
}

export interface ConversionCandidatesResponse {
  file_report_id: string
  candidates: ConversionCandidate[]
}

export interface ExclusionSummary {
  item_identifier: string
  item_type: string
  reason: string | null
  excluded_at: string  // ISO 8601 datetime string
  excluded_by: string
}

export interface ListExclusionsResponse {
  exclusions: ExclusionSummary[]
}

export interface AddExclusionRequest {
  item_identifier: string
  item_type: string
  reason?: string | null
}

export interface AddExclusionResponse {
  status: string
}

export interface SelectiveAutoRemedyRequest {
  scan_report_id?: string
  file_report_id?: string
  reviewed_content_types?: string[]
  skip_page_identifiers?: string[]
  skip_file_ids?: number[]
}

/**
 * Get items grouped by identifier for a specific content type.
 * Backs the CLU-85 content-type review views (Pages, Assignments,
 * Discussions, Quizzes, Announcements, Syllabus).
 *
 * Note: the API expects the canonical content_type enum value
 * (e.g. "wiki_page", "assignment", "discussion", "quiz",
 * "announcement", "syllabus"), NOT the URL slug.
 */
export function getItemsByContentType(
  courseId: number,
  contentType: string,
): Promise<ItemsResponse> {
  const encoded = encodeURIComponent(contentType)
  return apiClient.get<ItemsResponse>(
    `/courses/${courseId}/report/items?content_type=${encoded}`,
  )
}

/**
 * Get the list of files phase 4 of AutoRemedy would consider for
 * conversion — failed PDFs + convertible Office docs + legacy .ppt/.pps.
 * Clean PDFs that phase 4 would NOT touch are omitted. Backs the
 * CLU-85 Files review view.
 */
export function getConversionCandidates(
  courseId: number,
): Promise<ConversionCandidatesResponse> {
  return apiClient.get<ConversionCandidatesResponse>(
    `/courses/${courseId}/autoremedy/conversion-candidates`,
  )
}

/**
 * List all permanent per-course exclusions from AutoRemedy. Backs
 * the CLU-85 "Manage Exclusions" panel.
 */
export function listExclusions(
  courseId: number,
): Promise<ListExclusionsResponse> {
  return apiClient.get<ListExclusionsResponse>(
    `/courses/${courseId}/exclusions`,
  )
}

/**
 * Add a permanent exclusion. Idempotent — the backend returns 201
 * whether the identifier was already excluded or not.
 */
export function addExclusion(
  courseId: number,
  body: AddExclusionRequest,
): Promise<AddExclusionResponse> {
  return apiClient.post<AddExclusionResponse>(
    `/courses/${courseId}/exclusions`,
    body,
  )
}

/**
 * Remove a permanent exclusion. Idempotent — the backend returns
 * 204 whether the identifier existed or not.
 */
export function removeExclusion(
  courseId: number,
  itemIdentifier: string,
): Promise<void> {
  return apiClient.delete(
    `/courses/${courseId}/exclusions/${encodeURIComponent(itemIdentifier)}`,
  )
}

/**
 * Start an AutoRemedy run with a selective request body.
 *
 * When `body` is empty or all fields are default, this is
 * equivalent to today's Fix My Course one-click behavior. When
 * `skip_page_identifiers` or `skip_file_ids` are populated, phase 2
 * and phase 4 will filter those items out of the run.
 *
 * The `scan_report_id` and `file_report_id` fields, if provided,
 * pin the snapshot the user reviewed against; the backend returns
 * 409 Stale if they don't match the latest reports for the course.
 * Callers that don't need snapshot pinning (Fix My Course) can
 * omit both.
 */
export function startSelectiveAutoRemedy(
  courseId: number,
  body: SelectiveAutoRemedyRequest = {},
): Promise<AutoRemedyJob> {
  return apiClient.post<AutoRemedyJob>(
    `/courses/${courseId}/autoremedy`,
    body,
  )
}
