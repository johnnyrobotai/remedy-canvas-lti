"""Accessibility rules for links (WCAG 2.4.4 Link Purpose)."""

import re

from bs4 import BeautifulSoup

from lti_app.models import AccessibilityIssue, IssueCategory, IssueSeverity
from lti_app.core.accessibility.rules.base import AccessibilityRule


class NonDescriptiveLinkTextRule(AccessibilityRule):
    """LNK001: Check for non-descriptive link text."""

    rule_id = "LNK001"
    severity = IssueSeverity.WARNING
    category = IssueCategory.LINKS
    wcag_criterion = "2.4.4"
    message_template = "Link text is not descriptive"
    can_auto_fix = True
    fix_description = "Fix non-descriptive link text or unwrap broken placeholder links"

    # Common non-descriptive link texts
    NON_DESCRIPTIVE = {
        "click here",
        "click",
        "here",
        "link",
        "read more",
        "more",
        "learn more",
        "this",
        "this link",
        "this page",
        "continue",
        "go",
        "see more",
        "view",
        "view more",
        "details",
        "info",
        "information",
        "download",
    }

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find links with non-descriptive text."""
        issues = []

        for link in soup.find_all("a"):
            # Get link text
            text = link.get_text(strip=True).lower()

            if not text:
                continue  # Empty links handled by LNK003

            if self._is_url(text):
                continue  # URL text handled by LNK005

            # Check for exact match
            if text in self.NON_DESCRIPTIVE:
                href = link.get("href", "")[:50]
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f'Link text "{text}" is not descriptive (href: {href})',
                        element_html=str(link),
                    )
                )
                continue

            # Check for "click here for..." and similar prefix patterns
            if any(text.startswith(prefix) for prefix in self._NON_DESCRIPTIVE_PREFIXES):
                href = link.get("href", "")[:50]
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f'Link text "{text[:40]}" starts with non-descriptive phrase (href: {href})',
                        element_html=str(link),
                    )
                )

        return issues

    _NON_DESCRIPTIVE_PREFIXES = (
        "click here",
        "click this",
        "click on",
        "click to",
        "click for",
        "tap here",
        "tap to",
        "go here",
        "read more",
        "learn more",
        "see more",
        "view more",
        "find out more",
        "more info",
    )

    @staticmethod
    def _is_url(text: str) -> bool:
        """Check if text appears to be a URL."""
        return any(indicator in text for indicator in ["http://", "https://", "www."])


class RedundantTitleTextRule(AccessibilityRule):
    """LNK002: Check for links with redundant title attributes."""

    rule_id = "LNK002"
    severity = IssueSeverity.WARNING
    category = IssueCategory.LINKS
    wcag_criterion = "2.4.4"
    message_template = "Link has redundant title text"
    can_auto_fix = True
    fix_description = "Remove title attribute that duplicates link text"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find links where title duplicates link text."""
        issues = []

        for link in soup.find_all("a"):
            title = link.get("title", "").strip()
            if not title:
                continue
            text = link.get_text(strip=True)
            norm_title = self._normalize(title)
            norm_text = self._normalize(text)
            if norm_title and norm_text and (
                norm_title == norm_text
                or norm_title.startswith(norm_text)
                or norm_text.startswith(norm_title)
            ):
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f'Link title "{title[:50]}" duplicates link text',
                        element_html=str(link),
                    )
                )

        return issues

    def _normalize(self, text: str) -> str:
        """Normalize text for comparison: lowercase, strip extensions/punctuation."""
        text = text.lower().strip()
        text = re.sub(r"\.(pdf|docx?|txt|xlsx?|pptx?)$", "", text)
        text = re.sub(r"[^\w\s]", "", text)
        return " ".join(text.split())


class EmptyLinkRule(AccessibilityRule):
    """LNK003: Check for links with no text, no aria-label, and no child img with alt."""

    rule_id = "LNK003"
    severity = IssueSeverity.ERROR
    category = IssueCategory.LINKS
    wcag_criterion = "2.4.4"
    message_template = "Link has no accessible text"
    can_auto_fix = True
    fix_description = "Add aria-label derived from link href"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        for link in soup.find_all("a"):
            text = link.get_text(strip=True)
            if text:
                continue
            if link.get("aria-label"):
                continue
            # Check for child img with alt text
            img = link.find("img")
            if img and img.get("alt", "").strip():
                continue
            href = link.get("href", "")
            issues.append(
                self.create_issue(
                    page_id=page_id,
                    message=f"Link has no accessible text: {href[:60]}",
                    element_html=str(link)[:200],
                )
            )
        return issues


class FakeLinkTextRule(AccessibilityRule):
    """LNK004: Check for fake link text patterns like 'click here', 'read more'."""

    rule_id = "LNK004"
    severity = IssueSeverity.WARNING
    category = IssueCategory.LINKS
    wcag_criterion = "2.4.4"
    message_template = "Link uses generic text that does not describe the destination"
    can_auto_fix = False
    fix_description = "Rewrite link text to describe the destination"

    FAKE_TEXT = {
        "click here", "click", "here", "read more", "more",
        "learn more", "link", "this", "this link", "this page",
        "see more", "view", "view more",
    }

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        for link in soup.find_all("a"):
            text = link.get_text(strip=True).lower()
            if not text:
                continue
            if text in self.FAKE_TEXT:
                href = link.get("href", "")[:50]
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f'Link uses generic text "{text}" (href: {href})',
                        element_html=str(link)[:200],
                    )
                )
        return issues


class RawURLLinkTextRule(AccessibilityRule):
    """LNK005: Check for links where the visible text is a raw URL."""

    rule_id = "LNK005"
    severity = IssueSeverity.WARNING
    category = IssueCategory.LINKS
    wcag_criterion = "2.4.4"
    message_template = "Link text is a raw URL"
    can_auto_fix = True
    fix_description = "Replace URL text with descriptive link text derived from domain"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        for link in soup.find_all("a"):
            text = link.get_text(strip=True).lower()
            if not text:
                continue
            if self._is_url(text):
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f"Link text is a raw URL: {text[:60]}",
                        element_html=str(link)[:200],
                    )
                )
        return issues

    @staticmethod
    def _is_url(text: str) -> bool:
        return any(indicator in text for indicator in ["http://", "https://", "www."])


class AdjacentDuplicateLinksRule(AccessibilityRule):
    """LNK006: Check for adjacent links with the same href."""

    rule_id = "LNK006"
    severity = IssueSeverity.WARNING
    category = IssueCategory.LINKS
    wcag_criterion = "2.4.4"
    message_template = "Adjacent links point to the same destination"
    can_auto_fix = True
    fix_description = "Merge adjacent duplicate links into one"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        links = soup.find_all("a")
        for i in range(len(links) - 1):
            href1 = links[i].get("href", "")
            href2 = links[i + 1].get("href", "")
            if href1 and href1 == href2:
                # Check they are adjacent (no significant content between them)
                if self._are_adjacent(links[i], links[i + 1]):
                    issues.append(
                        self.create_issue(
                            page_id=page_id,
                            message=f"Adjacent links share the same href: {href1[:60]}",
                            element_html=str(links[i])[:200],
                        )
                    )
        return issues

    @staticmethod
    def _are_adjacent(el1, el2) -> bool:
        sibling = el1.next_sibling
        while sibling is not None:
            if isinstance(sibling, str) and not sibling.strip():
                sibling = sibling.next_sibling
                continue
            return sibling == el2
        return False


class BrokenLinkSpaceRule(AccessibilityRule):
    """LNK008: Detect links with unencoded spaces in href."""

    rule_id = "LNK008"
    severity = IssueSeverity.WARNING
    category = IssueCategory.LINKS
    wcag_criterion = "2.4.4"
    message_template = "Link URL contains unencoded spaces"
    can_auto_fix = True
    fix_description = "URL-encode spaces as %20"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        for link in soup.find_all("a"):
            href = link.get("href", "")
            if not href or href.startswith("#") or href.startswith("mailto:"):
                continue
            # Check for unencoded spaces (not already %20)
            if " " in href:
                text = link.get_text(strip=True)[:40]
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f'Link URL contains spaces: "{text}" ({href[:60]})',
                        element_html=str(link)[:200],
                    )
                )
        return issues


class BrokenSamePageLinkRule(AccessibilityRule):
    """LNK009: Detect broken same-page links (href="#" or href="#nonexistent-id")."""

    rule_id = "LNK009"
    severity = IssueSeverity.ERROR
    category = IssueCategory.LINKS
    wcag_criterion = "2.1.1"
    message_template = "Broken same-page link"
    can_auto_fix = True
    fix_description = "Unwrap broken anchor links, keeping inner text"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        # Collect all IDs on the page
        all_ids = {tag["id"] for tag in soup.find_all(id=True)}

        for link in soup.find_all("a"):
            href = link.get("href", "")
            if not href or not href.startswith("#"):
                continue

            fragment = href[1:]  # Strip leading #

            # Empty fragment: href="#"
            if not fragment:
                text = link.get_text(strip=True)[:40]
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f'Link "{text}" has empty fragment href="#"',
                        element_html=str(link)[:200],
                    )
                )
                continue

            # Fragment points to nonexistent ID
            if fragment not in all_ids:
                text = link.get_text(strip=True)[:40]
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f'Link "{text}" references nonexistent id="#{fragment}"',
                        element_html=str(link)[:200],
                    )
                )

        return issues


class DuplicateLinkTextRule(AccessibilityRule):
    """LNK010: Detect multiple links with identical text pointing to different destinations."""

    rule_id = "LNK010"
    severity = IssueSeverity.WARNING
    category = IssueCategory.LINKS
    wcag_criterion = "2.4.4"
    message_template = "Multiple links with same text point to different destinations"
    can_auto_fix = True
    fix_description = "Disambiguate duplicate link text with contextual suffix"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        # Build map: text -> set of unique hrefs
        text_to_hrefs: dict[str, set[str]] = {}
        for link in soup.find_all("a"):
            text = link.get_text(strip=True)
            href = link.get("href", "")
            if not text or not href:
                continue
            key = text.lower()
            if key not in text_to_hrefs:
                text_to_hrefs[key] = set()
            text_to_hrefs[key].add(href)

        # Flag texts that map to multiple different hrefs
        flagged_texts = set()
        for text_lower, hrefs in text_to_hrefs.items():
            if len(hrefs) > 1:
                flagged_texts.add(text_lower)

        if not flagged_texts:
            return issues

        for link in soup.find_all("a"):
            text = link.get_text(strip=True)
            if text.lower() in flagged_texts:
                href = link.get("href", "")[:60]
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f'Multiple links with text "{text[:40]}" point to different destinations ({href})',
                        element_html=str(link)[:200],
                    )
                )

        return issues


class RedundantEmptyLinkRule(AccessibilityRule):
    """LNK011: Empty <a> redundant with a content-bearing <a> to same URL.

    Catches a common Canvas RCE / IMSCC import pattern where the same
    href appears twice on a page: once with visible text or a child
    image, and once as an empty ``<a aria-label="…">`` (often a Flickr
    photo ID or filename). PopeTech flags the pair as a "Redundant link"
    alert. The empty link is the obvious one to remove because it
    contributes nothing visually and the visible link already provides
    full access to the destination.

    Note: this rule deliberately does NOT fire for lone empty links
    (those would lose access entirely if removed — that's LNK003's
    domain) or for two visible links to the same href (that's LNK010).
    """

    rule_id = "LNK011"
    severity = IssueSeverity.WARNING
    category = IssueCategory.LINKS
    wcag_criterion = "2.4.4"
    message_template = "Empty link is redundant with another link to the same destination"
    can_auto_fix = True
    fix_description = "Remove the empty link; the visible-content link already covers the destination"

    @staticmethod
    def _link_has_visible_content(link) -> bool:
        """Return True if the link's content is visible to sighted users.

        Visible content means inner text OR a child img with alt text.
        aria-label and title alone do NOT count as visible (they're
        accessible names, not visible content).
        """
        text = link.get_text(strip=True)
        if text:
            return True
        for img in link.find_all("img"):
            if (img.get("alt") or "").strip():
                return True
        return False

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []

        # First pass: bucket links by href, recording which hrefs have
        # at least one visible-content link.
        hrefs_with_visible: set[str] = set()
        empty_links_by_href: dict[str, list] = {}

        for link in soup.find_all("a"):
            href = (link.get("href") or "").strip()
            if not href:
                continue
            # Skip same-page anchor links — they're navigation aids and
            # often legitimately appear multiple times.
            if href.startswith("#"):
                continue

            if self._link_has_visible_content(link):
                hrefs_with_visible.add(href)
            else:
                empty_links_by_href.setdefault(href, []).append(link)

        # Second pass: branch on whether the href has a visible companion.
        for href, empties in empty_links_by_href.items():
            if href in hrefs_with_visible:
                # Branch A: visible companion exists — drop ALL empty
                # copies (the visible link already provides full access).
                for empty in empties:
                    issues.append(
                        self.create_issue(
                            page_id=page_id,
                            message=f"Empty link redundant with visible link to {href[:60]}",
                            element_html=str(empty)[:200],
                        )
                    )
            elif len(empties) >= 2:
                # Branch B: mutual-empty case — multiple empty links to
                # the same href with no visible companion (Art103
                # "0810958724" Amazon link, "venus of willendorf"
                # external links). Keep the first, flag the rest.
                for empty in empties[1:]:
                    issues.append(
                        self.create_issue(
                            page_id=page_id,
                            message=(
                                f"Duplicate empty link to {href[:60]}; "
                                "keeping first occurrence"
                            ),
                            element_html=str(empty)[:200],
                        )
                    )

        return issues


class ImageTextLinkMergeRule(AccessibilityRule):
    """LNK012: Adjacent image-link + text-link to the same href.

    Canvas RCE generates this pattern when an instructor inserts a
    file via the Files sidebar: an icon-image link to ``file?wrap=1``
    immediately followed by a text link with the filename, both
    pointing at the same href. PopeTech flags this as a "Redundant
    link" alert.

    The visible text from the text link becomes the alt text on the
    image link, and the text link is removed.
    """

    rule_id = "LNK012"
    severity = IssueSeverity.WARNING
    category = IssueCategory.LINKS
    wcag_criterion = "2.4.4"
    message_template = (
        "Adjacent image-link and text-link to same href can be merged"
    )
    can_auto_fix = True
    fix_description = (
        "Merge adjacent image-only and text-only links to the same "
        "destination into a single image link with the text as alt"
    )

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        for pair in _find_image_text_link_pairs(soup):
            img_link, _ = pair
            issues.append(
                self.create_issue(
                    page_id=page_id,
                    message=self.message_template,
                    element_html=str(img_link)[:200],
                )
            )
        return issues


def _find_image_text_link_pairs(soup):
    """Yield (image_link, text_link) tuples for adjacent <a> pairs to
    the same href where one wraps an image and the other has text only.

    Adjacency is checked through one level of intermediate whitespace
    text nodes (the typical Canvas RCE pattern is two <a> tags directly
    adjacent inside a <p> with no separator).
    """
    pairs = []
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        # Check if `a` is image-only
        text = a.get_text(strip=True)
        has_img = bool(a.find("img"))
        if text or not has_img:
            continue
        # Find the immediate next non-whitespace sibling
        sib = a.next_sibling
        while sib is not None and isinstance(sib, str) and not sib.strip():
            sib = sib.next_sibling
        if sib is None or not hasattr(sib, "name") or sib.name != "a":
            continue
        sib_href = (sib.get("href") or "").strip()
        if sib_href != href:
            continue
        sib_text = sib.get_text(strip=True)
        sib_has_img = bool(sib.find("img"))
        if not sib_text or sib_has_img:
            continue
        pairs.append((a, sib))
    return pairs


class DocumentLinkRule(AccessibilityRule):
    """LNK007: Flag links pointing to document files or cloud document URLs."""

    rule_id = "LNK007"
    severity = IssueSeverity.INFO
    category = IssueCategory.LINKS
    wcag_criterion = "2.4.4"
    message_template = "Link points to a document file"
    can_auto_fix = False
    fix_description = "Consider converting the document to an accessible HTML page"

    # All PopeTech document types
    DOCUMENT_EXTENSIONS = {
        # Microsoft Office
        ".doc", ".docx", ".ppt", ".pptx", ".pps", ".ppsx",
        ".xls", ".xlsx",
        # Open Document
        ".odt", ".ods", ".odp",
        # Other
        ".rtf", ".wpd", ".pdf",
        # Legacy/additional
        ".sxw", ".sxc", ".sxd", ".sxi",
        ".pages", ".key", ".numbers",
    }

    # Build regex from extension set (matches extension at end, optionally followed by query string)
    _DOC_EXTENSIONS_RE = re.compile(
        r"(" + "|".join(re.escape(ext) for ext in DOCUMENT_EXTENSIONS) + r")(\?.*)?$",
        re.IGNORECASE,
    )

    # Google cloud document URL patterns
    GOOGLE_LINK_PATTERNS = {
        "docs.google.com/document": "Google Document",
        "docs.google.com/spreadsheets": "Google Sheet",
        "docs.google.com/presentation": "Google Presentation",
        "docs.google.com/forms": "Google Form",
        "drive.google.com/file": "Google File",
        "docs.google.com/file": "Google File",
    }

    # Extension to human-readable label
    _EXTENSION_LABELS = {
        ".doc": "Word document", ".docx": "Word document",
        ".ppt": "PowerPoint presentation", ".pptx": "PowerPoint presentation",
        ".pps": "PowerPoint slideshow", ".ppsx": "PowerPoint slideshow",
        ".xls": "Excel spreadsheet", ".xlsx": "Excel spreadsheet",
        ".odt": "OpenDocument text", ".ods": "OpenDocument spreadsheet",
        ".odp": "OpenDocument presentation",
        ".rtf": "RTF document", ".wpd": "WordPerfect document",
        ".pdf": "PDF document",
        ".sxw": "StarOffice document", ".sxc": "StarOffice spreadsheet",
        ".sxd": "StarOffice drawing", ".sxi": "StarOffice presentation",
        ".pages": "Apple Pages document", ".key": "Apple Keynote presentation",
        ".numbers": "Apple Numbers spreadsheet",
    }

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        for link in soup.find_all("a"):
            href = link.get("href", "")
            if not href:
                continue
            text = link.get_text(strip=True)[:40]

            # Check file extension
            ext_match = self._DOC_EXTENSIONS_RE.search(href)
            if ext_match:
                ext = ext_match.group(1).lower()
                label = self._EXTENSION_LABELS.get(ext, "document file")
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f'Link to {label}: "{text}" ({href[:60]})',
                        element_html=str(link)[:200],
                    )
                )
                continue

            # Check Google document URL patterns
            href_lower = href.lower()
            for pattern, label in self.GOOGLE_LINK_PATTERNS.items():
                if pattern in href_lower:
                    issues.append(
                        self.create_issue(
                            page_id=page_id,
                            message=f'Link to {label}: "{text}" ({href[:60]})',
                            element_html=str(link)[:200],
                        )
                    )
                    break

        return issues
