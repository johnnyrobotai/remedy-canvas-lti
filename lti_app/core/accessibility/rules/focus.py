"""Accessibility rules for focus management (WCAG 2.4.11)."""

from bs4 import BeautifulSoup, Tag

from lti_app.models import AccessibilityIssue, IssueCategory, IssueSeverity
from lti_app.core.accessibility.rules.base import AccessibilityRule


class FocusNotObscuredRule(AccessibilityRule):
    """FOC001: Detect fixed/sticky elements that may obscure keyboard focus."""

    rule_id = "FOC001"
    severity = IssueSeverity.WARNING
    category = IssueCategory.FOCUS
    wcag_criterion = "2.4.11"
    message_template = "Fixed/sticky element may obscure focus"
    can_auto_fix = True
    fix_description = "Add scroll-margin to prevent focus from being obscured by fixed elements"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find elements with position:fixed or position:sticky that may obscure focus."""
        issues = []

        for el in soup.find_all(style=True):
            if not isinstance(el, Tag):
                continue
            style = el.get("style", "").lower()
            if "position" in style and ("fixed" in style or "sticky" in style):
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f"Element with position:fixed/sticky may obscure focused content: <{el.name}>",
                        element_html=str(el)[:200],
                    )
                )

        return issues
