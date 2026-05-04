"""Accessibility rules for media elements (WCAG 1.2.1, 1.2.2, 1.4.2)."""

import re

from bs4 import BeautifulSoup, Tag

from lti_app.models import AccessibilityIssue, IssueCategory, IssueSeverity
from lti_app.core.accessibility.rules.base import AccessibilityRule


class MediaWithoutCaptionsRule(AccessibilityRule):
    """MDA001: Detect video/audio/iframe without captions or text alternative."""

    rule_id = "MDA001"
    severity = IssueSeverity.ERROR
    category = IssueCategory.MEDIA
    wcag_criterion = "1.2.2"
    message_template = "Media element missing captions or text alternative"
    can_auto_fix = True
    fix_description = "Add title attribute to video iframe with video title or platform name"

    _VIDEO_HOSTS = re.compile(
        r"youtube|youtu\.be|vimeo|kaltura|panopto|wistia"
        r"|instructuremedia|arc\.instructure|canvastudio"
        r"|echo360|mediasite|media\.instructure",
        re.IGNORECASE,
    )

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []

        # Check <video> and <audio> elements
        for tag in soup.find_all(["video", "audio"]):
            if not self._has_text_alternative(tag):
                src = tag.get("src", "")
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f"<{tag.name}> element has no captions or text alternative",
                        element_html=str(tag)[:200],
                    )
                )

        # Check <iframe> elements for video hosting
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src", "")
            if self._VIDEO_HOSTS.search(src):
                if not iframe.get("title") and not iframe.get("aria-label"):
                    issues.append(
                        self.create_issue(
                            page_id=page_id,
                            message=f"Video iframe missing title or aria-label: {src[:80]}",
                            element_html=str(iframe)[:200],
                        )
                    )

        return issues

    @staticmethod
    def _has_text_alternative(tag: Tag) -> bool:
        """Check if a media element has a text alternative."""
        if tag.find("track", kind="captions") or tag.find("track", kind="subtitles"):
            return True
        if tag.get("aria-label") or tag.get("aria-describedby"):
            return True
        # Check for nearby text alternative
        next_sib = tag.find_next_sibling()
        if next_sib and next_sib.name == "p" and "transcript" in next_sib.get_text(strip=True).lower():
            return True
        return False


class AutoplayMediaRule(AccessibilityRule):
    """MDA002: Detect autoplay attribute on media elements."""

    rule_id = "MDA002"
    severity = IssueSeverity.ERROR
    category = IssueCategory.MEDIA
    wcag_criterion = "1.4.2"
    message_template = "Media element has autoplay enabled"
    can_auto_fix = True
    fix_description = "Remove autoplay attribute"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        for tag in soup.find_all(["video", "audio", "embed"]):
            if tag.has_attr("autoplay"):
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f"<{tag.name}> has autoplay enabled",
                        element_html=str(tag)[:200],
                    )
                )
        return issues


# ---------------------------------------------------------------------------
# YouTube URL pattern shared by MDA003 and MDA004
# ---------------------------------------------------------------------------
_YOUTUBE_RE = re.compile(
    r"youtube\.com/(?:watch|embed)|youtu\.be/", re.IGNORECASE
)

# Canvas Studio URL pattern shared by MDA005 and MDA006
_STUDIO_RE = re.compile(
    r"instructuremedia\.com|arc\.instructure\.com", re.IGNORECASE
)


class YouTubeAutoCaptionsRule(AccessibilityRule):
    """MDA003: Flag YouTube videos for caption review (WCAG 1.2.2).

    PopeTech equivalent: "Only caption track for a YouTube video is automated."
    Without YouTube API access we cannot verify caption status, so all YouTube
    embeds are flagged for manual review.
    """

    rule_id = "MDA003"
    severity = IssueSeverity.WARNING
    category = IssueCategory.MEDIA
    wcag_criterion = "1.2.2"
    message_template = "YouTube video — verify captions are human-generated, not auto-generated"
    can_auto_fix = False

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        seen_urls: set[str] = set()

        # <iframe> embeds
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src", "")
            if _YOUTUBE_RE.search(src) and src not in seen_urls:
                seen_urls.add(src)
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f"YouTube video — verify captions are human-generated, not auto-generated: {src[:80]}",
                        element_html=str(iframe)[:200],
                    )
                )

        # <a> links to YouTube
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if _YOUTUBE_RE.search(href) and href not in seen_urls:
                seen_urls.add(href)
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f"YouTube video link — verify captions are human-generated, not auto-generated: {href[:80]}",
                        element_html=str(link)[:200],
                    )
                )

        return issues


class YouTubeNotFoundRule(AccessibilityRule):
    """MDA004: Flag YouTube embeds that may be dead/unavailable (WCAG 1.2.2).

    PopeTech equivalent: "A YouTube video was not found."
    Without making HTTP requests we cannot confirm availability, so all YouTube
    embeds are flagged for verification.
    """

    rule_id = "MDA004"
    severity = IssueSeverity.WARNING
    category = IssueCategory.MEDIA
    wcag_criterion = "1.2.2"
    message_template = "YouTube video detected — verify video is still available"
    can_auto_fix = False

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        seen_urls: set[str] = set()

        for iframe in soup.find_all("iframe"):
            src = iframe.get("src", "")
            if _YOUTUBE_RE.search(src) and src not in seen_urls:
                seen_urls.add(src)
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f"YouTube video detected — verify video is still available: {src[:80]}",
                        element_html=str(iframe)[:200],
                    )
                )

        for link in soup.find_all("a", href=True):
            href = link["href"]
            if _YOUTUBE_RE.search(href) and href not in seen_urls:
                seen_urls.add(href)
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f"YouTube video link detected — verify video is still available: {href[:80]}",
                        element_html=str(link)[:200],
                    )
                )

        return issues


class CanvasStudioAutoCaptionsRule(AccessibilityRule):
    """MDA005: Flag Canvas Studio videos for caption review (WCAG 1.2.2).

    PopeTech equivalent: "Only auto-generated captions for Canvas Studio video."
    """

    rule_id = "MDA005"
    severity = IssueSeverity.WARNING
    category = IssueCategory.MEDIA
    wcag_criterion = "1.2.2"
    message_template = "Canvas Studio video — verify captions are human-generated"
    can_auto_fix = False

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        seen_urls: set[str] = set()

        for iframe in soup.find_all("iframe"):
            src = iframe.get("src", "")
            if _STUDIO_RE.search(src) and src not in seen_urls:
                seen_urls.add(src)
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f"Canvas Studio video — verify captions are human-generated: {src[:80]}",
                        element_html=str(iframe)[:200],
                    )
                )

        for link in soup.find_all("a", href=True):
            href = link["href"]
            if _STUDIO_RE.search(href) and href not in seen_urls:
                seen_urls.add(href)
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f"Canvas Studio video link — verify captions are human-generated: {href[:80]}",
                        element_html=str(link)[:200],
                    )
                )

        return issues


class CanvasStudioNotFoundRule(AccessibilityRule):
    """MDA006: Flag Canvas Studio embeds that may be dead/unavailable (WCAG 1.2.2)."""

    rule_id = "MDA006"
    severity = IssueSeverity.WARNING
    category = IssueCategory.MEDIA
    wcag_criterion = "1.2.2"
    message_template = "Canvas Studio video detected — verify video is still available"
    can_auto_fix = False

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        seen_urls: set[str] = set()

        for iframe in soup.find_all("iframe"):
            src = iframe.get("src", "")
            if _STUDIO_RE.search(src) and src not in seen_urls:
                seen_urls.add(src)
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f"Canvas Studio video detected — verify video is still available: {src[:80]}",
                        element_html=str(iframe)[:200],
                    )
                )

        for link in soup.find_all("a", href=True):
            href = link["href"]
            if _STUDIO_RE.search(href) and href not in seen_urls:
                seen_urls.add(href)
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f"Canvas Studio video link detected — verify video is still available: {href[:80]}",
                        element_html=str(link)[:200],
                    )
                )

        return issues
