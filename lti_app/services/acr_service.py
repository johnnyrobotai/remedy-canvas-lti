"""ACR (Accessibility Conformance Report) generation service.

Generates VPAT 2.5 format reports from scan and file audit results.
"""

from collections import defaultdict
from datetime import UTC, datetime
from typing import Optional

import structlog
from ulid import ULID

from lti_app.core.accessibility.vpat import (
    AA_CRITERIA_IDS,
    calculate_conformance_level,
    get_aa_criteria,
    get_criterion,
    map_rule_to_criterion,
)
from lti_app.db.repositories import ACRRepository, ScanRepository
from lti_app.models import (
    ACRJob,
    AccessibilityIssue,
    AccessibilityReport,
    ArtifactEvidence,
    ConformanceLevel,
    ContentType,
    CourseACR,
    CriterionRollup,
    FileAuditEntry,
    FileReport,
    FindingEvidence,
    RemediationStatus,
    ScanStatus,
)

_logger = structlog.get_logger(__name__)


class ACRService:
    """Generates Accessibility Conformance Reports from scan results."""

    def __init__(self, acr_repo: ACRRepository, scan_repo: ScanRepository):
        self._acr_repo = acr_repo
        self._scan_repo = scan_repo

    def create_job(self, session_id: str, course_id: str) -> ACRJob:
        """Create a PENDING ACR generation job."""
        job = ACRJob(
            id=str(ULID()),
            course_id=course_id,
            session_id=session_id,
            status=ScanStatus.PENDING,
            created_at=datetime.now(UTC),
        )
        self._acr_repo.save_job(job)
        return job

    async def generate_acr(
        self,
        job_id: str,
        course_id: str,
        course_name: str,
        course_url: str,
        evaluator: str,
        report: AccessibilityReport,
        file_report: Optional[FileReport] = None,
        pre_remediation_report: Optional[AccessibilityReport] = None,
    ) -> Optional[CourseACR]:
        """Generate a CourseACR from scan and file audit results.

        Args:
            job_id: ACR job ID for tracking
            course_id: Canvas course ID
            course_name: Canvas course name
            course_url: Canvas course URL
            evaluator: Name of institution/evaluator
            report: Current accessibility scan report
            file_report: Optional file audit report
            pre_remediation_report: Optional pre-remediation report for comparison

        Returns:
            Generated CourseACR or None if generation failed
        """
        job = self._acr_repo.get_job(job_id)
        if not job:
            _logger.error("acr_job_not_found", job_id=job_id)
            return None

        try:
            job.status = ScanStatus.RUNNING
            self._acr_repo.save_job(job)

            # Build criterion rollups and evidence
            criteria = self._build_criteria(report, file_report)
            evidence = self._build_evidence(report, file_report)

            # Calculate overall status
            overall_status = self._calculate_overall_status(criteria)

            # Calculate issues before/after if pre-remediation report provided
            issues_before = 0
            issues_after = report.total_issues
            issues_fixed = 0
            if pre_remediation_report:
                issues_before = pre_remediation_report.total_issues
                issues_fixed = max(0, issues_before - issues_after)

            # Create ACR
            acr = CourseACR(
                id=str(ULID()),
                course_id=course_id,
                scan_run_id=report.course_id,  # Using report's internal course_id reference
                generated_at=datetime.now(UTC),
                course_name=course_name,
                course_url=course_url,
                evaluator=evaluator,
                overall_status=overall_status,
                criteria=criteria,
                evidence=evidence,
                issues_before=issues_before,
                issues_after=issues_after,
                issues_fixed=issues_fixed,
                pages_remediated=sum(
                    1 for e in evidence if e.remediation_status != RemediationStatus.NOT_REMEDIATED
                ),
            )

            # Save ACR
            self._acr_repo.save_acr(acr)

            # Update job
            job.status = ScanStatus.COMPLETED
            job.acr_id = acr.id
            job.completed_at = datetime.now(UTC)
            self._acr_repo.save_job(job)

            _logger.info(
                "acr_generated",
                job_id=job_id,
                acr_id=acr.id,
                course_id=course_id,
                conformance_pct=round(acr.conformance_percentage, 1),
            )

            return acr

        except Exception as e:
            _logger.error("acr_generation_failed", job_id=job_id, error=str(e))
            job.status = ScanStatus.FAILED
            job.error = str(e)
            job.completed_at = datetime.now(UTC)
            self._acr_repo.save_job(job)
            return None

    def _build_criteria(
        self, report: AccessibilityReport, file_report: Optional[FileReport]
    ) -> list[CriterionRollup]:
        """Build criterion rollups from issues.

        Aggregates issues by WCAG criterion and calculates conformance.
        """
        # Group issues by WCAG criterion
        issues_by_criterion: dict[str, list[AccessibilityIssue]] = defaultdict(list)

        for issue in report.issues:
            criterion_id = issue.wcag_criterion
            if criterion_id:
                issues_by_criterion[criterion_id].append(issue)

        # Add file audit issues if available
        if file_report:
            # Map PDF checks to WCAG criteria
            for entry in file_report.entries:
                if entry.is_pdf and entry.status == "failed":
                    # PDF failures typically map to 1.3.1 (Info and Relationships)
                    # or 1.1.1 (Non-text Content) for images without alt
                    issues_by_criterion["1.3.1"].append(
                        AccessibilityIssue(
                            id=f"file_{entry.file_id}",
                            rule_id="PDF_CHECK",
                            severity="error",
                            category="structure",
                            wcag_criterion="1.3.1",
                            message=f"PDF '{entry.filename}' failed accessibility checks",
                            page_id=str(entry.file_id),
                            canvas_url=f"/files/{entry.file_id}",
                        )
                    )

        # Build criteria list
        criteria = []
        aa_criteria = get_aa_criteria()

        for criterion_def in aa_criteria:
            criterion_id = criterion_def["id"]
            issues = issues_by_criterion.get(criterion_id, [])

            # Count unique pages affected (all issues for transparency)
            pages_affected = len({issue.page_id for issue in issues})

            # Only count course_content issues for conformance determination
            # Platform issues appear in counts/remarks but don't penalize conformance
            content_issues = [i for i in issues if getattr(i, 'source', 'course_content') == 'course_content']
            content_pages_affected = len({issue.page_id for issue in content_issues})

            total_pages = report.pages_analyzed
            conformance = ConformanceLevel.SUPPORTS

            if not content_issues:
                conformance = ConformanceLevel.SUPPORTS
            elif content_pages_affected == total_pages:
                conformance = ConformanceLevel.DOES_NOT_SUPPORT
            else:
                conformance = ConformanceLevel.PARTIALLY_SUPPORTS

            # Build remarks based on conformance
            remarks = self._build_remarks(
                criterion_def, issues, conformance, file_report=file_report,
            )

            # Sample artifact IDs (up to 3)
            sample_artifacts = list({issue.page_id for issue in issues})[:3]

            criteria.append(
                CriterionRollup(
                    criterion_id=criterion_id,
                    name=criterion_def["name"],
                    level=criterion_def["level"],
                    conformance=conformance,
                    remarks=remarks,
                    issue_count=len(issues),
                    pages_affected=pages_affected,
                    sample_artifacts=sample_artifacts,
                )
            )

        # Sort by criterion ID for consistent ordering
        criteria.sort(key=lambda c: c.criterion_id)
        return criteria

    def _build_evidence(
        self, report: AccessibilityReport, file_report: Optional[FileReport]
    ) -> list[ArtifactEvidence]:
        """Build artifact evidence from issues.

        Groups issues by page/file to create ArtifactEvidence records.
        """
        evidence_map: dict[str, ArtifactEvidence] = {}

        # Process HTML content issues
        for issue in report.issues:
            page_id = issue.page_id

            if page_id not in evidence_map:
                evidence_map[page_id] = ArtifactEvidence(
                    artifact_id=page_id,
                    artifact_type=ContentType.WIKI_PAGE,  # Default, will be refined
                    title=issue.page_identifier or page_id,
                    canvas_url=issue.canvas_url or "",
                    findings=[],
                )

            evidence_map[page_id].findings.append(
                FindingEvidence(
                    rule_id=issue.rule_id,
                    wcag_criterion=issue.wcag_criterion,
                    severity=issue.severity,
                    message=issue.message,
                    element_html=issue.element_html,
                    remediation_applied=issue.can_auto_fix,
                    remediation_notes=issue.fix_description,
                )
            )

        # Process file audit entries
        if file_report:
            for entry in file_report.entries:
                if entry.is_pdf and entry.status == "failed":
                    file_id = str(entry.file_id)
                    evidence_map[file_id] = ArtifactEvidence(
                        artifact_id=file_id,
                        artifact_type=ContentType.WIKI_PAGE,  # PDFs get converted to pages
                        title=entry.filename,
                        canvas_url=f"/files/{entry.file_id}",
                        content_type_mime=entry.content_type,
                        findings=[
                            FindingEvidence(
                                rule_id="PDF_ACCESSIBILITY",
                                wcag_criterion="1.3.1",
                                severity="error",
                                message=f"PDF accessibility check failed",
                            )
                        ],
                        remediation_status=RemediationStatus.NOT_REMEDIATED,
                    )

        return list(evidence_map.values())

    # Criteria whose rules (LNK007, PDF001, DOC001) point at linked
    # documents. When the ACR has a file_report, the per-file outcome
    # (remediation_status + skip_reason) is summarised here so the
    # instructor sees *why* a residual warning persists.
    _DOC_LINK_CRITERIA = {"1.1.1", "2.4.4"}

    def _build_remarks(
        self,
        criterion_def: dict,
        issues: list[AccessibilityIssue],
        conformance: ConformanceLevel,
        file_report: Optional[FileReport] = None,
    ) -> str:
        """Build remarks text for a criterion based on conformance level."""
        if conformance == ConformanceLevel.SUPPORTS:
            remarks = f"All content meets {criterion_def['id']} {criterion_def['name']}. No issues detected."
        else:
            issue_types = defaultdict(int)
            for issue in issues:
                issue_types[issue.rule_id] += 1

            top_issues = sorted(issue_types.items(), key=lambda x: x[1], reverse=True)[:3]
            issue_summary = ", ".join([f"{rule} ({count})" for rule, count in top_issues])

            if conformance == ConformanceLevel.DOES_NOT_SUPPORT:
                remarks = f"Content does not meet {criterion_def['id']}. {len(issues)} issues found: {issue_summary}."
            else:
                remarks = f"Content partially meets {criterion_def['id']}. {len(issues)} issues across {len({i.page_id for i in issues})} pages: {issue_summary}."

        # Append source breakdown if platform issues exist
        content_count = sum(1 for i in issues if getattr(i, 'source', 'course_content') == 'course_content')
        platform_count = sum(1 for i in issues if getattr(i, 'source', 'course_content') == 'canvas_platform')

        if platform_count > 0:
            remarks += f" ({content_count} in course content, {platform_count} in Canvas platform)"

        # For 1.1.1 / 2.4.4, append a per-file outcome breakdown when a
        # file audit report is available. This is what turns a bare
        # "LNK007 (6)" line into "LNK007 (6) — 3 converted, 2 skipped".
        if (
            file_report
            and criterion_def.get("id") in self._DOC_LINK_CRITERIA
            and any(
                i.rule_id in ("LNK007", "PDF001", "DOC001") for i in issues
            )
        ):
            breakdown = self._file_outcome_breakdown(file_report)
            if breakdown:
                remarks += f" {breakdown}"

        return remarks

    @staticmethod
    def _file_outcome_breakdown(file_report: FileReport) -> str:
        """Render a human-readable sentence summarising per-file outcomes.

        Returns an empty string when the report has no entries or no
        entries carry remediation_status metadata — callers should
        fall back to the bare rule-id summary in that case.
        """
        converted = 0
        pdf_fixed = 0
        audit_passed = 0
        skipped = 0
        skip_reasons: dict[str, int] = defaultdict(int)
        for entry in file_report.entries:
            status = getattr(entry, "remediation_status", None)
            if status == "converted":
                converted += 1
            elif status == "pdf_fixed":
                pdf_fixed += 1
            elif status == "audit_passed":
                audit_passed += 1
            elif status == "skipped":
                skipped += 1
                if entry.skip_reason:
                    # Collapse the detail tail ("PDF has 200 pages
                    # (>50 threshold)" → "oversized PDF") so the remark
                    # stays scannable without losing the "why" signal.
                    skip_reasons[
                        ACRService._classify_skip_reason(entry.skip_reason)
                    ] += 1

        parts: list[str] = []
        if converted:
            parts.append(f"{converted} converted to HTML pages")
        if pdf_fixed:
            parts.append(f"{pdf_fixed} PDF fixed in place")
        if audit_passed:
            parts.append(
                f"{audit_passed} PDF{'s' if audit_passed != 1 else ''} "
                f"passed accessibility audit"
            )
        if skipped:
            if skip_reasons:
                details = ", ".join(
                    f"{n} {reason}" for reason, n in sorted(
                        skip_reasons.items(), key=lambda x: -x[1]
                    )
                )
                parts.append(f"{skipped} skipped ({details})")
            else:
                parts.append(f"{skipped} skipped")

        if not parts:
            return ""
        return "Document remediation outcomes: " + "; ".join(parts) + "."

    @staticmethod
    def _classify_skip_reason(raw: str) -> str:
        """Map the verbose skip_reason into a short bucket for ACR remarks."""
        text = raw.lower()
        if "keyword" in text or "textbook" in text or "catalog" in text or "manual" in text:
            return "reference-doc keyword"
        if "pages" in text and "threshold" in text:
            return "oversized PDF"
        if "conversion failed" in text or "error" in text:
            return "conversion error"
        return "other"

    def _calculate_overall_status(self, criteria: list[CriterionRollup]) -> ConformanceLevel:
        """Calculate overall course conformance level.

        - Supports: All criteria Support
        - Partially Supports: Some criteria Partially Support, none Does Not Support
        - Does Not Support: At least one criterion Does Not Support
        """
        if not criteria:
            return ConformanceLevel.NOT_APPLICABLE

        has_not_supported = any(c.conformance == ConformanceLevel.DOES_NOT_SUPPORT for c in criteria)
        has_partial = any(c.conformance == ConformanceLevel.PARTIALLY_SUPPORTS for c in criteria)

        if has_not_supported:
            return ConformanceLevel.PARTIALLY_SUPPORTS  # Use partial if any don't support
        if has_partial:
            return ConformanceLevel.PARTIALLY_SUPPORTS
        return ConformanceLevel.SUPPORTS

    def get_acr(self, acr_id: str) -> Optional[CourseACR]:
        """Get an ACR by ID."""
        return self._acr_repo.get_acr(acr_id)

    def list_acrs_for_course(self, course_id: str) -> list[CourseACR]:
        """List all ACRs for a course."""
        return self._acr_repo.list_acrs_for_course(course_id)

    def get_latest_acr(self, course_id: str) -> Optional[CourseACR]:
        """Get the most recent ACR for a course."""
        acrs = self.list_acrs_for_course(course_id)
        if not acrs:
            return None
        return max(acrs, key=lambda a: a.generated_at)
