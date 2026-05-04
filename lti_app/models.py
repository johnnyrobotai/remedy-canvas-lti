"""Pydantic models for the Remedy Canvas LTI Canvas Accessibility Remediation Tool.

Adapted from CLU backend/app/models.py with:
- Canvas API fields (canvas_id, canvas_url, parent_id) on CoursePage
- Extended ContentType enum (new quizzes, syllabus, calendar events, rubrics)
- Extended IssueCategory enum (forms, focus for WCAG 2.2)
- Updated RemediationRequest for LTI context
- Removed IMSCC-specific models (PopeTech, UploadResponse, Standalone*)
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, computed_field


class ContentType(str, Enum):
    """Content types in Canvas courses."""

    WIKI_PAGE = "wiki_page"
    ASSIGNMENT = "assignment"
    DISCUSSION = "discussion"
    ANNOUNCEMENT = "announcement"
    QUIZ = "quiz"
    QUIZ_QUESTION = "quiz_question"
    NEW_QUIZ = "new_quiz"
    NEW_QUIZ_ITEM = "new_quiz_item"
    SYLLABUS = "syllabus"
    CALENDAR_EVENT = "calendar_event"
    RUBRIC = "rubric"


class Campus(str, Enum):
    """LACCD campus identifiers."""

    LACCD = "laccd"
    ELAC = "elac"
    LACC = "lacc"
    LAHC = "lahc"
    LAMC = "lamc"
    LAPC = "lapc"
    LASC = "lasc"
    LATTC = "lattc"
    LAVC = "lavc"
    WLAC = "wlac"
    CUSTOM = "custom"


class TemplateType(str, Enum):
    """Joshua template types."""

    GENERAL_CONTENT = "general_content"
    FRONT_PAGE = "front_page"
    ANNOUNCEMENT = "announcement"
    MEET_INSTRUCTOR = "meet_instructor"
    MODULE_OVERVIEW = "module_overview"
    ASSIGNMENT = "assignment"
    DISCUSSION = "discussion"
    QUIZ = "quiz"


class IssueSeverity(str, Enum):
    """Accessibility issue severity levels."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class IssueCategory(str, Enum):
    """Accessibility issue categories based on WCAG 2.2."""

    IMAGES = "images"
    HEADINGS = "headings"
    TABLES = "tables"
    LINKS = "links"
    CONTRAST = "contrast"
    STRUCTURE = "structure"
    MEDIA = "media"
    MATH = "math"
    FORMS = "forms"
    FOCUS = "focus"
    DOCUMENTS = "documents"
    EVENTS = "events"


class AltTextGenerationStatus(str, Enum):
    """Outcome of an alt text generation attempt."""

    GENERATED = "generated"
    SKIPPED = "skipped"
    MANUAL_REVIEW = "manual_review"
    ERROR = "error"


# Color scheme model
class ColorScheme(BaseModel):
    """Campus color scheme."""

    primary: str = Field(..., pattern=r"^#[0-9A-Fa-f]{6}$")
    secondary: str = Field(..., pattern=r"^#[0-9A-Fa-f]{6}$")


# Course models
class CourseMetadata(BaseModel):
    """Course metadata from Canvas API."""

    id: str
    title: str
    created_at: datetime
    total_pages: int = 0
    total_images: int = 0
    content_type_counts: Optional[dict[str, int]] = None


class CoursePage(BaseModel):
    """A single content item from a Canvas course, normalized across all content types."""

    id: str
    title: str
    identifier: str
    html_content: str
    file_path: str = ""
    content_type: ContentType = ContentType.WIKI_PAGE
    detected_template: Optional[TemplateType] = None
    canvas_id: Optional[int] = None
    canvas_url: Optional[str] = None
    parent_id: Optional[int] = None


class CourseImage(BaseModel):
    """An image found in the course."""

    id: str
    src: str
    alt_text: Optional[str] = None
    page_id: str
    needs_alt_text: bool = True
    suggested_alt_text: Optional[str] = None
    alt_text_skip_reason: Optional[str] = None


class AltTextGenerationResult(BaseModel):
    """Result for a single image alt text generation attempt."""

    image_id: str
    page_id: str
    src: str
    status: AltTextGenerationStatus
    alt_text: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    confidence: float = 0.0
    candidate_count: int = 0
    judge_model: Optional[str] = None
    fallback_used: bool = False
    fallback_reason: Optional[str] = None
    reason: Optional[str] = None


class AltTextGenerationResponse(BaseModel):
    """Response after generating alt text for a course."""

    course_id: str
    generated_count: int = 0
    skipped_count: int = 0
    manual_review_count: int = 0
    error_count: int = 0
    alt_texts: dict[str, str] = {}
    results: list[AltTextGenerationResult] = []


# Accessibility models
class AccessibilityIssue(BaseModel):
    """A single accessibility issue found during analysis."""

    id: str
    rule_id: str
    severity: IssueSeverity
    category: IssueCategory
    wcag_criterion: str
    message: str
    page_id: str
    page_identifier: Optional[str] = None
    page_title: Optional[str] = None
    content_type: Optional[str] = None
    canvas_url: Optional[str] = None
    element_html: Optional[str] = None
    line_number: Optional[int] = None
    can_auto_fix: bool = False
    fix_description: Optional[str] = None
    source: str = "course_content"  # "course_content" or "canvas_platform"
    # Optional metadata captured by the rendered scanner. For axe-core
    # color-contrast violations this carries the rendered fg/bg colors
    # and contrast ratio so the transformer can inject an inline
    # override that satisfies the WCAG threshold against the actual
    # rendered Canvas background (not the assumed white background
    # used by the static contrast rule).
    axe_meta: Optional[dict] = None

    @computed_field
    @property
    def wcag_url(self) -> str:
        """WCAG 2.2 Understanding document URL for this criterion."""
        if not self.wcag_criterion:
            return ""
        return f"https://www.w3.org/WAI/WCAG22/Understanding/#:~:text={self.wcag_criterion}"


class AccessibilityReport(BaseModel):
    """Complete accessibility analysis report."""

    course_id: str
    analyzed_at: datetime
    total_issues: int = 0
    errors: int = 0
    warnings: int = 0
    info: int = 0
    issues: list[AccessibilityIssue] = []
    pages_analyzed: int = 0
    images_needing_alt: int = 0


class ContentItemSummary(BaseModel):
    """One row in the item-grouped view of a scan report (CLU-85).

    Produced by aggregating ``AccessibilityIssue`` records by
    ``page_identifier``. Returned by ``GET /api/courses/{id}/report/items``
    and consumed by the selective-remediation review views — one row
    per Canvas item (page, assignment, etc.) rather than one row per
    individual issue.
    """

    identifier: str  # e.g. "page-104", "assignment-22"
    title: str  # page title from the scan report
    content_type: str  # "wiki_page" | "assignment" | ...
    canvas_url: Optional[str] = None  # relative URL for linking back to Canvas
    issue_count: int
    issue_severities: dict[str, int]  # {"error": 1, "warning": 2, "info": 0}
    permanently_excluded: bool = False  # set from course_exclusions at response time


class CourseExclusion(BaseModel):
    """Durable per-course item exclusion from AutoRemedy runs (CLU-85).

    Items in this table are pre-unchecked on every content-type review
    view for the course. The backend merges these identifiers into the
    `skip_page_identifiers` / `skip_file_ids` sets at the start of every
    AutoRemedy run, on top of whatever the request payload specifies.
    """
    id: str  # uuid or ulid
    course_id: str
    item_identifier: str  # e.g. "page-104", "assignment-22", "file-201"
    item_type: str  # "wiki_page" | "assignment" | "discussion" | "quiz" | "announcement" | "syllabus" | "file"
    reason: str | None = None
    excluded_at: datetime
    excluded_by: str


class ScanStatus(str, Enum):
    """Scan job lifecycle states."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ScanJob(BaseModel):
    """Background scan job tracker."""

    id: str
    course_id: str
    session_id: str
    status: ScanStatus = ScanStatus.PENDING
    progress: float = 0.0
    pages_total: int = 0
    pages_scanned: int = 0
    created_at: datetime
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    report_id: Optional[str] = None
    scan_mode: str = "content_only"  # "content_only" | "full" | "rendered_only"
    phase: str = ""  # "" | "content" | "rendered" | "merging"
    queue_position: int = 0  # 0 = running, 1+ = waiting in queue
    current_page: str = ""  # Name of page currently being scanned


# Remediation models
class RemediationRequest(BaseModel):
    """Request to remediate a course."""

    campus: Campus = Campus.LACCD
    custom_colors: Optional[ColorScheme] = None
    selected_page_ids: Optional[list[str]] = None
    apply_templates: bool = False
    generate_alt_text: bool = True
    use_ai_remediation: bool = False
    fix_headings: bool = True
    fix_tables: bool = True
    fix_links: bool = True
    fix_contrast: bool = True
    fix_structure: bool = True
    fix_media: bool = True
    fix_math: bool = True
    fix_images: bool = True
    ai_provider: Optional[str] = None
    # Batch fix-all-instances
    apply_to_all_instances: bool = False
    target_rule_id: Optional[str] = None


class RemediationJob(BaseModel):
    """Background remediation job tracker."""

    id: str
    course_id: str
    session_id: str
    status: ScanStatus = ScanStatus.PENDING
    progress: float = 0.0
    pages_total: int = 0
    pages_remediated: int = 0
    created_at: datetime
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    request: RemediationRequest
    scan_report_id: Optional[str] = None


class RemediationPreview(BaseModel):
    """Before/after preview of a single page remediation."""

    job_id: str
    page_id: str
    page_identifier: Optional[str] = None
    page_title: str
    content_type: ContentType
    original_html: str
    remediated_html: str
    issues_fixed: list[str] = []
    canvas_tags_stripped: int = 0
    canvas_attributes_stripped: int = 0
    alt_text_results: list[AltTextGenerationResult] = []


class RemediationResult(BaseModel):
    """Result of remediation process."""

    course_id: str
    remediated_at: datetime
    pages_updated: int = 0
    issues_fixed: int = 0
    alt_texts_generated: int = 0
    alt_texts_skipped: int = 0
    alt_texts_manual_review: int = 0
    alt_texts_failed: int = 0
    templates_applied: int = 0
    pages_rebuilt: int = 0
    pages_reconverted: int = 0
    safe_fallbacks_applied: int = 0
    execution_strategies: dict[str, int] = {}
    validation_total_issues: int = 0
    validation_errors: int = 0
    validation_warnings: int = 0
    validation_infos: int = 0
    critical_auto_fixable_remaining: int = 0
    missing_required_alt_text: int = 0
    canvas_tags_stripped: int = 0
    canvas_attributes_stripped: int = 0
    canvas_css_stripped: int = 0
    correction_pass_fixes: int = 0
    document_conversion: Optional[dict] = None


# ---------------------------------------------------------------------------
# File audit models
# ---------------------------------------------------------------------------


class FileAuditJob(BaseModel):
    """Background file audit job tracker."""

    id: str
    course_id: str
    session_id: str
    status: ScanStatus = ScanStatus.PENDING
    progress: float = 0.0
    files_total: int = 0
    files_audited: int = 0
    created_at: datetime
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    report_id: Optional[str] = None


class CheckReportRef(BaseModel):
    """Lightweight reference to a PDF CheckReport (avoids importing PDF models into core)."""

    total_checks: int = 0
    passed: int = 0
    failed: int = 0
    not_applicable: int = 0
    errors: int = 0
    pass_rate: float = 0.0


class FileAuditEntry(BaseModel):
    """A single file's audit result."""

    file_id: int
    filename: str
    content_type: str
    size: int
    is_pdf: bool
    check_report: Optional[CheckReportRef] = None
    status: str  # "passed", "failed", "not_audited"
    # CLU-85: source page count (PDFs only). Populated by the file audit
    # pipeline when it parses a PDF. Used by the selective-remediation
    # Files view to hard-exclude PDFs that exceed the conversion page
    # limit. Remains None for non-PDFs and for historical reports that
    # predate the field.
    page_count: Optional[int] = None
    # Populated by AutoRemedy phase 4 to explain the file's remediation
    # outcome in the ACR. Carries both a machine-readable status and a
    # human-readable skip reason so the generated report can tell
    # instructors *why* a linked PDF/DOC warning still appears.
    #
    # remediation_status values:
    #   - "converted"  — file was transposed into a Canvas wiki page
    #   - "pdf_fixed"  — PDF was fixed in-place by PDFFixService
    #   - "skipped"    — conversion was intentionally bypassed (see skip_reason)
    #   - None         — unattempted, or legacy report from before the
    #                    field existed
    remediation_status: Optional[str] = None
    skip_reason: Optional[str] = None


class FileReport(BaseModel):
    """Course-level file audit report."""

    course_id: str
    audited_at: datetime
    total_files: int = 0
    pdf_count: int = 0
    pdfs_passed: int = 0
    pdfs_failed: int = 0
    entries: list[FileAuditEntry] = []


class ConversionCandidate(BaseModel):
    """One row in the Files review view (CLU-85).

    Populated from the latest file audit report + CLU-69 eligibility
    classifier. Only files that phase 4 would potentially touch are
    returned — clean PDFs (no audit issues) are omitted since phase 4
    would not convert them.
    """

    file_id: int
    filename: str
    size_bytes: int
    page_count: Optional[int] = None
    content_type: str  # mime type or extension
    phase4_eligible: bool
    default_selected: bool
    hard_excluded: bool
    exclude_reason: Optional[str] = None
    has_audit_issues: bool
    permanently_excluded: bool = False


class SelectiveAutoRemedyRequest(BaseModel):
    """Optional request body for POST /api/courses/{id}/autoremedy (CLU-85).

    All fields optional. An empty body preserves today's Fix My Course
    behavior (run with everything, no freshness check). Populated
    bodies come from the per-content-type "Run Remediation" flow and
    carry the user's opt-out selections plus snapshot identifiers that
    let the backend detect stale reviews.
    """

    scan_report_id: Optional[str] = None
    file_report_id: Optional[str] = None
    reviewed_content_types: list[str] = []
    skip_page_identifiers: list[str] = []
    skip_file_ids: list[int] = []


# ---------------------------------------------------------------------------
# AutoRemedy models
# ---------------------------------------------------------------------------


class AutoRemedyJob(BaseModel):
    """Background AutoRemedy job tracker."""

    id: str
    course_id: str
    session_id: str
    status: ScanStatus = ScanStatus.PENDING
    progress: float = 0.0
    phase: str = "scanning"  # "scanning" | "remediating_html" | "auditing_files" | "fixing_pdfs" | "converting_documents" | "complete"
    html_pages_total: int = 0
    html_pages_remediated: int = 0
    files_total: int = 0
    files_audited: int = 0
    issues_found: int = 0
    issues_fixed: int = 0
    pdfs_fixed: int = 0
    pdfs_fix_failed: int = 0
    docs_converted: int = 0
    docs_skipped: int = 0  # CLU-69: docs bypassed (textbooks, catalogs, etc.)
    links_replaced: int = 0
    originals_archived: int = 0
    created_at: datetime
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    activity_log: list[str] = []  # rolling log of recent actions for UI display
    scan_report_id: Optional[str] = None
    remediation_job_id: Optional[str] = None
    file_report_id: Optional[str] = None
    # Cooperative cancellation flag (CLU-64). Set to True via the cancel
    # endpoint; the orchestrator polls this between phases and inside
    # per-page callbacks and exits cleanly with status=failed when set.
    cancel_requested: bool = False


class CourseExportStatus(BaseModel):
    """Status of a Canvas content export job."""

    export_id: int
    course_id: str
    export_type: str = "common_cartridge"
    workflow_state: str = "created"
    created_at: Optional[datetime] = None
    progress_url: Optional[str] = None
    progress_completion: Optional[float] = None
    progress_state: Optional[str] = None
    progress_message: Optional[str] = None
    download_ready: bool = False
    download_url: Optional[str] = None


# ---------------------------------------------------------------------------
# Version history models
# ---------------------------------------------------------------------------


class ContentVersion(BaseModel):
    """Snapshot of page HTML before remediation."""

    id: str
    course_id: str
    page_id: str
    content_type: ContentType
    html_content: str
    created_at: datetime
    created_by: str  # "remediation" | "autoremedy" | "manual"
    remediation_job_id: Optional[str] = None


class PDFFixJob(BaseModel):
    """Background PDF fix job tracker."""

    id: str
    course_id: str
    session_id: str
    file_id: int
    filename: str
    status: ScanStatus = ScanStatus.PENDING
    progress: float = 0.0
    checks_before: Optional[CheckReportRef] = None
    checks_after: Optional[CheckReportRef] = None
    fixes_applied: list[str] = []
    fixes_skipped: list[str] = []
    upload_mode: str = "pending"  # "pending" | "replace" | "alongside"
    fixed_file_id: Optional[int] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    error: Optional[str] = None


class ConversionJob(BaseModel):
    """Background document conversion job tracker."""

    id: str
    course_id: str
    session_id: str
    file_id: int
    filename: str
    source_format: str = ""  # "docx", "pptx", "xlsx"
    status: ScanStatus = ScanStatus.PENDING
    progress: float = 0.0
    converted_html: str = ""
    canvas_page_id: Optional[int] = None
    canvas_page_url: Optional[str] = None
    # For multi-page output (CLU-67): list of Canvas page URLs for each
    # chapter. canvas_page_url remains the TOC page URL so existing link
    # replacement still points consumers to the right entry point.
    child_page_urls: list[str] = Field(default_factory=list)
    # CLU-69: when set, the document was deliberately skipped from
    # conversion (textbook / catalog / large reference doc). The original
    # file stays in place and link replacement is bypassed. Status is
    # still COMPLETED so the AutoRemedy phase 4 loop can keep running,
    # but downstream consumers must check this field before doing
    # archive / link-replace work.
    skip_reason: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    error: Optional[str] = None


class OCRJob(BaseModel):
    """Background OCR job tracker for scanned PDFs."""

    id: str
    course_id: str
    session_id: str
    file_id: int
    filename: str
    status: ScanStatus = ScanStatus.PENDING
    progress: float = 0.0
    pages_total: int = 0
    pages_processed: int = 0
    extracted_markdown: str = ""
    created_at: datetime
    completed_at: Optional[datetime] = None
    error: Optional[str] = None


class PageRemediationPlan(BaseModel):
    """Structured remediation plan for a page."""

    page_id: str
    page_title: str
    content_type: ContentType = ContentType.WIKI_PAGE
    strategy: str
    priority_reason: str
    confidence: float = 0.0
    target_issue_ids: list[str] = []
    target_asset_ids: list[str] = []
    rationale: str
    notes: list[str] = []


# Preview models
class PagePreview(BaseModel):
    """Before/after preview of a page."""

    page_id: str
    title: str
    original_html: str
    remediated_html: str
    issues_fixed: list[str] = []


# Design suggestion models
class DesignSuggestionRequest(BaseModel):
    """Natural-language request for design guidance."""

    design_prompt: str = Field(..., min_length=5, max_length=2000)
    campus: Optional[Campus] = None


class DesignSuggestion(BaseModel):
    """AI-assisted design suggestion with WCAG/POCR guardrails."""

    design_name: str
    requested_prompt: str
    custom_colors: ColorScheme
    rationale: str
    layout_notes: list[str] = []
    validation: dict
    passes_wcag_aa: bool
    pocr_requirements: list[str] = []
    warnings: list[str] = []
    used_fallback: bool = False


# ---------------------------------------------------------------------------
# Admin / configuration models
# ---------------------------------------------------------------------------


class RuleConfig(BaseModel):
    """Admin-configurable rule settings."""

    rule_id: str
    severity_override: Optional[str] = None  # "error", "warning", "info", None=default
    enabled: bool = True
    notes: str = ""


class AdminSettings(BaseModel):
    """Institution-level admin settings."""

    deployment_id: str
    rule_configs: dict[str, RuleConfig] = {}  # keyed by rule_id
    auto_scan_enabled: bool = False
    auto_scan_interval_hours: int = 24
    updated_at: datetime


class BatchJob(BaseModel):
    """Multi-course batch operation tracker."""

    id: str
    deployment_id: str
    course_ids: list[str]
    operation: str  # "scan", "remediate", "audit_files"
    status: ScanStatus = ScanStatus.PENDING
    progress: float = 0.0
    courses_total: int = 0
    courses_completed: int = 0
    results: dict[str, str] = {}  # course_id -> status
    created_at: datetime
    completed_at: Optional[datetime] = None
    error: Optional[str] = None


class InstitutionDashboard(BaseModel):
    """Aggregated accessibility stats across all courses for an institution."""

    total_courses: int = 0
    total_pages_scanned: int = 0
    total_issues: int = 0
    total_issues_fixed: int = 0
    avg_score: float = 0.0
    courses_scanned: int = 0
    top_issues: list[dict] = []  # [{rule_id, count, severity}]


class ScheduledScan(BaseModel):
    """Scheduled scan configuration for a single course."""

    course_id: str
    last_scan_at: Optional[datetime] = None
    next_scan_at: Optional[datetime] = None
    enabled: bool = True


# ---------------------------------------------------------------------------
# CLU Captions models
# ---------------------------------------------------------------------------


class TranscriptionJob(BaseModel):
    """Background transcription job tracker for CLU Captions."""

    id: str
    course_id: str
    session_id: str
    video_id: str                        # YouTube video ID or Studio perspective UUID
    video_url: str
    video_title: str = ""
    video_duration: float = 0.0          # Seconds
    source_type: str = "youtube"         # "youtube" or "studio"
    status: ScanStatus = ScanStatus.PENDING
    phase: str = "downloading"           # downloading|extracting|segmenting|transcribing|generating|complete
    progress: float = 0.0
    chunks_total: int = 0
    chunks_transcribed: int = 0
    vtt_content: str = ""                # Generated VTT (inline, ~10-50KB)
    srt_content: str = ""                # Generated SRT
    vtt_hash: str = ""                   # SHA-256 for public serving
    segments_json: str = ""              # JSON segments for large payloads
    page_ids_injected: list[str] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now())
    completed_at: Optional[datetime] = None
    error: Optional[str] = None


class CourseVideoRecord(BaseModel):
    """Cached video discovered in a course (YouTube or Canvas Studio)."""

    id: str                              # f"{course_id}:{video_id}"
    course_id: str
    video_id: str
    video_url: str
    video_title: str = ""
    thumbnail_url: str = ""
    duration: float = 0.0
    page_id: str
    page_title: str = ""
    content_type: str = "wiki_page"
    source_type: str = "youtube"         # "youtube" or "studio"
    caption_status: str = "none"         # none|transcribing|ready|injected
    transcription_job_id: Optional[str] = None
    discovered_at: datetime = Field(default_factory=lambda: datetime.now())


# ---------------------------------------------------------------------------
# ACR (Accessibility Conformance Report) models
# ---------------------------------------------------------------------------


class ConformanceLevel(str, Enum):
    """VPAT/ACR conformance levels per WCAG criterion."""

    SUPPORTS = "Supports"
    PARTIALLY_SUPPORTS = "Partially Supports"
    DOES_NOT_SUPPORT = "Does Not Support"
    NOT_APPLICABLE = "Not Applicable"


class RemediationStatus(str, Enum):
    """Remediation status for individual artifacts."""

    NOT_REMEDIATED = "not_remediated"
    PARTIALLY_REMEDIATED = "partially_remediated"
    FULLY_REMEDIATED = "fully_remediated"


class CriterionRollup(BaseModel):
    """WCAG 2.2 success criterion conformance summary for ACR."""

    criterion_id: str  # "1.1.1", "1.3.1", etc.
    name: str  # "Non-text Content", "Info and Relationships", etc.
    level: str  # "A", "AA"
    conformance: ConformanceLevel
    remarks: str  # Explanation of conformance determination
    issue_count: int = 0  # Number of findings for this criterion
    pages_affected: int = 0  # Number of pages/files with this issue
    sample_artifacts: list[str] = []  # IDs of sample items showing the issue

    @computed_field
    @property
    def wcag_url(self) -> str:
        """WCAG 2.2 Understanding document URL for this criterion."""
        return f"https://www.w3.org/WAI/WCAG22/Understanding/{self.criterion_id.replace('.', '')}"


class FindingEvidence(BaseModel):
    """Individual accessibility finding within an artifact for ACR evidence."""

    rule_id: str  # e.g., "IMG001"
    wcag_criterion: str  # "1.1.1"
    severity: IssueSeverity
    message: str
    element_selector: Optional[str] = None  # CSS selector or XPath
    element_html: Optional[str] = None  # Snippet of problematic HTML
    remediation_applied: bool = False
    remediation_notes: Optional[str] = None


class ArtifactEvidence(BaseModel):
    """Supporting evidence for an individual content item in ACR."""

    artifact_id: str  # page_id, file_id, assignment_id, etc.
    artifact_type: ContentType  # PAGE, FILE, ASSIGNMENT, DISCUSSION, etc.
    title: str
    canvas_url: str
    content_type_mime: Optional[str] = None  # For files: "application/pdf", etc.
    findings: list[FindingEvidence] = []
    remediation_status: RemediationStatus = RemediationStatus.NOT_REMEDIATED
    scan_version: Optional[str] = None  # Hash of content when scanned


class CourseACR(BaseModel):
    """Accessibility Conformance Report for a Canvas course (VPAT 2.5 format)."""

    id: str  # ULID
    course_id: str  # Canvas course ID
    scan_run_id: str  # Links to the scan that generated this ACR
    generated_at: datetime
    vpat_edition: str = "VPAT 2.5Rev WCAG"
    wcag_version: str = "2.2"
    conformance_level: str = "AA"
    course_name: str
    course_url: str
    evaluator: str  # Name of institution/evaluator
    evaluation_methods: list[str] = ["automated"]  # ["automated", "manual", "assistive_tech"]
    overall_status: ConformanceLevel = ConformanceLevel.NOT_APPLICABLE
    criteria: list[CriterionRollup] = []
    evidence: list[ArtifactEvidence] = []
    # Pre/post remediation comparison
    pre_remediation_report_id: Optional[str] = None
    post_remediation_report_id: Optional[str] = None
    issues_before: int = 0
    issues_after: int = 0
    issues_fixed: int = 0
    pages_remediated: int = 0

    @computed_field
    @property
    def conformance_percentage(self) -> float:
        """Overall conformance percentage, density-aware (CLU-55).

        Delegates to ``scoring_service.compute_conformance_pct`` so the
        formula lives next to the course-level score formula. The old
        implementation counted partials as a flat 0.5 regardless of finding
        count, producing "91.7% Excellent" on courses with 264+ alt-text
        failures. The new formula decays partial credit as issue counts rise.
        """
        # Local import to avoid models ↔ services cycle at module load.
        from lti_app.services.scoring_service import compute_conformance_pct

        return compute_conformance_pct(self.criteria)

    @computed_field
    @property
    def score_band(self) -> str:
        """Coarse label for the conformance percentage.

        Frontend gauges read this so the verbal label can't drift from the
        number. Returns one of "excellent" | "good" | "needs_work" | "poor".
        """
        from lti_app.services.scoring_service import score_band

        return score_band(self.conformance_percentage)


class ACRJob(BaseModel):
    """Background ACR generation job tracker."""

    id: str
    course_id: str
    session_id: str
    status: ScanStatus = ScanStatus.PENDING
    progress: float = 0.0
    created_at: datetime
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    acr_id: Optional[str] = None  # Generated ACR ID on completion
