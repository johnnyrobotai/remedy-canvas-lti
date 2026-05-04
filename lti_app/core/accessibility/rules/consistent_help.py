"""Accessibility rules for consistent help mechanisms (WCAG 3.2.6).

HELP001 is a **course-level** rule: it flags placement inconsistencies
across a course's pages rather than emitting a per-page notice for
every "help/support/contact" link.

Pre-change behavior: every <a> whose text contained "help", "support",
"contact", "faq", or "assistance" was flagged as an INFO issue on every
page it appeared. This conflated in-content help references (e.g.,
"If you experience trouble, contact support@cvc.edu") with the WCAG
3.2.6 placement requirement, producing large amounts of noise without
actionable signal.

Post-change behavior: the rule looks for a help-link *convention* —
the same help href appearing in the "footer slot" of 2+ pages — and
only flags pages that break the convention by missing the shared link.
Courses with no convention (or single outliers) stay silent.

"Footer slot" heuristic, in order of preference:
  1. Inside an explicit <footer> element
  2. Inside any element with role="contentinfo"
  3. The last top-level child of <body> (catches courses that put a
     trailing "Need help? Contact X" paragraph on every page)
"""

from typing import Iterable

from bs4 import BeautifulSoup, Tag

from lti_app.models import AccessibilityIssue, CoursePage, IssueCategory, IssueSeverity
from lti_app.core.accessibility.rules.base import AccessibilityRule


_HELP_KEYWORDS = {
    "help", "support", "contact", "faq", "assistance", "get help", "contact us",
}

# Minimum pages that must share the same help href for it to count as a
# course convention. Two-page threshold: the lowest value that still
# rules out a single page's in-content help reference. Bump if larger
# courses produce false positives in practice.
_MIN_PAGES_FOR_CONVENTION = 2


def _link_has_help_signal(link: Tag) -> bool:
    """True if the <a> text or href contains a help keyword."""
    text = link.get_text(strip=True).lower()
    if any(kw in text for kw in _HELP_KEYWORDS):
        return True
    href = (link.get("href") or "").lower()
    # Help URLs often contain literal keywords as path segments
    for kw in ("help", "support", "contact", "faq"):
        if f"/{kw}" in href or href.startswith(f"{kw}:") or href.startswith(f"mailto:{kw}"):
            return True
    return False


def _first_help_href_in(region: Tag) -> str | None:
    for link in region.find_all("a", href=True):
        if _link_has_help_signal(link):
            return link["href"]
    return None


def _footer_slot_help_href(soup: BeautifulSoup) -> str | None:
    """Return the first help href found in the page's footer slot.

    Tries explicit <footer>, then role="contentinfo", then the last
    top-level child of <body>. Returns None when no footer-positioned
    help link is present.
    """
    body = soup.find("body") or soup

    for footer in body.find_all("footer"):
        href = _first_help_href_in(footer)
        if href:
            return href

    for region in body.find_all(attrs={"role": "contentinfo"}):
        href = _first_help_href_in(region)
        if href:
            return href

    top_children = [c for c in body.children if isinstance(c, Tag)]
    if not top_children:
        return None
    return _first_help_href_in(top_children[-1])


class ConsistentHelpRule(AccessibilityRule):
    """HELP001: Course-level consistency check for help mechanisms.

    Per-page `check()` is a no-op so the analyzer never double-emits
    when the rule is mistakenly included in the per-page list. The
    real work happens in `check_course()`, which the analyzer calls
    once at the end of the per-page pass.
    """

    rule_id = "HELP001"
    severity = IssueSeverity.INFO
    category = IssueCategory.STRUCTURE
    wcag_criterion = "3.2.6"
    message_template = "Page is missing the course-wide help link found in the footer of other pages"
    can_auto_fix = False
    # Hint for the analyzer to route this rule through check_course
    # rather than the per-page check() loop.
    course_level = True

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        return []

    def check_course(
        self, pages: Iterable[CoursePage]
    ) -> list[AccessibilityIssue]:
        pages_list = list(pages)
        if not pages_list:
            return []

        # Gather (page, footer_help_href) for every page
        page_hrefs: list[tuple[CoursePage, str | None]] = []
        for page in pages_list:
            html = page.html_content or ""
            if not html.strip():
                page_hrefs.append((page, None))
                continue
            try:
                soup = BeautifulSoup(html, "html.parser")
            except Exception:
                page_hrefs.append((page, None))
                continue
            page_hrefs.append((page, _footer_slot_help_href(soup)))

        # A convention = one href shared by >= _MIN_PAGES_FOR_CONVENTION pages
        counts: dict[str, int] = {}
        for _, href in page_hrefs:
            if href:
                counts[href] = counts.get(href, 0) + 1
        conventions = {
            href for href, n in counts.items()
            if n >= _MIN_PAGES_FOR_CONVENTION
        }
        if not conventions:
            return []

        # Pick the most-used convention to name in the message (when
        # there are multiple, the instructor likely wants the canonical
        # one — the single href appearing on the most pages).
        canonical = max(conventions, key=lambda h: counts[h])

        issues: list[AccessibilityIssue] = []
        for page, href in page_hrefs:
            if href in conventions:
                continue
            issues.append(
                self.create_issue(
                    page_id=page.id,
                    message=(
                        f"This page is missing the course-wide help link "
                        f"({canonical}) that appears in the footer of "
                        f"{counts[canonical]} other pages."
                    ),
                    page_identifier=page.identifier,
                )
            )
        return issues
