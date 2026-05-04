"""Institution-wide accessibility dashboard service."""

from collections import Counter

import structlog

from lti_app.db.repositories import ScanRepository, RemediationRepository
from lti_app.models import InstitutionDashboard
from lti_app.services.scoring_service import compute_course_score

_logger = structlog.get_logger(__name__)


class DashboardService:
    """Aggregates accessibility statistics across all courses for an institution."""

    def __init__(
        self,
        scan_repo: ScanRepository,
        remediation_repo: RemediationRepository,
    ):
        self._scan_repo = scan_repo
        self._remediation_repo = remediation_repo

    def get_institution_dashboard(self, deployment_id: str) -> InstitutionDashboard:
        """Build institution-wide dashboard by aggregating all scan reports.

        Args:
            deployment_id: The LTI deployment identifier for the institution.

        Returns:
            InstitutionDashboard with aggregated statistics.
        """
        dashboard = InstitutionDashboard()

        # Gather all reports from the scan repository
        reports = self._get_all_reports()
        if not reports:
            return dashboard

        total_issues = 0
        total_errors = 0
        total_pages = 0
        scores = []
        issue_counter: Counter = Counter()

        for report in reports:
            dashboard.courses_scanned += 1
            total_pages += report.pages_analyzed
            total_issues += report.total_issues
            total_errors += report.errors

            # Score via the unified scoring service (CLU-52). Density-aware
            # exp decay — never clamps to 0 the way the old formula did.
            if report.pages_analyzed > 0:
                scores.append(
                    compute_course_score(
                        report.errors, report.warnings, report.pages_analyzed
                    )
                )

            # Count top issues by rule_id
            for issue in report.issues:
                issue_counter[(issue.rule_id, issue.severity.value)] += 1

        dashboard.total_courses = len(reports)
        dashboard.total_pages_scanned = total_pages
        dashboard.total_issues = total_issues

        if scores:
            dashboard.avg_score = round(sum(scores) / len(scores), 1)

        # Build top issues list (top 10 most common)
        for (rule_id, severity), count in issue_counter.most_common(10):
            dashboard.top_issues.append(
                {"rule_id": rule_id, "count": count, "severity": severity}
            )

        # Get remediation stats
        dashboard.total_issues_fixed = self._get_total_fixes()

        return dashboard

    def _get_all_reports(self):
        """Retrieve all scan reports from the repository.

        Works with both in-memory and Firestore implementations.
        """
        # In-memory repos store reports in _reports dict
        if hasattr(self._scan_repo, "_reports"):
            from lti_app.models import AccessibilityReport

            return [
                AccessibilityReport.model_validate(d)
                for d in self._scan_repo._reports.values()
            ]

        # Firestore repos: query the collection
        if hasattr(self._scan_repo, "_reports_col"):
            from lti_app.models import AccessibilityReport

            docs = self._scan_repo._reports_col.stream()
            return [
                AccessibilityReport.model_validate(doc.to_dict()) for doc in docs
            ]

        return []

    def _get_total_fixes(self) -> int:
        """Count total issues fixed across all remediation jobs."""
        if hasattr(self._remediation_repo, "_jobs"):
            return sum(
                d.get("pages_remediated", 0)
                for d in self._remediation_repo._jobs.values()
                if d.get("status") == "completed"
            )

        if hasattr(self._remediation_repo, "_jobs_col"):
            total = 0
            docs = (
                self._remediation_repo._jobs_col.where("status", "==", "completed")
                .stream()
            )
            for doc in docs:
                data = doc.to_dict()
                total += data.get("pages_remediated", 0)
            return total

        return 0
