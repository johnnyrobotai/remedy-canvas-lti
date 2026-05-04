"""Accessibility rules for target size (WCAG 2.5.8 Target Size Minimum)."""

import re

from bs4 import BeautifulSoup, Tag

from lti_app.models import AccessibilityIssue, IssueCategory, IssueSeverity
from lti_app.core.accessibility.rules.base import AccessibilityRule


class TargetSizeRule(AccessibilityRule):
    """TGT001: Detect interactive elements with insufficient target size.

    WCAG 2.5.8 requires clickable targets to be at least 24x24 CSS pixels,
    unless they are inline in text, have 24px spacing, or are user-agent controlled.
    """

    rule_id = "TGT001"
    severity = IssueSeverity.WARNING
    category = IssueCategory.STRUCTURE
    wcag_criterion = "2.5.8"
    message_template = "Interactive element may have insufficient target size"
    can_auto_fix = True
    fix_description = "Ensure minimum 24x24px target size for interactive elements"

    MIN_TARGET_PX = 24

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []

        for link in soup.find_all("a"):
            issue = self._check_element(link, page_id)
            if issue:
                issues.append(issue)

        for button in soup.find_all("button"):
            issue = self._check_element(button, page_id)
            if issue:
                issues.append(issue)

        return issues

    def _check_element(self, element: Tag, page_id: str):
        """Check if an interactive element has a small explicit target size."""
        # Skip inline links in running text -- exempt per WCAG 2.5.8
        if self._is_inline_in_text(element):
            return None

        # Check for image-only links with small explicit dimensions
        img = element.find("img")
        if img and not element.get_text(strip=True):
            w = self._get_dimension(img, "width")
            h = self._get_dimension(img, "height")
            if w is not None and w < self.MIN_TARGET_PX:
                return self.create_issue(
                    page_id=page_id,
                    message=f"Image link target is {w}px wide (minimum 24px)",
                    element_html=str(element)[:200],
                )
            if h is not None and h < self.MIN_TARGET_PX:
                return self.create_issue(
                    page_id=page_id,
                    message=f"Image link target is {h}px tall (minimum 24px)",
                    element_html=str(element)[:200],
                )

        # Check for explicit small dimensions in inline styles
        style = element.get("style", "")
        if style:
            w = self._get_style_dimension(style, "width")
            h = self._get_style_dimension(style, "height")
            if w is not None and w < self.MIN_TARGET_PX:
                return self.create_issue(
                    page_id=page_id,
                    message=f"Interactive element width is {w:.0f}px (minimum 24px)",
                    element_html=str(element)[:200],
                )
            if h is not None and h < self.MIN_TARGET_PX:
                return self.create_issue(
                    page_id=page_id,
                    message=f"Interactive element height is {h:.0f}px (minimum 24px)",
                    element_html=str(element)[:200],
                )

        return None

    def _is_inline_in_text(self, element: Tag) -> bool:
        """Check if an element is inline within running text (exempt from target size)."""
        parent = element.parent
        if not parent:
            return False

        # If parent is a block element with mixed text + link content, it's inline
        if parent.name in ("p", "span", "em", "strong", "b", "i", "li", "td", "th", "label"):
            # Check if there's substantial text content besides this element
            parent_text = parent.get_text(strip=True)
            element_text = element.get_text(strip=True)
            remaining = len(parent_text) - len(element_text)
            if remaining > 5:
                return True

        return False

    def _get_dimension(self, element: Tag, attr: str):
        """Get a dimension from an HTML attribute, return as float px or None."""
        val = element.get(attr)
        if val is None:
            return None
        try:
            return float(str(val).replace("px", "").strip())
        except (ValueError, TypeError):
            return None

    def _get_style_dimension(self, style: str, prop: str):
        """Extract a dimension in px from inline style."""
        match = re.search(rf"(?:^|;|\s){prop}\s*:\s*([\d.]+)\s*px", style)
        if match:
            return float(match.group(1))
        return None
