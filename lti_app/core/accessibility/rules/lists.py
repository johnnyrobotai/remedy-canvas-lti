"""Accessibility rules for list structure (WCAG 1.3.1 Info and Relationships)."""

import re

from bs4 import BeautifulSoup, Tag

from lti_app.models import AccessibilityIssue, IssueCategory, IssueSeverity
from lti_app.core.accessibility.rules.base import AccessibilityRule

# Patterns for detecting list markers at the start of paragraph text.
#
# CLU-75: `UNORDERED_RE` is relaxed from `^[-*•]\s` to `^[-*•]+\s?`
# so it matches `-`, `--`, `-----` (multi-dash quote markers from
# Art103 Impressionists reading) AND dash-prefixed words without
# trailing space (`-egg tempera` from Art103 brushstrokes video).
# The two-line minimum gating in `PossibleListRule.check` prevents
# false positives on isolated `-X` lines.
ORDERED_NUM_RE = re.compile(r"^(\d+)[.)]\s")
ORDERED_LETTER_RE = re.compile(r"^([a-z])[.)]\s")
ORDERED_UPPER_LETTER_RE = re.compile(r"^([A-Z])[.)]\s")
ORDERED_ROMAN_RE = re.compile(r"^(i{1,3}|iv|vi{0,3})[.)]\s")
UNORDERED_RE = re.compile(r"^[-*\u2022]+\s?")


def _get_marker_type(text: str) -> str | None:
    """Return the marker type for paragraph text, or None if not list-like."""
    if ORDERED_NUM_RE.match(text):
        return "numeric"
    if ORDERED_LETTER_RE.match(text):
        return "letter"
    if ORDERED_UPPER_LETTER_RE.match(text):
        return "upper_letter"
    if ORDERED_ROMAN_RE.match(text):
        return "roman"
    if UNORDERED_RE.match(text):
        return "unordered"
    return None


def _has_complex_children(tag: Tag) -> bool:
    """Check if a tag has complex children that shouldn't be converted to list items."""
    return bool(tag.find(["table", "div", "section", "article", "figure", "ol", "ul"]))


def _split_p_on_br(p: Tag) -> list[str]:
    """Split a <p> element's text content on <br> boundaries.

    CLU-75: returns a list of trimmed text chunks, one per
    `<br>`-separated section. Used to detect list-like patterns
    inside a single `<p>` (Art103 brushstrokes/impressionists cases).

    Walks every descendant text node in document order, accumulating
    text into the current chunk and starting a new chunk each time
    a `<br>` tag is encountered. Empty chunks are preserved (they
    indicate `<br><br>` separators between items) — caller can filter
    if needed.
    """
    chunks: list[str] = []
    current_chunk: list[str] = []

    def flush() -> None:
        text = "".join(current_chunk).strip()
        chunks.append(text)
        current_chunk.clear()

    # `descendants` walks the tree in document order, yielding both
    # text nodes (NavigableString) and child Tags. We accumulate text
    # and split on <br>.
    for node in p.descendants:
        if isinstance(node, Tag):
            if node.name == "br":
                flush()
            # Other tags don't directly contribute text — their text
            # children will be visited as separate NavigableString
            # descendants and accumulate into current_chunk.
        elif isinstance(node, str):
            current_chunk.append(str(node))

    flush()  # Final chunk after the last <br>
    return chunks


class PossibleListRule(AccessibilityRule):
    """LIST001: Check for numbered/lettered paragraphs that should be semantic lists."""

    rule_id = "LIST001"
    severity = IssueSeverity.WARNING
    category = IssueCategory.STRUCTURE
    wcag_criterion = "1.3.1"
    message_template = "Numbered/lettered paragraphs should use semantic list elements"
    can_auto_fix = True
    fix_description = "Convert numbered paragraphs to ordered list elements"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find sequences of consecutive <p> elements that look like lists."""
        issues = []
        body = soup.find("body") or soup

        all_paragraphs = body.find_all("p")
        visited = set()

        for i, p in enumerate(all_paragraphs):
            if id(p) in visited:
                continue

            text = p.get_text(separator=" ", strip=True)
            if not text:
                continue

            marker_type = _get_marker_type(text)
            if not marker_type:
                continue

            if _has_complex_children(p):
                continue

            # Try to build a group of consecutive siblings with the same marker type.
            # CLU-75: don't mark visited until we know the group is large
            # enough to flag — otherwise the second-pass `<br>`-internal
            # scan would skip single-element groups that should be
            # re-checked for internal markers.
            group = [p]
            sibling = p.next_sibling

            while sibling is not None:
                # Skip whitespace text nodes
                if isinstance(sibling, str) and not sibling.strip():
                    sibling = sibling.next_sibling
                    continue

                # Skip <br> tags between list items (common in PPTX conversions)
                if isinstance(sibling, Tag) and sibling.name == "br":
                    sibling = sibling.next_sibling
                    continue

                if not isinstance(sibling, Tag) or sibling.name != "p":
                    break

                sib_text = sibling.get_text(separator=" ", strip=True)
                # Skip empty paragraphs between list items (up to 1 gap)
                if not sib_text:
                    next_after = sibling.next_sibling
                    while next_after and isinstance(next_after, str) and not next_after.strip():
                        next_after = next_after.next_sibling
                    if (next_after and isinstance(next_after, Tag)
                            and next_after.name == "p"
                            and next_after.get_text(separator=" ", strip=True)
                            and _get_marker_type(next_after.get_text(separator=" ", strip=True)) == marker_type):
                        sibling = sibling.next_sibling
                        continue
                    break

                sib_marker = _get_marker_type(sib_text)
                if sib_marker != marker_type:
                    break

                if _has_complex_children(sibling):
                    break

                group.append(sibling)
                sibling = sibling.next_sibling

            # Need at least 2 consecutive matches to flag as a list
            if len(group) >= 2:
                # Only mark visited once we know the group is committed.
                for g in group:
                    visited.add(id(g))
                preview_items = [g.get_text(strip=True)[:40] for g in group[:3]]
                preview = "; ".join(preview_items)
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f"{len(group)} consecutive paragraphs look like a list: \"{preview}...\"",
                        element_html=str(group[0])[:200],
                    )
                )

        # CLU-75 — second pass: scan inside individual <p> elements for
        # <br>-separated lines that share a common list marker. This
        # catches Art103 patterns like:
        #
        #   <p>-egg tempera<br>-oil paints<br>-sfumato</p>
        #   <p>-----"quote 1"<br><br>-----"quote 2"</p>
        #
        # where the markers live inside a single <p> instead of across
        # consecutive <p> siblings.
        for p in body.find_all("p"):
            if id(p) in visited:
                continue  # Already part of a sibling-group flag
            if _has_complex_children(p):
                continue
            # Need at least one <br> child for there to be multiple lines
            if not p.find("br"):
                continue

            chunks = _split_p_on_br(p)
            line_markers = [
                _get_marker_type(chunk) for chunk in chunks if chunk
            ]
            # Filter out None (non-marker chunks) and check if we have
            # 2+ chunks with the SAME marker type
            marker_types = [m for m in line_markers if m is not None]
            if len(marker_types) < 2:
                continue
            # All matching markers must be the same type
            common = marker_types[0]
            if not all(m == common for m in marker_types):
                continue

            preview_items = [c[:40] for c in chunks if c][:3]
            preview = "; ".join(preview_items)
            issues.append(
                self.create_issue(
                    page_id=page_id,
                    message=(
                        f"{len(marker_types)} <br>-separated lines inside a "
                        f"single <p> look like a list: \"{preview}...\""
                    ),
                    element_html=str(p)[:200],
                )
            )

        return issues
