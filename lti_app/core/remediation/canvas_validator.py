"""Validate and sanitize HTML against Canvas Rich Content Editor constraints."""

import logging
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

from lti_app.core.remediation.canvas_allowlist import (
    CANVAS_ALLOWED_ARIA_ATTRIBUTES,
    CANVAS_ALLOWED_CSS_PROPERTIES,
    CANVAS_ALLOWED_GLOBAL_ATTRIBUTES,
    CANVAS_ALLOWED_ROLE_VALUES,
    CANVAS_ALLOWED_TAGS,
    CANVAS_ELEMENT_ATTRIBUTES,
    CANVAS_FORBIDDEN_TAGS,
)

logger = logging.getLogger(__name__)


@dataclass
class ValidationIssue:
    """A single Canvas HTML validation issue."""
    severity: str  # "error" | "warning"
    element: str
    message: str
    original_html: str = ""


@dataclass
class ValidationResult:
    """Result of Canvas HTML validation or sanitization."""
    is_valid: bool = True
    issues: list[ValidationIssue] = field(default_factory=list)
    tags_stripped: int = 0
    attributes_stripped: int = 0
    css_properties_stripped: int = 0


class CanvasHTMLValidator:
    """Validate and sanitize HTML for Canvas Rich Content Editor compatibility."""

    # Tags that should be decomposed entirely (children removed too)
    _DECOMPOSE_TAGS = {"script", "style", "form", "input", "select", "textarea", "button", "object", "embed", "applet", "link", "meta"}

    # Tags that should be unwrapped (children preserved, tag replaced)
    _UNWRAP_TAGS = {"figure", "figcaption", "font", "center", "blink", "marquee", "big"}

    def validate(self, html: str) -> ValidationResult:
        """Check HTML against Canvas allowlist without modifying it.

        Args:
            html: HTML string to validate.

        Returns:
            ValidationResult with any issues found.
        """
        soup = BeautifulSoup(html, "html.parser")
        result = ValidationResult()

        for tag in soup.find_all(True):
            tag_name = tag.name.lower()

            if tag_name in CANVAS_FORBIDDEN_TAGS:
                result.is_valid = False
                result.issues.append(ValidationIssue(
                    severity="error",
                    element=tag_name,
                    message=f"Forbidden tag <{tag_name}> found",
                    original_html=str(tag)[:200],
                ))

            if tag_name not in CANVAS_ALLOWED_TAGS and tag_name not in CANVAS_FORBIDDEN_TAGS:
                result.issues.append(ValidationIssue(
                    severity="warning",
                    element=tag_name,
                    message=f"Unknown tag <{tag_name}> may be stripped by Canvas",
                    original_html=str(tag)[:200],
                ))

            # Check inline CSS
            style = tag.get("style", "")
            if style:
                bad_props = self._find_disallowed_css_properties(style)
                for prop in bad_props:
                    result.issues.append(ValidationIssue(
                        severity="warning",
                        element=tag_name,
                        message=f"CSS property '{prop}' may not be supported",
                    ))

        return result

    def sanitize(self, html: str) -> tuple[str, ValidationResult]:
        """Sanitize HTML by removing or converting disallowed elements.

        Args:
            html: HTML string to sanitize.

        Returns:
            Tuple of (sanitized_html, ValidationResult with changes made).
        """
        soup = BeautifulSoup(html, "html.parser")
        result = ValidationResult()

        # Pass 1: Decompose dangerous tags (remove entirely with children)
        for tag_name in self._DECOMPOSE_TAGS:
            for tag in soup.find_all(tag_name):
                result.tags_stripped += 1
                result.issues.append(ValidationIssue(
                    severity="error",
                    element=tag_name,
                    message=f"Removed <{tag_name}> element",
                    original_html=str(tag)[:200],
                ))
                tag.decompose()

        # Pass 2: Convert h1 to h2
        for h1 in soup.find_all("h1"):
            h1.name = "h2"
            result.tags_stripped += 1
            result.issues.append(ValidationIssue(
                severity="warning",
                element="h1",
                message="Converted <h1> to <h2>",
            ))

        # Pass 3: Unwrap tags that should preserve children
        for tag_name in self._UNWRAP_TAGS:
            for tag in soup.find_all(tag_name):
                if tag_name == "figure":
                    # Convert figure to div with role="group"
                    tag.name = "div"
                    tag["role"] = "group"
                    # Try to get aria-label from figcaption
                    figcaption = tag.find("figcaption")
                    if figcaption:
                        caption_text = figcaption.get_text(strip=True)
                        if caption_text:
                            tag["aria-label"] = caption_text[:150]
                        figcaption.name = "p"
                    result.tags_stripped += 1
                elif tag_name == "figcaption":
                    tag.name = "p"
                    result.tags_stripped += 1
                elif tag_name == "center":
                    tag.name = "div"
                    tag["style"] = "text-align: center;"
                    result.tags_stripped += 1
                elif tag_name == "font":
                    # Convert font attributes to style
                    style_parts = []
                    color = tag.get("color")
                    if color:
                        style_parts.append(f"color: {color}")
                        del tag["color"]
                    size = tag.get("size")
                    if size:
                        del tag["size"]
                    face = tag.get("face")
                    if face:
                        style_parts.append(f"font-family: {face}")
                        del tag["face"]
                    tag.name = "span"
                    if style_parts:
                        existing = tag.get("style", "")
                        tag["style"] = "; ".join(style_parts) + (f"; {existing}" if existing else "")
                    result.tags_stripped += 1
                elif tag_name in ("blink", "marquee", "big"):
                    tag.name = "span"
                    result.tags_stripped += 1

                result.issues.append(ValidationIssue(
                    severity="warning",
                    element=tag_name,
                    message=f"Converted <{tag_name}> to compatible element",
                ))

        # Pass 4: Validate role attribute values
        for tag in soup.find_all(attrs={"role": True}):
            if not isinstance(tag, Tag):
                continue
            role_value = tag.get("role", "").strip().lower()
            if role_value and role_value not in CANVAS_ALLOWED_ROLE_VALUES:
                result.attributes_stripped += 1
                result.issues.append(ValidationIssue(
                    severity="warning",
                    element=tag.name,
                    message=f"Removed non-standard role=\"{role_value}\"",
                ))
                del tag["role"]

        # Pass 5: Filter inline CSS properties
        for tag in soup.find_all(style=True):
            if not isinstance(tag, Tag):
                continue
            style = tag.get("style", "")
            if style:
                filtered_style, stripped_count = self._filter_css_properties(style)
                if stripped_count > 0:
                    result.css_properties_stripped += stripped_count
                    if filtered_style.strip():
                        tag["style"] = filtered_style
                    else:
                        del tag["style"]

        # Pass 6: Strip non-allowed attributes per element
        # Build the set of globally allowed attribute names (without the glob "data-*")
        global_attrs = {a for a in CANVAS_ALLOWED_GLOBAL_ATTRIBUTES if a != "data-*"}
        aria_attrs = CANVAS_ALLOWED_ARIA_ATTRIBUTES

        for tag in soup.find_all(True):
            if not isinstance(tag, Tag):
                continue
            tag_name = tag.name.lower()
            element_attrs = CANVAS_ELEMENT_ATTRIBUTES.get(tag_name, set())
            allowed = global_attrs | aria_attrs | element_attrs

            for attr_name in list(tag.attrs.keys()):
                # data-* attributes are always allowed
                if attr_name.startswith("data-"):
                    continue
                if attr_name not in allowed:
                    del tag[attr_name]
                    result.attributes_stripped += 1

        result.is_valid = result.tags_stripped == 0 and result.css_properties_stripped == 0
        return str(soup), result

    @staticmethod
    def _find_disallowed_css_properties(style: str) -> list[str]:
        """Find CSS properties in a style string that are not in the allowlist."""
        bad = []
        # Split on semicolons, parse property names
        for declaration in style.split(";"):
            declaration = declaration.strip()
            if not declaration or ":" not in declaration:
                continue
            prop = declaration.split(":")[0].strip().lower()
            if prop and prop not in CANVAS_ALLOWED_CSS_PROPERTIES:
                bad.append(prop)
        return bad

    @staticmethod
    def _filter_css_properties(style: str) -> tuple[str, int]:
        """Filter a CSS style string, keeping only allowed properties.

        Returns:
            Tuple of (filtered_style, count_of_stripped_properties).
        """
        kept = []
        stripped = 0
        for declaration in style.split(";"):
            declaration = declaration.strip()
            if not declaration or ":" not in declaration:
                continue
            prop = declaration.split(":")[0].strip().lower()
            if prop in CANVAS_ALLOWED_CSS_PROPERTIES:
                kept.append(declaration)
            else:
                stripped += 1
        return "; ".join(kept) + (";" if kept else ""), stripped
