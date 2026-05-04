"""Accessibility rules for event handlers (WCAG 2.1.1 Keyboard)."""

import re

from bs4 import BeautifulSoup

from lti_app.models import AccessibilityIssue, IssueCategory, IssueSeverity
from lti_app.core.accessibility.rules.base import AccessibilityRule


# Mouse-only event handler attributes (no keyboard equivalent)
MOUSE_ONLY_EVENTS = {
    "onmouseover",
    "onmouseout",
    "onmousedown",
    "onmouseup",
    "ondblclick",
}

# Patterns indicating navigation in onchange handlers
NAVIGATION_PATTERNS = re.compile(
    r"(location\b|navigate\b|\.href\b|window\.open\b)", re.IGNORECASE
)


class DeviceDependentEventRule(AccessibilityRule):
    """EVT001: Element uses device-dependent (mouse-only) event handlers."""

    rule_id = "EVT001"
    severity = IssueSeverity.WARNING
    category = IssueCategory.EVENTS
    wcag_criterion = "2.1.1"
    message_template = "Device-dependent event handler — may not be keyboard accessible"
    can_auto_fix = False

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find elements with mouse-only event handlers."""
        issues = []

        for element in soup.find_all(True):  # All elements
            found_events = []
            for attr in element.attrs:
                if attr.lower() in MOUSE_ONLY_EVENTS:
                    found_events.append(attr.lower())

            if found_events:
                events_str = ", ".join(sorted(found_events))
                tag = element.name
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f"Device-dependent event handler ({events_str}) on <{tag}> — may not be keyboard accessible",
                        element_html=str(element),
                    )
                )

        return issues


class JavaScriptJumpMenuRule(AccessibilityRule):
    """EVT002: Select element navigates on change (JavaScript jump menu)."""

    rule_id = "EVT002"
    severity = IssueSeverity.WARNING
    category = IssueCategory.EVENTS
    wcag_criterion = "2.1.1"
    message_template = "JavaScript jump menu — select element navigates on change"
    can_auto_fix = False

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find select elements with onchange containing navigation."""
        issues = []

        for select in soup.find_all("select"):
            onchange = select.get("onchange", "")
            if not onchange:
                continue

            if NAVIGATION_PATTERNS.search(onchange):
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message="JavaScript jump menu — select element navigates on change",
                        element_html=str(select),
                    )
                )

        return issues
