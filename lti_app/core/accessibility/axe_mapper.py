"""Map axe-core rule IDs to Remedy Canvas LTI rule IDs and classify issue source.

axe-core uses its own rule taxonomy (e.g., 'color-contrast', 'image-alt').
This module translates those into Remedy Canvas LTI's rule IDs and determines whether
each issue is in course content (fixable) or Canvas platform UI (not fixable).
"""

from lti_app.models import AccessibilityIssue, IssueSeverity, IssueCategory

# axe-core rule ID -> Remedy Canvas LTI rule ID
AXE_TO_CLU_RULE: dict[str, str] = {
    # Images
    "image-alt": "IMG001",
    "input-image-alt": "IMG001",
    "area-alt": "IMG001",
    "image-redundant-alt": "IMG003",
    "role-img-alt": "IMG001",
    # Headings
    "empty-heading": "HDG003",
    "heading-order": "HDG002",
    "page-has-heading-one": "HDG005",
    # Tables
    "td-headers-attr": "TBL001",
    "th-has-data-cells": "TBL004",
    "empty-table-header": "TBL004",
    "table-fake-caption": "TBL003",
    "scope-attr-valid": "TBL002",
    # Links
    "link-name": "LNK003",
    "link-in-text-block": "LNK001",
    # Contrast
    "color-contrast": "CLR001",
    "color-contrast-enhanced": "CLR001",
    # Buttons / Forms
    "button-name": "BTN001",
    "label": "FORM002",
    "select-name": "FORM008",
    "input-button-name": "BTN001",
    "form-field-multiple-labels": "FORM004",
    "label-title-only": "FORM009",
    "fieldset": "FORM006",
    # Structure
    "marquee": "STR002",
    "blink": "STR002",
    "html-has-lang": "LANG001",
    "html-lang-valid": "LANG001",
    "document-title": "PAGE001",
    "bypass": "NAV001",
    "meta-refresh": "NAV002",
    # ARIA
    "aria-valid-attr": "ARIA001",
    "aria-valid-attr-value": "ARIA001",
    "aria-allowed-attr": "ARIA001",
    "aria-required-attr": "ARIA001",
    "aria-roles": "ARIA001",
    # Auth
    "autocomplete-valid": "AUTH001",
    # Media
    "video-caption": "MDA001",
    "audio-caption": "MDA001",
    "frame-title": "MDA002",
    # Focus / Keyboard
    "tabindex": "FOC001",
    "focus-order-semantics": "FOC001",
}

# axe-core impact -> Remedy Canvas LTI severity
AXE_IMPACT_TO_SEVERITY: dict[str, IssueSeverity] = {
    "critical": IssueSeverity.ERROR,
    "serious": IssueSeverity.ERROR,
    "moderate": IssueSeverity.WARNING,
    "minor": IssueSeverity.INFO,
}

# Remedy Canvas LTI rule ID prefix -> IssueCategory (mirrors rules/ conventions)
_RULE_PREFIX_TO_CATEGORY: dict[str, IssueCategory] = {
    "IMG": IssueCategory.IMAGES,
    "HDG": IssueCategory.HEADINGS,
    "TBL": IssueCategory.TABLES,
    "LNK": IssueCategory.LINKS,
    "CLR": IssueCategory.CONTRAST,
    "BTN": IssueCategory.STRUCTURE,
    "FORM": IssueCategory.FORMS,
    "STR": IssueCategory.STRUCTURE,
    "LANG": IssueCategory.STRUCTURE,
    "PAGE": IssueCategory.STRUCTURE,
    "NAV": IssueCategory.STRUCTURE,
    "ARIA": IssueCategory.STRUCTURE,
    "MDA": IssueCategory.MEDIA,
    "FOC": IssueCategory.FOCUS,
}

# CSS selectors that indicate Canvas UI chrome (not course content)
CANVAS_PLATFORM_SELECTORS = [
    "#header", "#nav", ".ic-app-header", ".ic-app-nav-toggle-and-crumbs",
    "#breadcrumbs", "#footer", ".navigation-tray", "#left-side",
    ".course-menu", "#right-side-wrapper", "#flash_message_holder",
    "#global_nav_tray_container", ".ic-Layout-watermark", "#skip_navigation_link",
]

# CSS selectors that indicate course content area
CONTENT_AREA_SELECTORS = [
    "#content", ".user_content", ".ic-Layout-contentMain", ".show-content",
]


def _category_for_rule(rule_id: str) -> IssueCategory:
    """Derive IssueCategory from a Remedy Canvas LTI rule ID prefix."""
    for prefix, category in _RULE_PREFIX_TO_CATEGORY.items():
        if rule_id.startswith(prefix):
            return category
    return IssueCategory.STRUCTURE


def is_canvas_platform_issue(node: dict) -> bool:
    """Determine if an axe violation node is in Canvas UI chrome vs course content."""
    targets = node.get("target", [])

    for selector in targets:
        sel_str = selector if isinstance(selector, str) else str(selector)
        # Check if in content area first (takes priority)
        for content_sel in CONTENT_AREA_SELECTORS:
            if content_sel in sel_str:
                return False
        # Check if in platform chrome
        for platform_sel in CANVAS_PLATFORM_SELECTORS:
            if platform_sel in sel_str:
                return True

    # Default: if not clearly in content area, assume platform
    return True


def axe_violation_to_issues(
    violation: dict,
    page_id: str,
    page_url: str,
) -> list[AccessibilityIssue]:
    """Convert an axe-core violation to Remedy Canvas LTI AccessibilityIssue objects."""
    axe_rule = violation.get("id", "")
    clu_rule = AXE_TO_CLU_RULE.get(axe_rule, f"AXE_{axe_rule.upper().replace('-', '_')}")
    impact = violation.get("impact", "moderate")
    severity = AXE_IMPACT_TO_SEVERITY.get(impact, IssueSeverity.WARNING)
    help_text = violation.get("help", "")
    description = violation.get("description", "")
    category = _category_for_rule(clu_rule)

    # Extract WCAG criterion from tags like "wcag111" -> "1.1.1"
    wcag_criterion = ""
    for tag in violation.get("tags", []):
        digits = tag.replace("wcag", "")
        if len(digits) >= 3 and digits.isdigit():
            wcag_criterion = f"{digits[0]}.{digits[1]}.{digits[2:]}"
            break

    issues: list[AccessibilityIssue] = []
    for node in violation.get("nodes", []):
        source = "canvas_platform" if is_canvas_platform_issue(node) else "course_content"

        # For color-contrast violations, capture the axe-reported
        # rendered fg/bg colors + ratio so the transformer can inject
        # a precise inline color override against the ACTUAL rendered
        # background (not the assumed white the static rule uses).
        axe_meta: dict | None = None
        if axe_rule in ("color-contrast", "color-contrast-enhanced"):
            for any_check in (node.get("any") or []):
                data = any_check.get("data") or {}
                if "fgColor" in data and "bgColor" in data:
                    axe_meta = {
                        "fg_color": data.get("fgColor"),
                        "bg_color": data.get("bgColor"),
                        "contrast_ratio": data.get("contrastRatio"),
                        "expected_ratio": data.get("expectedContrastRatio"),
                        "font_size": data.get("fontSize"),
                        "font_weight": data.get("fontWeight"),
                        "target": (node.get("target") or [None])[0],
                    }
                    break

        issues.append(AccessibilityIssue(
            id=f"{clu_rule}_{page_id}_{len(issues)}",
            rule_id=clu_rule,
            severity=severity,
            category=category,
            wcag_criterion=wcag_criterion,
            message=help_text or description,
            page_id=page_id,
            canvas_url=page_url,
            element_html=(node.get("html", ""))[:200],
            source=source,
            can_auto_fix=(source == "course_content"),
            axe_meta=axe_meta,
        ))

    return issues
