"""Accessibility analysis orchestrator."""

import logging
from datetime import UTC, datetime
from typing import Optional
from ulid import ULID

from bs4 import BeautifulSoup

from lti_app.models import (
    AccessibilityIssue,
    AccessibilityReport,
    CoursePage,
)
from lti_app.core.accessibility.rules.base import AccessibilityRule
from lti_app.core.accessibility.rules.images import (
    SuspiciousAltTextRule,
    MissingAltTextRule, InadequateAltTextRule, DuplicateAltTextRule,
    LongAltTextRule, LinkedImageMissingAltRule,
    ImageWithTitleRule, InvalidLongdescRule,
)
from lti_app.core.accessibility.rules.headings import (
    H1UsedRule, SkippedHeadingLevelRule,
    EmptyHeadingRule, FakeHeadingRule,
    NoHeadingStructureRule, LongHeadingRule,
)
from lti_app.core.accessibility.rules.tables import (
    MissingTableHeadersRule,
    MissingScopeAttributeRule,
    MissingTableCaptionRule,
    EmptyTableHeaderRule,
    LayoutTableRule,
    SparseTableRule,
)
from lti_app.core.accessibility.rules.links import (
    NonDescriptiveLinkTextRule, RedundantTitleTextRule,
    EmptyLinkRule, FakeLinkTextRule, RawURLLinkTextRule,
    AdjacentDuplicateLinksRule, DocumentLinkRule, BrokenLinkSpaceRule,
    BrokenSamePageLinkRule, DuplicateLinkTextRule, RedundantEmptyLinkRule,
    ImageTextLinkMergeRule,
)
from lti_app.core.accessibility.rules.contrast import InsufficientContrastRule
from lti_app.core.accessibility.rules.target_size import TargetSizeRule
from lti_app.core.accessibility.rules.lists import PossibleListRule
from lti_app.core.accessibility.rules.math import LatexInContentRule
from lti_app.core.accessibility.rules.structure import (
    EmptyElementsRule, DeprecatedTagsRule, SmallTextRule, BrokenARIAReferenceRule,
    UnderlinedTextRule, JustifiedTextRule,
    DuplicateIdRule, AriaHiddenFocusableRule, MissingRequiredAriaPropsRule, InvalidAriaRoleRule,
)
from lti_app.core.accessibility.rules.media import (
    MediaWithoutCaptionsRule, AutoplayMediaRule,
    YouTubeAutoCaptionsRule, YouTubeNotFoundRule,
    CanvasStudioAutoCaptionsRule, CanvasStudioNotFoundRule,
)
from lti_app.core.accessibility.rules.focus import FocusNotObscuredRule
from lti_app.core.accessibility.rules.consistent_help import ConsistentHelpRule
from lti_app.core.accessibility.rules.forms import (
    RedundantEntryRule, AccessibleAuthenticationRule,
    EmptyFormLabelRule, MissingFieldsetRule, OrphanedFormLabelRule,
    PersonalDataAutocompleteRule,
)
from lti_app.core.accessibility.rules.buttons import EmptyButtonRule
from lti_app.core.accessibility.rules.documents import (
    GoogleLinkRule, CanvasAssetLinkRule, PDFLinkRule,
)
from lti_app.core.accessibility.rules.events import (
    DeviceDependentEventRule, JavaScriptJumpMenuRule,
)

logger = logging.getLogger(__name__)


class AccessibilityAnalyzer:
    """Analyze course pages for accessibility issues."""

    def __init__(self):
        """Initialize analyzer with all accessibility rules."""
        self.rules: list[AccessibilityRule] = [
            # Image rules (WCAG 1.1.1)
            MissingAltTextRule(),
            InadequateAltTextRule(),
            DuplicateAltTextRule(),
            LongAltTextRule(),
            LinkedImageMissingAltRule(),
            ImageWithTitleRule(),
            InvalidLongdescRule(),
            SuspiciousAltTextRule(),
            # Heading rules (WCAG 1.3.1)
            H1UsedRule(),
            SkippedHeadingLevelRule(),
            EmptyHeadingRule(),
            FakeHeadingRule(),
            NoHeadingStructureRule(),
            LongHeadingRule(),
            # Table rules (WCAG 1.3.1)
            MissingTableHeadersRule(),
            MissingScopeAttributeRule(),
            MissingTableCaptionRule(),
            EmptyTableHeaderRule(),
            LayoutTableRule(),
            SparseTableRule(),
            # Structure rules (WCAG 1.3.1, 4.1.2, 1.4.4, 1.4.8)
            PossibleListRule(),
            EmptyElementsRule(),
            DeprecatedTagsRule(),
            SmallTextRule(),
            BrokenARIAReferenceRule(),
            DuplicateIdRule(),
            AriaHiddenFocusableRule(),
            MissingRequiredAriaPropsRule(),
            InvalidAriaRoleRule(),
            UnderlinedTextRule(),
            JustifiedTextRule(),
            # Media rules (WCAG 1.2.2, 1.4.2)
            MediaWithoutCaptionsRule(),
            AutoplayMediaRule(),
            YouTubeAutoCaptionsRule(),
            YouTubeNotFoundRule(),
            CanvasStudioAutoCaptionsRule(),
            CanvasStudioNotFoundRule(),
            # Math rules (WCAG 1.3.1)
            LatexInContentRule(),
            # Link rules (WCAG 2.4.4)
            NonDescriptiveLinkTextRule(),
            RedundantTitleTextRule(),
            EmptyLinkRule(),
            FakeLinkTextRule(),
            RawURLLinkTextRule(),
            AdjacentDuplicateLinksRule(),
            DocumentLinkRule(),
            BrokenLinkSpaceRule(),
            BrokenSamePageLinkRule(),
            DuplicateLinkTextRule(),
            RedundantEmptyLinkRule(),
            ImageTextLinkMergeRule(),
            # Contrast rules (WCAG 1.4.3)
            InsufficientContrastRule(),
            # Target size rules (WCAG 2.5.8)
            TargetSizeRule(),
            # WCAG 2.2 rules
            FocusNotObscuredRule(),
            # HELP001 (ConsistentHelpRule) runs course-level, not per-page.
            # Registered separately in self.course_level_rules below.
            RedundantEntryRule(),
            AccessibleAuthenticationRule(),
            # Form rules (WCAG 1.3.1)
            EmptyFormLabelRule(),
            MissingFieldsetRule(),
            OrphanedFormLabelRule(),
            PersonalDataAutocompleteRule(),
            # Button rules (WCAG 1.1.1)
            EmptyButtonRule(),
            # Document rules (WCAG 1.1.1)
            GoogleLinkRule(),
            CanvasAssetLinkRule(),
            PDFLinkRule(),
            # Event handler rules (WCAG 2.1.1)
            DeviceDependentEventRule(),
            JavaScriptJumpMenuRule(),
        ]

        # Course-level rules run once per analyze_course() call, after
        # the per-page pass. They look at patterns across pages (e.g.,
        # WCAG 3.2.6 consistent help placement) rather than examining
        # one page in isolation.
        self.course_level_rules: list[AccessibilityRule] = [
            ConsistentHelpRule(),
        ]

    async def analyze_page(self, page: CoursePage) -> list[AccessibilityIssue]:
        """Analyze a single page for accessibility issues.

        Args:
            page: The CoursePage to analyze.

        Returns:
            List of AccessibilityIssue objects found on the page.
        """
        issues = []

        # Parse HTML
        soup = BeautifulSoup(page.html_content, "html.parser")

        # Run each rule
        for rule in self.rules:
            try:
                rule_issues = rule.check(soup, page.id)
                # Stamp stable page_identifier on each issue for cross-fetch matching
                for issue in rule_issues:
                    issue.page_identifier = page.identifier
                issues.extend(rule_issues)
            except Exception as e:
                logger.error(f"Rule {rule.rule_id} failed on page {page.id}: {e}")

        return issues

    async def analyze_course(
        self, pages: list[CoursePage], course_id: str
    ) -> AccessibilityReport:
        """Analyze all pages in a course.

        Args:
            pages: List of CoursePage objects to analyze.
            course_id: Unique identifier for the course.

        Returns:
            AccessibilityReport with all issues found.
        """
        logger.info(f"Analyzing {len(pages)} pages for accessibility issues")

        all_issues = []
        images_needing_alt = 0

        for page in pages:
            page_issues = await self.analyze_page(page)
            all_issues.extend(page_issues)

            # Count images needing alt text
            for issue in page_issues:
                if issue.rule_id in ["IMG001", "IMG002"]:
                    images_needing_alt += 1

        # Course-level rules (e.g. HELP001 consistency) run once with
        # the full page list. Isolated per-rule failures are logged but
        # never crash the whole report.
        for rule in self.course_level_rules:
            try:
                all_issues.extend(rule.check_course(pages))
            except Exception as e:
                logger.error(f"Course-level rule {rule.rule_id} failed: {e}")

        # Calculate summary stats
        errors = sum(1 for i in all_issues if i.severity.value == "error")
        warnings = sum(1 for i in all_issues if i.severity.value == "warning")
        info = sum(1 for i in all_issues if i.severity.value == "info")

        report = AccessibilityReport(
            course_id=course_id,
            analyzed_at=datetime.now(UTC),
            total_issues=len(all_issues),
            errors=errors,
            warnings=warnings,
            info=info,
            issues=all_issues,
            pages_analyzed=len(pages),
            images_needing_alt=images_needing_alt,
        )

        logger.info(
            f"Analysis complete: {report.total_issues} issues "
            f"({errors} errors, {warnings} warnings, {info} info)"
        )

        return report

    def get_issues_by_category(
        self, issues: list[AccessibilityIssue], category: str
    ) -> list[AccessibilityIssue]:
        """Filter issues by category."""
        return [i for i in issues if i.category.value == category]

    def get_auto_fixable_issues(
        self, issues: list[AccessibilityIssue]
    ) -> list[AccessibilityIssue]:
        """Get issues that can be automatically fixed."""
        return [i for i in issues if i.can_auto_fix]
