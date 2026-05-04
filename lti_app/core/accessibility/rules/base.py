"""Base class for accessibility rules."""

from abc import ABC, abstractmethod

from bs4 import BeautifulSoup

from lti_app.models import AccessibilityIssue, IssueCategory, IssueSeverity


class AccessibilityRule(ABC):
    """Abstract base class for accessibility rules."""

    rule_id: str
    severity: IssueSeverity
    category: IssueCategory
    wcag_criterion: str
    message_template: str
    can_auto_fix: bool = False
    fix_description: str = ""

    @abstractmethod
    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Check the page for this accessibility issue.

        Args:
            soup: Parsed HTML content.
            page_id: Identifier for the page being checked.

        Returns:
            List of AccessibilityIssue objects found.
        """
        pass

    def create_issue(
        self,
        page_id: str,
        message: str,
        element_html: str = None,
        line_number: int = None,
        page_identifier: str = None,
    ) -> AccessibilityIssue:
        """Create an AccessibilityIssue with this rule's defaults.

        Args:
            page_id: Identifier for the page.
            message: Specific message for this instance.
            element_html: HTML snippet of the problematic element.
            line_number: Line number in the source.
            page_identifier: Stable Canvas identifier (e.g. 'page-123').

        Returns:
            AccessibilityIssue instance.
        """
        from ulid import ULID

        return AccessibilityIssue(
            id=str(ULID()),
            rule_id=self.rule_id,
            severity=self.severity,
            category=self.category,
            wcag_criterion=self.wcag_criterion,
            message=message,
            page_id=page_id,
            page_identifier=page_identifier,
            element_html=element_html[:200] if element_html else None,
            line_number=line_number,
            can_auto_fix=self.can_auto_fix,
            fix_description=self.fix_description if self.can_auto_fix else None,
        )
