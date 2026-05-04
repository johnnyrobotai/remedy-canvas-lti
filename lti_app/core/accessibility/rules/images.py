"""Accessibility rules for images (WCAG 1.1.1 Non-text Content)."""

from bs4 import BeautifulSoup

from lti_app.core.accessibility.image_alt import (
    assess_image_tag,
    describe_inadequate_alt,
    get_inadequate_alt_reason,
)
from lti_app.models import AccessibilityIssue, IssueCategory, IssueSeverity
from lti_app.core.accessibility.rules.base import AccessibilityRule


class MissingAltTextRule(AccessibilityRule):
    """IMG001: Check for images missing alt text."""

    rule_id = "IMG001"
    severity = IssueSeverity.ERROR
    category = IssueCategory.IMAGES
    wcag_criterion = "1.1.1"
    message_template = "Image missing alt text"
    can_auto_fix = True
    fix_description = "Generate alt text using AI vision model"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find images without alt attributes."""
        issues = []

        for img in soup.find_all("img"):
            assessment = assess_image_tag(img)
            if assessment.skip_reason:
                continue

            # Check if alt attribute exists
            if not img.has_attr("alt"):
                src = img.get("src", "unknown")
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f"Image missing alt text: {src}",
                        element_html=str(img),
                    )
                )
            elif img.get("alt", "").strip() == "":
                # Empty alt attribute (not the same as decorative)
                # Check if it's explicitly marked as decorative
                role = img.get("role", "")
                if role != "presentation":
                    src = img.get("src", "unknown")
                    issues.append(
                        self.create_issue(
                            page_id=page_id,
                            message=f"Image has empty alt text: {src}",
                            element_html=str(img),
                        )
                    )

        # Image maps: <img usemap="..."> without alt
        for img in soup.find_all("img", usemap=True):
            if not img.has_attr("alt"):
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message="Image map missing alternative text",
                        element_html=str(img)[:200],
                    )
                )

        # Image map areas: <area> without alt
        for area in soup.find_all("area"):
            if not area.get("alt", "").strip():
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message="Image map area missing alternative text",
                        element_html=str(area)[:200],
                    )
                )

        return issues


class InadequateAltTextRule(AccessibilityRule):
    """IMG002: Check for images with inadequate alt text."""

    rule_id = "IMG002"
    severity = IssueSeverity.WARNING
    category = IssueCategory.IMAGES
    wcag_criterion = "1.1.1"
    message_template = "Image has inadequate alt text"
    can_auto_fix = True
    fix_description = "Generate descriptive alt text using AI"

    # Common inadequate alt text patterns
    INADEQUATE_ALTS = {
        "image",
        "photo",
        "picture",
        "pic",
        "img",
        "graphic",
        "icon",
        "logo",
        "banner",
        "screenshot",
        "screen shot",
        "untitled",
        "null",
        "undefined",
        "placeholder",
        "test",
        "temp",
        "temporary",
    }

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find images with inadequate alt text."""
        issues = []

        for img in soup.find_all("img"):
            assessment = assess_image_tag(img)
            if assessment.skip_reason:
                continue

            alt = img.get("alt", "")

            if not alt:
                continue  # Handled by IMG001

            reason = get_inadequate_alt_reason(alt)
            if reason:
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=describe_inadequate_alt(reason, alt),
                        element_html=str(img),
                    )
                )

        return issues


class DuplicateAltTextRule(AccessibilityRule):
    """IMG003: Check for nearby images with the same alt text."""

    rule_id = "IMG003"
    severity = IssueSeverity.WARNING
    category = IssueCategory.IMAGES
    wcag_criterion = "1.1.1"
    message_template = "Nearby images have identical alt text"
    can_auto_fix = True
    fix_description = "Differentiate alt text for nearby images"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find nearby images with the same alt text (within 2 other images).

        PopeTech algorithm: The same alternative text (case insensitive, not
        null/empty) is present for two images or image buttons
        (<input type='image'>), no more than 2 other images separate them.
        """
        issues = []

        # Collect all images and image buttons in document order
        all_imgs = []
        for el in soup.find_all(["img", "input"]):
            if el.name == "img":
                all_imgs.append(el)
            elif el.name == "input" and el.get("type") == "image":
                all_imgs.append(el)

        # Check each pair within distance of 2
        for idx, img in enumerate(all_imgs):
            alt = (img.get("alt") or "").strip().lower()
            if not alt:
                continue
            for j in range(idx + 1, min(idx + 3, len(all_imgs))):
                other_alt = (all_imgs[j].get("alt") or "").strip().lower()
                if other_alt == alt:
                    issues.append(
                        self.create_issue(
                            page_id=page_id,
                            message=f"A nearby image has the same alternative text: '{alt[:50]}'",
                            element_html=str(all_imgs[j])[:200],
                        )
                    )
                    break  # Only flag once per pair

        return issues


class LongAltTextRule(AccessibilityRule):
    """IMG004: Check for images with alt text exceeding 150 characters."""

    rule_id = "IMG004"
    severity = IssueSeverity.WARNING
    category = IssueCategory.IMAGES
    wcag_criterion = "1.1.1"
    message_template = "Image alt text is too long"
    can_auto_fix = True
    fix_description = "Truncate alt text to a concise description"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        from lti_app.core.accessibility.image_alt import MAX_ALT_TEXT_CHARS

        issues = []
        for img in soup.find_all("img"):
            alt = img.get("alt", "")
            if len(alt) > MAX_ALT_TEXT_CHARS:
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f"Alt text is {len(alt)} characters (max {MAX_ALT_TEXT_CHARS}): \"{alt[:40]}...\"",
                        element_html=str(img)[:200],
                    )
                )
        return issues


class LinkedImageMissingAltRule(AccessibilityRule):
    """IMG005: Check for linked images with no alt text."""

    rule_id = "IMG005"
    severity = IssueSeverity.ERROR
    category = IssueCategory.IMAGES
    wcag_criterion = "1.1.1"
    message_template = "Linked image has no alt text"
    can_auto_fix = True
    fix_description = "Add alt text from link context"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        for link in soup.find_all("a"):
            imgs = link.find_all("img")
            if not imgs:
                continue
            link_text = link.get_text(strip=True)
            for img in imgs:
                alt = img.get("alt", "").strip()
                if not alt and not link_text:
                    href = link.get("href", "")[:60]
                    issues.append(
                        self.create_issue(
                            page_id=page_id,
                            message=f"Linked image has no alt text and link has no text: {href}",
                            element_html=str(link)[:200],
                        )
                    )
        return issues


class ImageWithTitleRule(AccessibilityRule):
    """IMG006: Image has a title attribute but no alt text."""

    rule_id = "IMG006"
    severity = IssueSeverity.WARNING
    category = IssueCategory.IMAGES
    wcag_criterion = "1.1.1"
    message_template = "Image has title but no alt text — title is not a substitute for alt"
    can_auto_fix = True
    fix_description = "Copy title to alt or generate alt text using AI"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find images with title attribute but missing or empty alt."""
        issues = []
        for img in soup.find_all("img"):
            title = (img.get("title") or "").strip()
            if not title:
                continue
            alt = (img.get("alt") or "").strip()
            if not alt:
                src = img.get("src", "unknown")[:80]
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f"Image has title but no alt text — title is not a substitute for alt: {src}",
                        element_html=str(img),
                    )
                )
        return issues


class InvalidLongdescRule(AccessibilityRule):
    """IMG008: The longdesc attribute is not a valid URL."""

    rule_id = "IMG008"
    severity = IssueSeverity.ERROR
    category = IssueCategory.IMAGES
    wcag_criterion = "1.1.1"
    message_template = "Invalid longdesc — value is not a valid URL"
    can_auto_fix = False

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find images with longdesc that is not a valid URL."""
        issues = []
        for img in soup.find_all("img", attrs={"longdesc": True}):
            longdesc = (img.get("longdesc") or "").strip()
            if not longdesc:
                continue
            # Valid longdesc starts with http(s), /, or #
            if not (longdesc.startswith("http") or longdesc.startswith("/") or longdesc.startswith("#")):
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f"Invalid longdesc — '{longdesc[:60]}' is not a valid URL",
                        element_html=str(img),
                    )
                )
        return issues


# Words that PopeTech treats as redundant noise when they appear at the
# start or end of alt text. Screen readers already announce that an
# element is an image, so "image of X" or "X. Image" is informationally
# noisy. Bare-word cases ("alt='Image'") are caught by IMG002.
_SUSPICIOUS_ALT_WORDS = {
    "image", "images",
    "picture", "pictures",
    "photo", "photos",
    "photograph", "photographs",
    "graphic", "graphics",
    "icon", "icons",
    "img",
    "pic",
}


# Navigation noise phrases — these belong to the parent <a target="_blank">
# link's accessibility hint, NOT to the image. Real Art103 case:
#
#   <a target="_blank"><img alt="(opens in new window)" src="..."></a>
#
# A screen reader user will already hear "opens in new window" announced
# for the link itself; repeating it as the image's alt text adds nothing
# and clutters the announcement. We strip the phrase entirely (or empty
# the alt if it's the whole thing).
#
# Pattern matches:
#   "opens in new window" / "opens in a new window"
#   "opens in new tab" / "opens in a new tab"
#   "link opens in new window" / "(opens in new window)" / etc.
#   Optional surrounding parens, optional leading "link " prefix.
import re as _nav_re
_NAVIGATION_NOISE_RE = _nav_re.compile(
    r"""
    \(?                                  # optional opening paren
    (?:link\s+)?                         # optional "link " prefix
    opens?\s+in\s+(?:a\s+)?new\s+        # "open(s) in (a) new "
    (?:window|tab|page)                  # window | tab | page
    \)?                                  # optional closing paren
    """,
    _nav_re.IGNORECASE | _nav_re.VERBOSE,
)


def _suspicious_alt_match(alt: str) -> tuple[str, str] | None:
    """Inspect alt text for a suspicious leading or trailing noise word.

    Returns ``(position, cleaned_text)`` if a match was found, where
    position is one of {"leading", "trailing", "navigation"} and
    cleaned_text is the alt text with the noise removed and any
    redundant punctuation cleaned up. Returns ``None`` if the alt
    text is fine.

    Patterns matched:
      - "Image of X" / "Picture of X" / etc. → strip the leading
        "Image of " (capitalize first letter of remainder)
      - "X. Image" / "X Image" / etc. → strip the trailing word and
        any preceding punctuation
      - "(opens in new window)" / "opens in new tab" / etc. (CLU-76)
        → strip the navigation hint phrase. If the whole alt is the
        phrase, return ("navigation", "") to mark the image
        decorative.
    """
    import re

    alt = alt.strip()
    if not alt:
        return None

    # CLU-76: navigation noise check first — works on any-length alt,
    # including 1-word forms like "(opens" because the phrase always
    # spans multiple tokens after parens are stripped.
    nav_match = _NAVIGATION_NOISE_RE.search(alt)
    if nav_match:
        # Strip the phrase and any surrounding whitespace/punctuation
        before = alt[: nav_match.start()].rstrip(" .,;:-!?()")
        after = alt[nav_match.end():].lstrip(" .,;:-!?()")
        cleaned = (before + " " + after).strip()
        return ("navigation", cleaned)

    # Bare word or 1-word alt — IMG002 handles those
    if len(alt.split()) <= 1:
        return None

    lower = alt.lower()

    # Leading "Image of " / "A picture of " / etc.
    leading_re = re.compile(
        r"^(?:an?\s+)?(?:" + "|".join(_SUSPICIOUS_ALT_WORDS) + r")\s+of\s+",
        re.IGNORECASE,
    )
    m = leading_re.match(alt)
    if m:
        rest = alt[m.end():].strip()
        if rest:
            cleaned = rest[0].upper() + rest[1:]
            return ("leading", cleaned)

    # Trailing word: ". Image", " Image", "Image", etc. as the last
    # whitespace-token. Match optional punctuation+whitespace then the
    # word at end-of-string.
    trailing_re = re.compile(
        r"(?:[.,;:\-!?\s]+)?\b(?:" + "|".join(_SUSPICIOUS_ALT_WORDS) + r")\.?\s*$",
        re.IGNORECASE,
    )
    m = trailing_re.search(alt)
    if m and m.start() > 0:
        cleaned = alt[: m.start()].rstrip(" ,;:.-!?")
        if cleaned:
            return ("trailing", cleaned)

    return None


class SuspiciousAltTextRule(AccessibilityRule):
    """IMG009: Alt text contains redundant 'image' / 'picture' noise.

    PopeTech "Suspicious alternative text". Flags multi-word alt text
    where the first or last word is a redundant noun like "image",
    "picture", "photo" — these add no information for screen reader
    users (who already know it's an image) and just clutter the
    announcement.

    Bare-word cases ("alt='Image'") are caught by IMG002 instead.
    """

    rule_id = "IMG009"
    severity = IssueSeverity.WARNING
    category = IssueCategory.IMAGES
    wcag_criterion = "1.1.1"
    message_template = "Alt text contains redundant 'image'/'picture' noise"
    can_auto_fix = True
    fix_description = "Strip leading 'Image of …' or trailing '… Image' noise"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        for img in soup.find_all("img"):
            alt = img.get("alt", "")
            if not alt:
                continue
            match = _suspicious_alt_match(alt)
            if match is None:
                continue
            position, _ = match
            issues.append(
                self.create_issue(
                    page_id=page_id,
                    message=f"Alt text has redundant {position} noise: \"{alt[:60]}\"",
                    element_html=str(img)[:200],
                )
            )
        return issues
