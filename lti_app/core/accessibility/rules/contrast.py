"""Accessibility rules for color contrast (WCAG 1.4.3 Contrast Minimum)."""

import re
from typing import Optional, Tuple

from bs4 import BeautifulSoup, Tag

from lti_app.models import AccessibilityIssue, IssueCategory, IssueSeverity
from lti_app.core.accessibility.rules.base import AccessibilityRule


NAMED_COLORS = {
    "black": "#000000",
    "white": "#ffffff",
    "red": "#ff0000",
    "green": "#008000",
    "blue": "#0000ff",
    "yellow": "#ffff00",
    "orange": "#ffa500",
    "purple": "#800080",
    "gray": "#808080",
    "grey": "#808080",
    "navy": "#000080",
    "teal": "#008080",
    "maroon": "#800000",
    "olive": "#808000",
    "lime": "#00ff00",
    "aqua": "#00ffff",
    "fuchsia": "#ff00ff",
    "silver": "#c0c0c0",
    "brown": "#a52a2a",
    "darkgray": "#a9a9a9",
}


def hex_to_rgb(hex_color: str) -> Optional[Tuple[int, int, int]]:
    """Convert hex color to RGB tuple."""
    hex_color = hex_color.lstrip("#")

    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)

    if len(hex_color) != 6:
        return None

    try:
        return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    """Convert RGB tuple to hex color string."""
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def adjust_color_for_contrast(
    fg_rgb: Tuple[int, int, int],
    bg_rgb: Tuple[int, int, int],
    target_ratio: float = 4.5,
) -> Tuple[int, int, int]:
    """Adjust foreground color to meet target contrast ratio against background.

    Preserves hue by interpolating toward black or white.
    """
    if contrast_ratio(fg_rgb, bg_rgb) >= target_ratio:
        return fg_rgb

    bg_lum = luminance(bg_rgb)
    # Darken fg if background is light, lighten if background is dark
    if bg_lum > 0.18:
        target_rgb = (0, 0, 0)
    else:
        target_rgb = (255, 255, 255)

    # Binary search for the minimum interpolation that meets the ratio
    lo, hi = 0.0, 1.0
    best = target_rgb
    for _ in range(30):
        mid = (lo + hi) / 2
        candidate = tuple(
            int(fg_rgb[i] + (target_rgb[i] - fg_rgb[i]) * mid) for i in range(3)
        )
        if contrast_ratio(candidate, bg_rgb) >= target_ratio:
            best = candidate
            hi = mid
        else:
            lo = mid

    return best


def adjust_background_for_contrast(
    fg_rgb: Tuple[int, int, int],
    bg_rgb: Tuple[int, int, int],
    target_ratio: float = 4.5,
) -> Tuple[int, int, int]:
    """Adjust background color (keeping fg fixed) to meet target ratio.

    Fallback for the case where ``adjust_color_for_contrast`` can't move
    the foreground — typically ``#000000`` text on a bg with luminance
    just above 0.18, where the adjuster picks target=black and fg is
    already there.

    Moves bg *away* from fg: if fg is dark, lighten bg toward white;
    if fg is light, darken bg toward black. Preserves bg hue via the
    same black/white interpolation used by the fg adjuster, so the
    palette shift is as small as possible.
    """
    if contrast_ratio(fg_rgb, bg_rgb) >= target_ratio:
        return bg_rgb

    fg_lum = luminance(fg_rgb)
    # If fg is dark, bg needs to get lighter (toward white);
    # if fg is light, bg needs to get darker (toward black).
    if fg_lum < 0.18:
        target_rgb = (255, 255, 255)
    else:
        target_rgb = (0, 0, 0)

    lo, hi = 0.0, 1.0
    best = target_rgb
    for _ in range(30):
        mid = (lo + hi) / 2
        candidate = tuple(
            int(bg_rgb[i] + (target_rgb[i] - bg_rgb[i]) * mid) for i in range(3)
        )
        if contrast_ratio(fg_rgb, candidate) >= target_ratio:
            best = candidate
            hi = mid
        else:
            lo = mid

    return best


def luminance(rgb: Tuple[int, int, int]) -> float:
    """Calculate relative luminance of an RGB color."""

    def adjust(c: int) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * adjust(r) + 0.7152 * adjust(g) + 0.0722 * adjust(b)


def contrast_ratio(color1: Tuple[int, int, int], color2: Tuple[int, int, int]) -> float:
    """Calculate contrast ratio between two RGB colors."""
    l1 = luminance(color1)
    l2 = luminance(color2)

    lighter = max(l1, l2)
    darker = min(l1, l2)

    return (lighter + 0.05) / (darker + 0.05)


class InsufficientContrastRule(AccessibilityRule):
    """CLR001: Check for insufficient color contrast."""

    rule_id = "CLR001"
    severity = IssueSeverity.ERROR
    category = IssueCategory.CONTRAST
    wcag_criterion = "1.4.3"
    message_template = "Insufficient color contrast"
    can_auto_fix = True
    fix_description = "Adjust foreground color to meet 4.5:1 contrast ratio"

    # Minimum contrast ratios.
    #
    # WCAG 1.4.3 Level AA strictly requires 4.5:1 (normal) and 3:1 (large),
    # and the spec explicitly forbids rounding ("4.499:1 would not meet
    # the 4.5:1 threshold"). WebAIM's contrast checker — the de-facto
    # gold standard — agrees. Both confirm that #da3c2b on white
    # (computed 4.504:1) PASSES WCAG AA Normal.
    #
    # PopeTech is stricter than the spec. On real Art103 data it flagged
    # #da3c2b AND our previous fix output #d93b2a (4.556:1, also a WCAG
    # pass per WebAIM) as "Very low contrast" errors. The most plausible
    # cause is that PopeTech computes against the actual rendered Canvas
    # page background (slightly off-white, often ~#fafafa) where
    # #da3c2b drops to ~4.32:1 — a real fail.
    #
    # We deliberately over-correct the WCAG floor by 0.2 points to
    # satisfy PopeTech's behavior and account for ~#fafafa rendered
    # backgrounds. This means our remediation output is technically
    # slightly stricter than WCAG requires, but the visual difference
    # is imperceptible and we get clean PopeTech reports.
    NORMAL_TEXT_RATIO = 4.7
    LARGE_TEXT_RATIO = 3.2

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find elements with potential contrast issues."""
        issues = []

        # Check elements with inline color styles
        for element in soup.find_all(style=True):
            if not isinstance(element, Tag):
                continue

            style = element.get("style", "")
            if not style:
                continue

            fg_color = self._extract_color(style, "color")
            bg_color = self._extract_color(style, "background-color")

            if fg_color and bg_color:
                fg_rgb = hex_to_rgb(fg_color)
                bg_rgb = hex_to_rgb(bg_color)

                if fg_rgb and bg_rgb:
                    ratio = contrast_ratio(fg_rgb, bg_rgb)
                    required = self.LARGE_TEXT_RATIO if self._is_large_text(element) else self.NORMAL_TEXT_RATIO

                    if ratio < required:
                        text_preview = element.get_text(strip=True)[:30]
                        issues.append(
                            self.create_issue(
                                page_id=page_id,
                                message=f'Low contrast ratio ({ratio:.1f}:1, needs {required}:1) for text: "{text_preview}..."',
                                element_html=str(element)[:200],
                            )
                        )

            # Element has inline `color:` but no inline `background-color:`.
            # CLU-72: walk ancestors for an inherited background-color
            # BEFORE falling back to assumed white. Art103 produced 4
            # silent-pass cases where `#d53a29` on white computed to
            # ~4.702 (just above our 4.7 threshold) but the real
            # ancestor bg was `#fbeeb8` (yellow highlight) where the
            # true ratio is ~4.03. Previously: no issue created →
            # fixer never ran → PopeTech flagged it.
            elif fg_color:
                fg_rgb = hex_to_rgb(fg_color)
                if fg_rgb:
                    ancestor_bg_hex = self._resolve_ancestor_color(
                        element, "background-color"
                    )
                    ancestor_bg_rgb = (
                        hex_to_rgb(ancestor_bg_hex) if ancestor_bg_hex else None
                    )
                    bg_rgb = ancestor_bg_rgb or (255, 255, 255)
                    ratio = contrast_ratio(fg_rgb, bg_rgb)
                    required = self.LARGE_TEXT_RATIO if self._is_large_text(element) else self.NORMAL_TEXT_RATIO
                    if ratio < required:
                        text = element.get_text(strip=True)[:30]
                        if text:
                            bg_label = ancestor_bg_hex or "#ffffff (assumed)"
                            issues.append(
                                self.create_issue(
                                    page_id=page_id,
                                    message=(
                                        f'Low contrast ratio ({ratio:.2f}:1, '
                                        f'needs {required}:1) — {fg_color} on '
                                        f'{bg_label} for text: "{text}..."'
                                    ),
                                    element_html=str(element)[:200],
                                )
                            )

        # Second pass: check elements with inherited colors (no inline style)
        for element in soup.find_all(True):
            if not isinstance(element, Tag):
                continue
            style = element.get("style", "")
            if style and re.search(r"(?:^|;)\s*color\s*:", style):
                continue  # Already checked in first pass
            text = element.get_text(strip=True)
            if not text or element.find(True):
                continue  # Skip empty elements and containers

            # Walk ancestors for inherited colors
            fg_hex = self._resolve_ancestor_color(element, "color")
            bg_hex = self._resolve_ancestor_color(element, "background-color")
            if not fg_hex or not bg_hex:
                continue

            fg_rgb = hex_to_rgb(fg_hex)
            bg_rgb = hex_to_rgb(bg_hex)
            if not fg_rgb or not bg_rgb:
                continue

            ratio = contrast_ratio(fg_rgb, bg_rgb)
            target = self.LARGE_TEXT_RATIO if self._is_large_text(element) else self.NORMAL_TEXT_RATIO
            if ratio < target:
                text_preview = text[:30]
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f'Very low contrast ({ratio:.2f}:1, needs {target}:1) — inherited colors {fg_hex} on {bg_hex}',
                        element_html=str(element)[:200],
                    )
                )

        return issues

    def _resolve_ancestor_color(self, element, prop: str) -> Optional[str]:
        """Walk DOM ancestors to find inherited inline color."""
        current = element
        while current and hasattr(current, 'get'):
            style = current.get("style", "")
            if style:
                color = self._extract_color(style, prop)
                if color:
                    return color
            current = current.parent
        return None

    def _extract_color(self, style: str, property_name: str) -> Optional[str]:
        """Extract a color value from inline style.

        Uses negative lookbehind to avoid matching 'color' within 'background-color'.
        """
        # Negative lookbehind prevents matching 'color' inside 'background-color'
        lb = r"(?<![a-zA-Z-])" if property_name == "color" else ""

        # Match hex colors
        hex_pattern = rf"{lb}{property_name}\s*:\s*(#[0-9A-Fa-f]{{3,6}})"
        match = re.search(hex_pattern, style)
        if match:
            return match.group(1)

        # Match rgb colors
        rgb_pattern = rf"{lb}{property_name}\s*:\s*rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)"
        match = re.search(rgb_pattern, style)
        if match:
            r, g, b = match.groups()
            return f"#{int(r):02x}{int(g):02x}{int(b):02x}"

        # Match named colors
        name_pattern = rf"{lb}{property_name}\s*:\s*([a-zA-Z]+)"
        match = re.search(name_pattern, style)
        if match:
            name = match.group(1).lower()
            if name in NAMED_COLORS:
                return NAMED_COLORS[name]

        return None

    def _is_large_text(self, element: Tag) -> bool:
        """Check if element has large text (WCAG: >=18pt or >=14pt bold).

        Large text only needs 3:1 contrast ratio instead of 4.5:1.
        """
        style = element.get("style", "")
        if not style:
            return False

        font_size_px = None

        # Match px
        match = re.search(r"font-size\s*:\s*([\d.]+)\s*px", style)
        if match:
            font_size_px = float(match.group(1))

        # Match pt (1pt = 1.333px)
        if font_size_px is None:
            match = re.search(r"font-size\s*:\s*([\d.]+)\s*pt", style)
            if match:
                font_size_px = float(match.group(1)) * 1.333

        # Match em/rem (assume 1em = 16px)
        if font_size_px is None:
            match = re.search(r"font-size\s*:\s*([\d.]+)\s*(?:em|rem)", style)
            if match:
                font_size_px = float(match.group(1)) * 16

        # Match % (assume 100% = 16px)
        if font_size_px is None:
            match = re.search(r"font-size\s*:\s*([\d.]+)\s*%", style)
            if match:
                font_size_px = float(match.group(1)) / 100 * 16

        if font_size_px is None:
            return False

        is_bold = bool(
            re.search(r"font-weight\s*:\s*(bold|[7-9]\d{2})", style)
            or element.find_parent(["strong", "b"])
            or element.find(["strong", "b"])
        )

        # 18pt = 24px, 14pt = 18.67px
        if font_size_px >= 24:
            return True
        if font_size_px >= 18.67 and is_bold:
            return True

        return False
