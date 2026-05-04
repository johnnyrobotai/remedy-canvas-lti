"""Accessibility rules for forms (WCAG 1.3.1, 3.3.7, 3.3.8)."""

import re

from bs4 import BeautifulSoup, Tag

from lti_app.models import AccessibilityIssue, IssueCategory, IssueSeverity
from lti_app.core.accessibility.rules.base import AccessibilityRule


class RedundantEntryRule(AccessibilityRule):
    """FORM001: Detect form inputs that may require redundant data entry."""

    rule_id = "FORM001"
    severity = IssueSeverity.WARNING
    category = IssueCategory.FORMS
    wcag_criterion = "3.3.7"
    message_template = "Form may require redundant data entry"
    can_auto_fix = False

    # Normalize label text for comparison (strip whitespace, lowercase, remove punctuation)
    _PUNCT_RE = re.compile(r"[^a-z0-9 ]")

    def _normalize(self, text: str) -> str:
        return self._PUNCT_RE.sub("", text.lower()).strip()

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find forms with inputs that share similar labels/names, suggesting redundant entry."""
        issues = []

        for form in soup.find_all("form"):
            if not isinstance(form, Tag):
                continue
            inputs = form.find_all(["input", "textarea", "select"])
            # Build a mapping from normalized label/name to list of input elements
            label_map: dict[str, list[Tag]] = {}

            for inp in inputs:
                if not isinstance(inp, Tag):
                    continue
                inp_type = inp.get("type", "text").lower()
                # Skip hidden, submit, reset, button, checkbox, radio — they don't gather user text
                if inp_type in {"hidden", "submit", "reset", "button", "image"}:
                    continue

                # Resolve label text: prefer <label for="id">, then aria-label, then name attr
                label_text = ""
                inp_id = inp.get("id")
                if inp_id:
                    label_tag = form.find("label", attrs={"for": inp_id})
                    if label_tag:
                        label_text = label_tag.get_text(strip=True)

                if not label_text:
                    label_text = inp.get("aria-label", "").strip()
                if not label_text:
                    label_text = inp.get("placeholder", "").strip()
                if not label_text:
                    label_text = inp.get("name", "").strip()

                key = self._normalize(label_text)
                if not key:
                    continue

                label_map.setdefault(key, []).append(inp)

            # Flag groups of 2+ inputs with the same normalized label key
            for key, group in label_map.items():
                if len(group) >= 2:
                    # Only report once per group
                    issues.append(
                        self.create_issue(
                            page_id=page_id,
                            message=(
                                f"Form contains {len(group)} inputs with similar label "
                                f'"{key}" — may require redundant data entry'
                            ),
                            element_html=str(group[0])[:200],
                        )
                    )

        return issues


class AccessibleAuthenticationRule(AccessibilityRule):
    """AUTH001: Detect password inputs missing autocomplete for accessible authentication."""

    rule_id = "AUTH001"
    severity = IssueSeverity.WARNING
    category = IssueCategory.FORMS
    wcag_criterion = "3.3.8"
    message_template = "Password input missing autocomplete attribute"
    can_auto_fix = True
    fix_description = "Add autocomplete attribute to password fields"

    _VALID_AUTOCOMPLETE = {"current-password", "new-password"}

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find password inputs missing a valid autocomplete attribute."""
        issues = []

        for inp in soup.find_all("input"):
            if not isinstance(inp, Tag):
                continue
            if inp.get("type", "").lower() != "password":
                continue

            autocomplete = inp.get("autocomplete", "").strip().lower()
            if autocomplete not in self._VALID_AUTOCOMPLETE:
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=(
                            "Password input is missing autocomplete=\"current-password\" or "
                            "\"new-password\" — required for accessible authentication (WCAG 3.3.8)"
                        ),
                        element_html=str(inp)[:200],
                    )
                )

        return issues


class EmptyFormLabelRule(AccessibilityRule):
    """FORM003: Detect <label> elements that are present but contain no content."""

    rule_id = "FORM003"
    severity = IssueSeverity.ERROR
    category = IssueCategory.FORMS
    wcag_criterion = "1.3.1"
    message_template = "A form label is present but contains no content"
    can_auto_fix = True
    fix_description = "Add descriptive text to empty form label"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find <label> elements with no text content and no images with alt text."""
        issues = []

        for label in soup.find_all("label"):
            if not isinstance(label, Tag):
                continue
            text = label.get_text(strip=True).replace("\u00a0", "").strip()
            if text:
                continue
            # Check for images with non-empty alt inside the label
            if any(
                img.get("alt", "").strip()
                for img in label.find_all("img")
            ):
                continue

            issues.append(
                self.create_issue(
                    page_id=page_id,
                    message="A form label is present but contains no content",
                    element_html=str(label)[:200],
                )
            )

        return issues


class MissingFieldsetRule(AccessibilityRule):
    """FORM005: Detect radio/checkbox inputs not enclosed in a <fieldset>."""

    rule_id = "FORM005"
    severity = IssueSeverity.ERROR
    category = IssueCategory.FORMS
    wcag_criterion = "1.3.1"
    message_template = "Checkboxes or radio buttons not enclosed in a fieldset"
    can_auto_fix = False
    fix_description = "Wrap related radio/checkbox groups in a <fieldset> with a <legend>"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find radio/checkbox inputs outside a <fieldset>, deduplicating by name group."""
        issues = []
        flagged_names: set[str] = set()

        for inp in soup.find_all("input"):
            if not isinstance(inp, Tag):
                continue
            inp_type = inp.get("type", "").lower()
            if inp_type not in ("radio", "checkbox"):
                continue

            # Check if enclosed in a <fieldset>
            if inp.find_parent("fieldset"):
                continue

            # Deduplicate: only flag once per name attribute group
            name = inp.get("name", "")
            dedup_key = f"{inp_type}:{name}" if name else None
            if dedup_key and dedup_key in flagged_names:
                continue
            if dedup_key:
                flagged_names.add(dedup_key)

            label_text = ""
            if name:
                label_text = f' (name="{name}")'

            issues.append(
                self.create_issue(
                    page_id=page_id,
                    message=(
                        f"{inp_type.capitalize()} input{label_text} is not enclosed "
                        f"in a <fieldset> — related controls should be grouped"
                    ),
                    element_html=str(inp)[:200],
                )
            )

        return issues


class OrphanedFormLabelRule(AccessibilityRule):
    """FORM007: Detect <label for="x"> where no element with id="x" exists."""

    rule_id = "FORM007"
    severity = IssueSeverity.ERROR
    category = IssueCategory.FORMS
    wcag_criterion = "1.3.1"
    message_template = "A form label is not correctly associated with a form control"
    can_auto_fix = True
    fix_description = "Associate label with a matching form control or remove orphaned for attribute"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find <label for="x"> elements where no element with id="x" exists in the document."""
        issues = []

        for label in soup.find_all("label"):
            if not isinstance(label, Tag):
                continue
            for_attr = label.get("for", "").strip()
            if not for_attr:
                continue

            # Look for any element with that id in the entire document
            target = soup.find(id=for_attr)
            if target:
                continue

            issues.append(
                self.create_issue(
                    page_id=page_id,
                    message=(
                        f'Label references for="{for_attr}" but no element with '
                        f'id="{for_attr}" exists — label is not associated with a control'
                    ),
                    element_html=str(label)[:200],
                )
            )

        return issues


class PersonalDataAutocompleteRule(AccessibilityRule):
    """FORM009: Detect inputs collecting personal data without autocomplete (WCAG 1.3.5)."""

    rule_id = "FORM009"
    severity = IssueSeverity.WARNING
    category = IssueCategory.FORMS
    wcag_criterion = "1.3.5"
    message_template = "Input missing autocomplete for personal data"
    can_auto_fix = True
    fix_description = "Add autocomplete attribute for personal data inputs"

    _SKIP_TYPES = frozenset({
        "hidden", "submit", "reset", "button", "image", "checkbox", "radio", "file",
    })

    _SIGNALS: list[tuple[str, str, str]] = [
        ("type", "email", "email"),
        ("type", "tel", "tel"),
        ("name", "email", "email"),
        ("name", "phone", "tel"),
        ("name", "tel", "tel"),
        ("name", "fname", "given-name"),
        ("name", "first.name", "given-name"),
        ("name", "first_name", "given-name"),
        ("name", "given", "given-name"),
        ("name", "lname", "family-name"),
        ("name", "last.name", "family-name"),
        ("name", "last_name", "family-name"),
        ("name", "family", "family-name"),
        ("name", "address", "street-address"),
        ("name", "street", "street-address"),
        ("name", "zip", "postal-code"),
        ("name", "postal", "postal-code"),
        ("name", "city", "address-level2"),
        ("name", "state", "address-level1"),
        ("name", "province", "address-level1"),
        ("name", "country", "country-name"),
    ]

    def _infer_autocomplete(self, inp_type: str, inp_name: str) -> str | None:
        for signal_type, pattern, autocomplete in self._SIGNALS:
            if signal_type == "type" and inp_type == pattern:
                return autocomplete
            if signal_type == "name" and pattern in inp_name:
                return autocomplete
        return None

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        for inp in soup.find_all("input"):
            if not isinstance(inp, Tag):
                continue
            if inp.get("autocomplete"):
                continue
            inp_type = inp.get("type", "text").lower()
            if inp_type in self._SKIP_TYPES:
                continue
            inp_name = inp.get("name", "").lower()
            suggested = self._infer_autocomplete(inp_type, inp_name)
            if suggested:
                issues.append(self.create_issue(
                    page_id=page_id,
                    message=f'Input appears to collect "{suggested}" data but has no autocomplete attribute',
                    element_html=str(inp)[:200],
                ))
        return issues
