"""Accessibility rules for structural elements (WCAG 1.3.1, 4.1.1, 1.4.4)."""

import re

from bs4 import BeautifulSoup, Tag

from lti_app.models import AccessibilityIssue, IssueCategory, IssueSeverity
from lti_app.core.accessibility.rules.base import AccessibilityRule


class EmptyElementsRule(AccessibilityRule):
    """STR001: Detect consecutive empty paragraphs/divs."""

    rule_id = "STR001"
    severity = IssueSeverity.WARNING
    category = IssueCategory.STRUCTURE
    wcag_criterion = "1.3.1"
    message_template = "Consecutive empty elements found"
    can_auto_fix = True
    fix_description = "Remove consecutive empty paragraphs and divs"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        body = soup.find("body") or soup

        for tag_name in ("p", "div"):
            elements = body.find_all(tag_name)
            i = 0
            while i < len(elements) - 1:
                el = elements[i]
                if self._is_empty(el):
                    consecutive = 1
                    j = i + 1
                    while j < len(elements) and self._is_empty(elements[j]):
                        # Check that they are actual siblings (not distant)
                        if self._are_adjacent(el, elements[j]):
                            consecutive += 1
                            j += 1
                        else:
                            break
                    if consecutive >= 2:
                        issues.append(
                            self.create_issue(
                                page_id=page_id,
                                message=f"{consecutive} consecutive empty <{tag_name}> elements",
                                element_html=str(el)[:200],
                            )
                        )
                    i = j
                else:
                    i += 1

        return issues

    @staticmethod
    def _is_empty(tag: Tag) -> bool:
        """Check if a tag is effectively empty (whitespace/nbsp/br only)."""
        # Preserve elements with id, role, or aria-* attributes
        if tag.get("id") or tag.get("role"):
            return False
        for attr in tag.attrs:
            if attr.startswith("aria-"):
                return False

        text = tag.get_text(strip=True)
        text = text.replace("\u00a0", "").strip()  # Remove &nbsp;
        if text:
            return False

        # Check if it only contains <br> tags
        children = [c for c in tag.children if not (isinstance(c, str) and not c.strip())]
        return all(
            isinstance(c, Tag) and c.name == "br"
            for c in children
        ) if children else True

    @staticmethod
    def _are_adjacent(el1: Tag, el2: Tag) -> bool:
        """Check if two elements are adjacent siblings."""
        sibling = el1.next_sibling
        while sibling is not None:
            if isinstance(sibling, str) and not sibling.strip():
                sibling = sibling.next_sibling
                continue
            return sibling == el2
        return False


class DeprecatedTagsRule(AccessibilityRule):
    """STR002: Detect deprecated HTML tags."""

    rule_id = "STR002"
    severity = IssueSeverity.ERROR
    category = IssueCategory.STRUCTURE
    wcag_criterion = "4.1.2"
    message_template = "Deprecated HTML tag found"
    can_auto_fix = True
    fix_description = "Convert deprecated tags to modern equivalents"

    DEPRECATED_TAGS = {"font", "center", "blink", "marquee", "big", "strike", "tt"}

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        for tag_name in self.DEPRECATED_TAGS:
            for tag in soup.find_all(tag_name):
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f"Deprecated <{tag_name}> tag found",
                        element_html=str(tag)[:200],
                    )
                )
        return issues


class BrokenARIAReferenceRule(AccessibilityRule):
    """ARIA001: Detect broken ARIA references (labelledby/describedby/controls to nonexistent IDs)."""

    rule_id = "ARIA001"
    severity = IssueSeverity.ERROR
    category = IssueCategory.STRUCTURE
    wcag_criterion = "4.1.2"
    message_template = "ARIA attribute references nonexistent element ID"
    can_auto_fix = True
    fix_description = "Remove broken ARIA references"

    _ARIA_REF_ATTRS = ("aria-labelledby", "aria-describedby", "aria-controls", "aria-owns")

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        all_ids = {tag["id"] for tag in soup.find_all(id=True)}

        for attr_name in self._ARIA_REF_ATTRS:
            for element in soup.find_all(attrs={attr_name: True}):
                if not isinstance(element, Tag):
                    continue
                ref_value = element.get(attr_name, "").strip()
                if not ref_value:
                    continue
                # ARIA ref attrs accept space-separated ID lists
                ref_ids = ref_value.split()
                missing = [rid for rid in ref_ids if rid not in all_ids]
                if missing:
                    issues.append(
                        self.create_issue(
                            page_id=page_id,
                            message=f'{attr_name} references missing ID(s): {", ".join(missing[:3])}',
                            element_html=str(element)[:200],
                        )
                    )

        return issues


class SmallTextRule(AccessibilityRule):
    """STR003: Detect text with very small font-size (<=10px / <10pt / <0.75em)."""

    rule_id = "STR003"
    severity = IssueSeverity.WARNING
    category = IssueCategory.STRUCTURE
    wcag_criterion = "1.4.4"
    message_template = "Text has very small font size"
    can_auto_fix = True
    fix_description = "Remove small font-size or normalize to readable size"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        for element in soup.find_all(style=True):
            if not isinstance(element, Tag):
                continue
            style = element.get("style", "")
            if not style:
                continue
            text = element.get_text(strip=True)
            if not text:
                continue
            if self._has_small_font(style):
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f'Very small text: "{text[:40]}"',
                        element_html=str(element)[:200],
                    )
                )
        return issues

    @staticmethod
    def _has_small_font(style: str) -> bool:
        """Check if inline style has a font-size below minimum readability threshold."""
        # px (<= 10px per PopeTech algorithm)
        match = re.search(r"font-size\s*:\s*([\d.]+)\s*px", style)
        if match and float(match.group(1)) <= 10:
            return True
        # pt (< 10pt)
        match = re.search(r"font-size\s*:\s*([\d.]+)\s*pt", style)
        if match and float(match.group(1)) < 10:
            return True
        # em/rem (< 0.75em)
        match = re.search(r"font-size\s*:\s*([\d.]+)\s*(?:em|rem)", style)
        if match and float(match.group(1)) < 0.75:
            return True
        # % (< 75%)
        match = re.search(r"font-size\s*:\s*([\d.]+)\s*%", style)
        if match and float(match.group(1)) < 75:
            return True
        return False


# Matches an active "text-decoration: underline" declaration anywhere in
# an inline style. We exclude the literal "none" and "line-through" values
# so we don't false-positive on text-decoration: none / line-through.
_UNDERLINE_STYLE_RE = re.compile(
    r"text-decoration\s*:\s*[^;]*\bunderline\b",
    re.IGNORECASE,
)


def _style_has_underline(style: str) -> bool:
    """Return True if an inline style declares text-decoration: underline."""
    if not style or "underline" not in style.lower():
        return False
    return bool(_UNDERLINE_STYLE_RE.search(style))


class UnderlinedTextRule(AccessibilityRule):
    """STR004: Detect underlined non-link text confusable with links.

    Catches both legacy ``<u>`` tags and modern inline-style
    ``text-decoration: underline`` declarations on any element. Skips
    underlines on ``<a>`` elements since links are naturally underlined.
    """

    rule_id = "STR004"
    severity = IssueSeverity.WARNING
    category = IssueCategory.STRUCTURE
    wcag_criterion = "1.3.1"
    message_template = "Underlined text — may be confused with links. Use bold or italic instead."
    can_auto_fix = True
    fix_description = "Replace <u> with <em>; strip text-decoration: underline from non-link elements"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []

        # Legacy <u> tags
        for u_tag in soup.find_all("u"):
            if not isinstance(u_tag, Tag):
                continue
            text = u_tag.get_text(strip=True)
            if text:
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=self.message_template,
                        element_html=str(u_tag)[:200],
                    )
                )

        # Inline-style underlines on non-link elements (Art103 PopeTech
        # regression: 65 alerts the legacy detector silently missed)
        for el in soup.find_all(style=True):
            if not isinstance(el, Tag):
                continue
            if el.name == "a":
                continue  # Links are SUPPOSED to be underlined
            style = el.get("style", "")
            if not _style_has_underline(style):
                continue
            text = el.get_text(strip=True)
            if not text:
                continue
            issues.append(
                self.create_issue(
                    page_id=page_id,
                    message=self.message_template,
                    element_html=str(el)[:200],
                )
            )

        return issues


class JustifiedTextRule(AccessibilityRule):
    """STR005: Detect elements with text-align: justify in inline style."""

    rule_id = "STR005"
    severity = IssueSeverity.WARNING
    category = IssueCategory.STRUCTURE
    wcag_criterion = "1.4.8"
    message_template = "Justified text — can create uneven spacing. Use left-aligned text."
    can_auto_fix = True
    fix_description = "Change text-align: justify to text-align: left"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        for element in soup.find_all(style=True):
            if not isinstance(element, Tag):
                continue
            style = element.get("style", "")
            if "text-align" in style and "justify" in style:
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=self.message_template,
                        element_html=str(element)[:200],
                    )
                )
        return issues


class DuplicateIdRule(AccessibilityRule):
    """DID001: Detect duplicate id attributes (WCAG 4.1.1)."""

    rule_id = "DID001"
    severity = IssueSeverity.ERROR
    category = IssueCategory.STRUCTURE
    wcag_criterion = "4.1.1"
    message_template = "Duplicate id attribute found"
    can_auto_fix = True
    fix_description = "Rename duplicate IDs to be unique"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        id_counts: dict[str, int] = {}
        for tag in soup.find_all(id=True):
            id_val = tag.get("id", "").strip()
            if not id_val:
                continue
            id_counts[id_val] = id_counts.get(id_val, 0) + 1
        for id_val, count in id_counts.items():
            if count > 1:
                issues.append(self.create_issue(
                    page_id=page_id,
                    message=f'Duplicate id="{id_val}" found {count} times — IDs must be unique',
                    element_html=f'id="{id_val}"',
                ))
        return issues


class AriaHiddenFocusableRule(AccessibilityRule):
    """ARIA002: Detect aria-hidden='true' on or above focusable elements (WCAG 4.1.2)."""

    rule_id = "ARIA002"
    severity = IssueSeverity.ERROR
    category = IssueCategory.STRUCTURE
    wcag_criterion = "4.1.2"
    message_template = "aria-hidden on focusable content"
    can_auto_fix = False
    fix_description = "Remove aria-hidden or remove focusable children"

    _FOCUSABLE_TAGS = frozenset({"button", "input", "select", "textarea"})

    def _is_focusable(self, tag: Tag) -> bool:
        if not isinstance(tag, Tag):
            return False
        if tag.name in self._FOCUSABLE_TAGS:
            return True
        if tag.name == "a" and tag.get("href"):
            return True
        tabindex = tag.get("tabindex")
        if tabindex is not None and tabindex.strip() != "-1":
            return True
        return False

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        for el in soup.find_all(attrs={"aria-hidden": "true"}):
            if not isinstance(el, Tag):
                continue
            if self._is_focusable(el):
                issues.append(self.create_issue(
                    page_id=page_id,
                    message='aria-hidden="true" set on a focusable element — keyboard users can reach it but screen readers cannot',
                    element_html=str(el)[:200],
                ))
                continue
            has_focusable = False
            for descendant in el.descendants:
                if self._is_focusable(descendant):
                    has_focusable = True
                    break
            if has_focusable:
                issues.append(self.create_issue(
                    page_id=page_id,
                    message='aria-hidden="true" on element containing focusable children — keyboard users can reach content that screen readers cannot see',
                    element_html=str(el)[:200],
                ))
        return issues


class MissingRequiredAriaPropsRule(AccessibilityRule):
    """ARIA003: Detect roles missing their required ARIA properties (WCAG 4.1.2)."""

    rule_id = "ARIA003"
    severity = IssueSeverity.ERROR
    category = IssueCategory.STRUCTURE
    wcag_criterion = "4.1.2"
    message_template = "Role missing required ARIA property"
    can_auto_fix = False
    fix_description = "Add the required ARIA property for this role"

    _REQUIRED_PROPS: dict[str, list[str]] = {
        "combobox": ["aria-expanded"],
        "meter": ["aria-valuenow"],
        "scrollbar": ["aria-controls", "aria-valuenow"],
        "slider": ["aria-valuenow"],
        "spinbutton": ["aria-valuenow"],
    }

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        for el in soup.find_all(attrs={"role": True}):
            if not isinstance(el, Tag):
                continue
            role = el.get("role", "").strip().lower()
            required = self._REQUIRED_PROPS.get(role, [])
            for prop in required:
                if not el.get(prop):
                    issues.append(self.create_issue(
                        page_id=page_id,
                        message=f'Role "{role}" requires {prop} attribute',
                        element_html=str(el)[:200],
                    ))
        return issues


class InvalidAriaRoleRule(AccessibilityRule):
    """ARIA004: Detect invalid ARIA role values (WCAG 4.1.2)."""

    rule_id = "ARIA004"
    severity = IssueSeverity.ERROR
    category = IssueCategory.STRUCTURE
    wcag_criterion = "4.1.2"
    message_template = "Invalid ARIA role value"
    can_auto_fix = True
    fix_description = "Remove invalid role attribute"

    _VALID_ROLES = frozenset({
        "alert", "alertdialog", "application", "article", "banner",
        "blockquote", "button", "caption", "cell", "checkbox", "code",
        "columnheader", "combobox", "command", "comment", "complementary",
        "composite", "contentinfo", "definition", "deletion", "dialog",
        "directory", "document", "emphasis", "feed", "figure", "form",
        "generic", "grid", "gridcell", "group", "heading", "img",
        "input", "insertion", "link", "list", "listbox", "listitem",
        "log", "main", "marquee", "math", "menu", "menubar", "menuitem",
        "menuitemcheckbox", "menuitemradio", "meter", "navigation",
        "none", "note", "option", "paragraph", "presentation",
        "progressbar", "radio", "radiogroup", "range", "region",
        "roletype", "row", "rowgroup", "rowheader", "scrollbar",
        "search", "searchbox", "section", "sectionhead", "select",
        "separator", "slider", "spinbutton", "status", "strong",
        "structure", "subscript", "superscript", "switch", "tab",
        "table", "tablist", "tabpanel", "term", "textbox", "time",
        "timer", "toolbar", "tooltip", "tree", "treegrid", "treeitem",
        "widget", "window",
    })

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        for el in soup.find_all(attrs={"role": True}):
            if not isinstance(el, Tag):
                continue
            role = el.get("role", "").strip().lower()
            if not role:
                continue
            if role not in self._VALID_ROLES:
                issues.append(self.create_issue(
                    page_id=page_id,
                    message=f'Invalid ARIA role "{role}" — use a valid WAI-ARIA role or a native HTML element',
                    element_html=str(el)[:200],
                ))
        return issues
