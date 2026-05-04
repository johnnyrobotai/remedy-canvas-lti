"""Accessibility rules for headings (WCAG 1.3.1 Info and Relationships)."""

import re

from bs4 import BeautifulSoup

from lti_app.models import AccessibilityIssue, IssueCategory, IssueSeverity
from lti_app.core.accessibility.rules.base import AccessibilityRule


# Canvas RCE auto-applies these class names to anchors that point at
# course Files. The visible body text on such pages is just the link
# text (often the filename, ~50 chars), but Canvas client-side injects
# a Scribd preview / DocumentCloud iframe AFTER page load, so the
# rendered page is meaningful even though the static body looks empty.
# NoHeadingStructureRule treats any of these as "structural content
# worth labeling" so the page-title H2 still gets injected.
_CANVAS_FILE_LINK_CLASSES = re.compile(
    r"(?:^|\s)(?:instructure_scribd_file|instructure_file_link|"
    r"preview_in_overlay|inline_disabled)(?:\s|$)"
)


class H1UsedRule(AccessibilityRule):
    """HDG001: Check for H1 tags (Canvas forbids H1 in content)."""

    rule_id = "HDG001"
    severity = IssueSeverity.ERROR
    category = IssueCategory.HEADINGS
    wcag_criterion = "1.3.1"
    message_template = "H1 tag used (Canvas content should start with H2)"
    can_auto_fix = True
    fix_description = "Convert H1 to H2 and shift all other headings"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find H1 tags in the content."""
        issues = []

        # Only check within body content, not the full document
        body = soup.find("body") or soup

        for h1 in body.find_all("h1"):
            text = h1.get_text(strip=True)[:50]
            issues.append(
                self.create_issue(
                    page_id=page_id,
                    message=f'H1 tag found: "{text}..." - Canvas reserves H1 for page title',
                    element_html=str(h1),
                )
            )

        return issues


class SkippedHeadingLevelRule(AccessibilityRule):
    """HDG002: Check for skipped heading levels (e.g., H2 -> H4)."""

    rule_id = "HDG002"
    severity = IssueSeverity.WARNING
    category = IssueCategory.HEADINGS
    wcag_criterion = "1.3.1"
    message_template = "Heading level skipped"
    can_auto_fix = True
    fix_description = "Adjust heading levels to maintain proper hierarchy"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find skipped heading levels."""
        issues = []

        # Only check within body content
        body = soup.find("body") or soup

        # Find all headings in order
        headings = body.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])

        if not headings:
            return issues

        # Canvas provides H1 as page title, so content should start at H2.
        # An H3 without a preceding H2 must be caught.
        prev_level = 1

        for heading in headings:
            # Get heading level (h1 = 1, h2 = 2, etc.)
            current_level = int(heading.name[1])

            # Check if level was skipped (going deeper by more than 1)
            if current_level > prev_level + 1:
                text = heading.get_text(strip=True)[:30]
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f"Heading level skipped from H{prev_level} to H{current_level}: \"{text}...\"",
                        element_html=str(heading),
                    )
                )

            prev_level = current_level

        return issues


class EmptyHeadingRule(AccessibilityRule):
    """HDG003: Check for empty heading elements."""

    rule_id = "HDG003"
    severity = IssueSeverity.ERROR
    category = IssueCategory.HEADINGS
    wcag_criterion = "1.3.1"
    message_template = "Empty heading element"
    can_auto_fix = True
    fix_description = "Remove empty heading elements"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Detect empty headings per PopeTech algorithm.

        A heading is 'empty' if it contains no text AND no images with alt text.
        Headings containing an image with alt text are NOT empty.
        """
        issues = []
        body = soup.find("body") or soup
        for heading in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
            text = heading.get_text(strip=True)
            # Strip &nbsp; and check
            text = text.replace("\u00a0", "").strip()
            if text:
                continue  # Has text — not empty

            # Check for images with alt text
            imgs_with_alt = [img for img in heading.find_all("img") if img.get("alt", "").strip()]
            if imgs_with_alt:
                continue  # Has image with alt — not empty per PopeTech

            issues.append(
                self.create_issue(
                    page_id=page_id,
                    message=f"Empty <{heading.name}> element",
                    element_html=str(heading)[:200],
                )
            )
        return issues


class FakeHeadingRule(AccessibilityRule):
    """HDG004: Detect paragraphs styled as headings (bold + short text)."""

    rule_id = "HDG004"
    severity = IssueSeverity.WARNING
    category = IssueCategory.HEADINGS
    wcag_criterion = "1.3.1"
    message_template = "Paragraph appears to be a heading"
    can_auto_fix = True
    fix_description = "Convert bold paragraph to proper heading element"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        body = soup.find("body") or soup

        for p in body.find_all("p"):
            # Skip paragraphs inside lists or tables
            if p.find_parent(["li", "td", "th", "ul", "ol", "table"]):
                continue

            text = p.get_text(strip=True)
            if not text or len(text) > 80:
                continue

            # Check if entire paragraph is bold
            if self._is_all_bold(p):
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f'Paragraph styled as heading: "{text[:50]}"',
                        element_html=str(p)[:200],
                    )
                )
                continue

            # Check for large font-size in style (px, em, rem, pt, %)
            style = p.get("style", "")
            if style and self._has_large_font_size(style):
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f'Large-text paragraph may be a heading: "{text[:50]}"',
                        element_html=str(p)[:200],
                    )
                )

        return issues

    @staticmethod
    def _has_large_font_size(style: str) -> bool:
        """Check if inline style has a large font-size (>=20px, >=1.3em/rem, >=14pt, >=130%)."""
        # px
        match = re.search(r"font-size\s*:\s*([\d.]+)\s*px", style)
        if match and float(match.group(1)) >= 20:
            return True
        # em/rem
        match = re.search(r"font-size\s*:\s*([\d.]+)\s*(?:em|rem)", style)
        if match and float(match.group(1)) >= 1.3:
            return True
        # pt
        match = re.search(r"font-size\s*:\s*([\d.]+)\s*pt", style)
        if match and float(match.group(1)) >= 14:
            return True
        # %
        match = re.search(r"font-size\s*:\s*([\d.]+)\s*%", style)
        if match and float(match.group(1)) >= 130:
            return True
        return False

    @staticmethod
    def _is_all_bold(p) -> bool:
        """Check if a paragraph's entire content is wrapped in <strong> or <b>."""
        children = list(p.children)
        # Filter out whitespace text nodes
        meaningful = [c for c in children if not (isinstance(c, str) and not c.strip())]
        if len(meaningful) != 1:
            return False
        child = meaningful[0]
        if hasattr(child, "name") and child.name in ("strong", "b"):
            return True
        return False


class NoHeadingStructureRule(AccessibilityRule):
    """HDG005: Detect pages with text content but no heading structure."""

    rule_id = "HDG005"
    severity = IssueSeverity.WARNING
    category = IssueCategory.HEADINGS
    wcag_criterion = "1.3.1"
    message_template = "Page has no heading structure"
    can_auto_fix = True
    fix_description = "Add page title as H2 heading at top of content"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        body = soup.find("body") or soup

        # Check if any headings exist (h2-h6; h1 is page title in Canvas)
        headings = body.find_all(["h2", "h3", "h4", "h5", "h6"])
        if headings:
            return issues

        # Flag pages that contain "structural content worth labeling".
        #
        # The threshold has progressively widened as we've discovered
        # real-world Canvas page shapes that PopeTech flags but our
        # original rule missed:
        #
        #   1. text > 50 chars (the original)
        #   2. iframe / embed / object / video (Bug #1, ~189 pages)
        #   3. Canvas Scribd / file embed link (this update — Canvas
        #      injects the preview client-side so the body has only the
        #      <a class="instructure_scribd_file_link"> link tag)
        text = body.get_text(strip=True)
        embeds = body.find_all(["iframe", "embed", "object", "video"])
        canvas_file_links = body.find_all("a", class_=_CANVAS_FILE_LINK_CLASSES)

        if not (len(text) > 50 or embeds or canvas_file_links):
            return issues

        if canvas_file_links and len(text) <= 50 and not embeds:
            detail = (
                f"Page has {len(canvas_file_links)} Canvas file embed(s) "
                f"but no headings"
            )
        elif embeds and len(text) <= 50:
            detail = f"Page has {len(embeds)} embedded media element(s) but no headings"
        else:
            detail = f"Page has {len(text)} characters of text but no headings"

        issues.append(
            self.create_issue(
                page_id=page_id,
                message=detail,
                element_html=str(body)[:200],
            )
        )

        return issues


class LongHeadingRule(AccessibilityRule):
    """HDG006: Detect headings longer than 120 characters."""

    rule_id = "HDG006"
    severity = IssueSeverity.WARNING
    category = IssueCategory.HEADINGS
    wcag_criterion = "1.3.1"
    message_template = "Heading text is too long"
    can_auto_fix = True
    fix_description = "Truncate heading at word boundary and move remainder to paragraph"

    MAX_HEADING_CHARS = 120

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        body = soup.find("body") or soup

        for heading in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
            text = heading.get_text(strip=True)
            if len(text) > self.MAX_HEADING_CHARS:
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f"Heading is {len(text)} characters (max {self.MAX_HEADING_CHARS}): \"{text[:50]}...\"",
                        element_html=str(heading)[:200],
                    )
                )

        return issues
