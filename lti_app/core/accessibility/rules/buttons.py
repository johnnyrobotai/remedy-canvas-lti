"""Empty button detection rule (BTN001).

PopeTech algorithm: A <button> element contains no text content (or alternative
text), or an <input type="submit|button|reset"> has an empty or missing value.
"""

from bs4 import BeautifulSoup, Tag

from lti_app.core.accessibility.rules.base import AccessibilityRule
from lti_app.models import IssueCategory, IssueSeverity


class EmptyButtonRule(AccessibilityRule):
    """Detect empty buttons per PopeTech algorithm."""

    rule_id = "BTN001"
    severity = IssueSeverity.ERROR
    category = IssueCategory.STRUCTURE
    wcag_criterion = "1.1.1"
    message_template = "Empty button — button contains no text or alternative text"
    can_auto_fix = True
    fix_description = "Add accessible label to empty button"

    def check(self, soup: BeautifulSoup, page_id: str) -> list:
        issues = []

        # Check <button> elements
        for button in soup.find_all("button"):
            if not isinstance(button, Tag):
                continue
            text = button.get_text(strip=True).replace("\u00a0", "").strip()
            if text:
                continue
            if button.get("aria-label", "").strip():
                continue
            if button.get("aria-labelledby", "").strip():
                continue
            # Check for images with alt inside button
            if any(img.get("alt", "").strip() for img in button.find_all("img")):
                continue

            issues.append(
                self.create_issue(
                    page_id=page_id,
                    message="Empty button \u2014 button contains no text or alternative text",
                    element_html=str(button),
                )
            )

        # Check <input type="submit|button|reset">
        for inp in soup.find_all("input", type=["submit", "button", "reset"]):
            if not isinstance(inp, Tag):
                continue
            if inp.get("value", "").strip():
                continue
            if inp.get("aria-label", "").strip():
                continue

            inp_type = inp.get("type", "button")
            issues.append(
                self.create_issue(
                    page_id=page_id,
                    message=f"Empty {inp_type} input \u2014 missing value attribute",
                    element_html=str(inp),
                )
            )

        return issues
