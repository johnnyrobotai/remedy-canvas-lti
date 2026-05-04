"""Accessibility rules for document and PDF links."""

import re

from bs4 import BeautifulSoup

from lti_app.models import AccessibilityIssue, IssueCategory, IssueSeverity
from lti_app.core.accessibility.rules.base import AccessibilityRule


class GoogleLinkRule(AccessibilityRule):
    """DOC001: Detect links to Google Docs/Sheets/Forms/Slides/Drive."""

    rule_id = "DOC001"
    severity = IssueSeverity.WARNING
    category = IssueCategory.DOCUMENTS
    wcag_criterion = "1.1.1"
    message_template = "Link to Google document — verify document is accessible"
    can_auto_fix = False
    fix_description = "Verify the linked Google document meets accessibility standards"

    GOOGLE_PATTERNS = {
        "docs.google.com/document": "Google Document",
        "docs.google.com/spreadsheets": "Google Sheet",
        "docs.google.com/presentation": "Google Slides",
        "docs.google.com/forms": "Google Form",
        "drive.google.com/file": "Google Drive file",
        "docs.google.com/file": "Google Drive file",
    }

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find links pointing to Google document URLs."""
        issues = []

        for link in soup.find_all("a"):
            href = link.get("href", "")
            if not href:
                continue

            href_lower = href.lower()
            for pattern, doc_type in self.GOOGLE_PATTERNS.items():
                if pattern in href_lower:
                    text = link.get_text(strip=True)[:40]
                    issues.append(
                        self.create_issue(
                            page_id=page_id,
                            message=f'Link to {doc_type} — verify document is accessible: "{text}" ({href[:60]})',
                            element_html=str(link)[:200],
                        )
                    )
                    break

        return issues


class CanvasAssetLinkRule(AccessibilityRule):
    """DOC002: Detect links to Canvas file/media assets (non-page content)."""

    rule_id = "DOC002"
    severity = IssueSeverity.INFO
    category = IssueCategory.DOCUMENTS
    wcag_criterion = "1.1.1"
    message_template = "Link to Canvas asset — consider converting to accessible HTML page"
    can_auto_fix = False
    fix_description = "Consider converting the linked asset to an accessible HTML page"

    # Patterns that indicate a Canvas page/assignment/discussion (skip these)
    _SKIP_PATTERNS = re.compile(
        r"/(pages|assignments|discussion_topics|quizzes|modules|announcements)/",
        re.IGNORECASE,
    )

    # Patterns that indicate a Canvas file or media asset
    _ASSET_PATTERNS = re.compile(
        r"/files/\d+|/media_objects/",
        re.IGNORECASE,
    )

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find links pointing to Canvas file or media assets."""
        issues = []

        for link in soup.find_all("a"):
            href = link.get("href", "")
            if not href:
                continue

            # Skip page/assignment/discussion links
            if self._SKIP_PATTERNS.search(href):
                continue

            # Check for Canvas file/media asset links
            if self._ASSET_PATTERNS.search(href):
                text = link.get_text(strip=True)[:40]
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f'Link to Canvas asset — consider converting to accessible HTML page: "{text}" ({href[:60]})',
                        element_html=str(link)[:200],
                    )
                )

        return issues


class PDFLinkRule(AccessibilityRule):
    """PDF001: Detect links to PDF documents."""

    rule_id = "PDF001"
    severity = IssueSeverity.WARNING
    category = IssueCategory.DOCUMENTS
    wcag_criterion = "1.1.1"
    message_template = "Link to PDF document — verify PDF is accessible"
    can_auto_fix = False
    fix_description = "Verify the linked PDF meets accessibility standards (tagged, readable, proper structure)"

    # Match .pdf extension (optionally followed by query string or fragment)
    _PDF_HREF_RE = re.compile(r"\.pdf(\?.*)?(\#.*)?$", re.IGNORECASE)

    # Match "pdf" in link text (case-insensitive)
    _PDF_TEXT_RE = re.compile(r"\bpdf\b", re.IGNORECASE)

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find links to PDF documents."""
        issues = []

        for link in soup.find_all("a"):
            href = link.get("href", "")
            if not href:
                continue

            text = link.get_text(strip=True)
            is_pdf = False

            # Check href ending in .pdf
            if self._PDF_HREF_RE.search(href):
                is_pdf = True

            # Check /files/ link where text mentions "pdf"
            if not is_pdf and "/files/" in href and self._PDF_TEXT_RE.search(text):
                is_pdf = True

            if is_pdf:
                display_text = text[:40] if text else "(no text)"
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f'Link to PDF document — verify PDF is accessible: "{display_text}" ({href[:60]})',
                        element_html=str(link)[:200],
                    )
                )

        return issues
