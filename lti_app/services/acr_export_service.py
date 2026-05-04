"""ACR export service for generating HTML reports from Accessibility Conformance Reports."""

from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog
from jinja2 import Environment, PackageLoader, select_autoescape

from lti_app.models import CourseACR, CriterionRollup, ConformanceLevel

_logger = structlog.get_logger(__name__)

# Initialize Jinja2 environment
_jinja_env = Environment(
    loader=PackageLoader("lti_app", "templates"),
    autoescape=select_autoescape(["html", "xml"]),
)


def _get_conformance_badge_class(conformance: ConformanceLevel) -> str:
    """Get CSS class for conformance badge."""
    return {
        ConformanceLevel.SUPPORTS: "bg-green-100 text-green-800",
        ConformanceLevel.PARTIALLY_SUPPORTS: "bg-yellow-100 text-yellow-800",
        ConformanceLevel.DOES_NOT_SUPPORT: "bg-red-100 text-red-800",
        ConformanceLevel.NOT_APPLICABLE: "bg-gray-100 text-gray-800",
    }.get(conformance, "bg-gray-100 text-gray-800")


def _get_conformance_icon(conformance: ConformanceLevel) -> str:
    """Get icon for conformance level."""
    return {
        ConformanceLevel.SUPPORTS: "✅",
        ConformanceLevel.PARTIALLY_SUPPORTS: "⚠️",
        ConformanceLevel.DOES_NOT_SUPPORT: "❌",
        ConformanceLevel.NOT_APPLICABLE: "➖",
    }.get(conformance, "➖")


class ACRExportService:
    """Service for exporting ACRs to various formats."""

    def __init__(self, template_dir: Optional[Path] = None):
        """Initialize export service.

        Args:
            template_dir: Optional custom template directory
        """
        if template_dir:
            from jinja2 import FileSystemLoader
            self._env = Environment(
                loader=FileSystemLoader(template_dir),
                autoescape=select_autoescape(["html", "xml"]),
            )
        else:
            self._env = _jinja_env

        # Register template filters
        self._env.filters["conformance_badge"] = _get_conformance_badge_class
        self._env.filters["conformance_icon"] = _get_conformance_icon

    def export_html(self, acr: CourseACR, include_evidence: bool = True) -> str:
        """Export ACR to HTML format.

        Args:
            acr: The CourseACR to export
            include_evidence: Whether to include detailed artifact evidence

        Returns:
            HTML string
        """
        template = self._env.get_template("acr_report.html")

        # Calculate statistics
        level_a_criteria = [c for c in acr.criteria if c.level == "A"]
        level_aa_criteria = [c for c in acr.criteria if c.level == "AA"]

        stats = {
            "total_criteria": len(acr.criteria),
            "level_a_total": len(level_a_criteria),
            "level_a_supports": sum(1 for c in level_a_criteria if c.conformance == ConformanceLevel.SUPPORTS),
            "level_a_partial": sum(1 for c in level_a_criteria if c.conformance == ConformanceLevel.PARTIALLY_SUPPORTS),
            "level_a_fails": sum(1 for c in level_a_criteria if c.conformance == ConformanceLevel.DOES_NOT_SUPPORT),
            "level_aa_total": len(level_aa_criteria),
            "level_aa_supports": sum(1 for c in level_aa_criteria if c.conformance == ConformanceLevel.SUPPORTS),
            "level_aa_partial": sum(1 for c in level_aa_criteria if c.conformance == ConformanceLevel.PARTIALLY_SUPPORTS),
            "level_aa_fails": sum(1 for c in level_aa_criteria if c.conformance == ConformanceLevel.DOES_NOT_SUPPORT),
            "total_issues": sum(c.issue_count for c in acr.criteria),
            "total_pages_affected": sum(c.pages_affected for c in acr.criteria),
        }

        # Group criteria by category
        criteria_by_category: dict[str, list[CriterionRollup]] = {}
        for criterion in acr.criteria:
            category_id = criterion.criterion_id.rsplit(".", 1)[0]
            if category_id not in criteria_by_category:
                criteria_by_category[category_id] = []
            criteria_by_category[category_id].append(criterion)

        return template.render(
            acr=acr,
            stats=stats,
            criteria_by_category=criteria_by_category,
            include_evidence=include_evidence,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    def export_json(self, acr: CourseACR) -> str:
        """Export ACR to JSON format.

        Args:
            acr: The CourseACR to export

        Returns:
            JSON string
        """
        import json
        return json.dumps(acr.model_dump(mode="json"), indent=2, default=str)

    def export_markdown(self, acr: CourseACR) -> str:
        """Export ACR to Markdown format.

        Args:
            acr: The CourseACR to export

        Returns:
            Markdown string
        """
        lines = [
            f"# Accessibility Conformance Report",
            f"",
            f"**Course:** {acr.course_name}",
            f"**Generated:** {acr.generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"**Evaluator:** {acr.evaluator}",
            f"**VPAT Edition:** {acr.vpat_edition}",
            f"**WCAG Version:** {acr.wcag_version} Level {acr.conformance_level}",
            f"",
            f"## Executive Summary",
            f"",
            f"**Overall Status:** {acr.overall_status.value}",
            f"**Conformance Score:** {acr.conformance_percentage:.1f}%",
            f"",
        ]

        # Remediation comparison
        if acr.issues_before > 0:
            lines.extend([
                f"### Remediation Impact",
                f"",
                f"- **Issues Before:** {acr.issues_before}",
                f"- **Issues After:** {acr.issues_after}",
                f"- **Issues Fixed:** {acr.issues_fixed}",
                f"- **Pages Remediated:** {acr.pages_remediated}",
                f"",
            ])

        lines.extend([
            f"## WCAG Conformance Summary",
            f"",
            f"| Criterion | Level | Status | Issues | Pages Affected |",
            f"|-----------|-------|--------|--------|----------------|",
        ])

        for criterion in acr.criteria:
            lines.append(
                f"| {criterion.criterion_id} {criterion.name} | "
                f"{criterion.level} | {criterion.conformance.value} | "
                f"{criterion.issue_count} | {criterion.pages_affected} |"
            )

        lines.extend([
            f"",
            f"## Detailed Remarks",
            f"",
        ])

        for criterion in acr.criteria:
            if criterion.conformance != ConformanceLevel.SUPPORTS:
                lines.extend([
                    f"### {criterion.criterion_id} {criterion.name}",
                    f"",
                    f"**Level:** {criterion.level}",
                    f"**Status:** {criterion.conformance.value}",
                    f"**Issues:** {criterion.issue_count}",
                    f"",
                    f"{criterion.remarks}",
                    f"",
                ])

        return "\n".join(lines)

    def save_html_report(self, acr: CourseACR, output_path: Path, include_evidence: bool = True) -> None:
        """Save HTML report to file.

        Args:
            acr: The CourseACR to export
            output_path: Path to save the HTML file
            include_evidence: Whether to include detailed artifact evidence
        """
        html = self.export_html(acr, include_evidence)
        output_path.write_text(html, encoding="utf-8")
        _logger.info("acr_html_saved", acr_id=acr.id, path=str(output_path))
