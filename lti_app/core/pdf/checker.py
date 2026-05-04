"""PDF Accessibility Checker — all 32 Adobe Acrobat checks.

Ported from Project Remedy's ``pdf_checker.py`` and adapted for Remedy Canvas LTI:
- Dataclass models replaced with Pydantic ``CheckResult`` / ``CheckReport``
- Imports updated from ``project_remedy.pdf_semantics`` to ``lti_app.core.pdf.semantics``
- Categories normalised to snake_case keys
- ``run_all()`` returns our Pydantic ``CheckReport`` with error wrapping

Runs the same checks Adobe's accessibility checker performs, organised into
four categories: document, page_content, forms_tables_lists, and
alt_text_headings.

Usage::

    checker = PDFAccessibilityChecker("/path/to/report.pdf")
    report = checker.run_all(file_id="abc", filename="report.pdf")
    for r in report.results:
        print(r.rule_id, r.status)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator

import pikepdf

from lti_app.core.pdf.models import CheckReport, CheckResult, CheckStatus
from lti_app.core.pdf.semantics import (
    document_has_bookmarks,
    document_requires_bookmarks,
    find_node_page,
    get_rendered_multimedia_names,
    node_has_annotation_ref,
    node_has_content_association,
    node_has_direct_content as _shared_node_has_direct_content,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@dataclass
class _CharacterEncodingAnalysis:
    """Internal summary of whether the PDF text layer is trustworthy."""

    details: list[str] = field(default_factory=list)
    page_numbers: set[int] = field(default_factory=set)
    requires_rebuild: bool = False


def _resolve_pdf_object(obj):
    """Best-effort resolve for indirect pikepdf objects."""
    if isinstance(obj, pikepdf.Array):
        return obj
    if isinstance(obj, pikepdf.Object) and obj.is_indirect:
        try:
            return obj.resolve()
        except Exception:
            return obj
    return obj


# ---------------------------------------------------------------------------
# Structure tree walker
# ---------------------------------------------------------------------------


def walk_structure_tree(
    pdf: pikepdf.Pdf,
) -> Generator[tuple[pikepdf.Dictionary, int, pikepdf.Dictionary | None], None, None]:
    """Yield ``(node, depth, parent)`` for every node in the structure tree.

    Uses an explicit stack instead of recursion to handle large PDFs.
    """
    struct_root = pdf.Root.get("/StructTreeRoot")
    if struct_root is None:
        return

    # Stack entries: (node, depth, parent)
    stack: list[tuple[pikepdf.Dictionary, int, pikepdf.Dictionary | None]] = [
        (struct_root, 0, None)
    ]

    while stack:
        node, depth, parent = stack.pop()
        yield node, depth, parent

        kids = node.get("/K")
        if kids is None:
            continue

        children: list[pikepdf.Object] = []
        if isinstance(kids, pikepdf.Array):
            children = list(kids)
        elif isinstance(kids, pikepdf.Dictionary):
            children = [kids]

        # Push in reverse so left-to-right order is preserved.
        for child in reversed(children):
            resolved = _resolve_pdf_object(child)
            if isinstance(resolved, pikepdf.Dictionary) and "/S" in resolved:
                stack.append((resolved, depth + 1, node))


def _get_struct_type(node: pikepdf.Dictionary) -> str:
    """Return the structure type name as a plain string (e.g. 'Table')."""
    s = node.get("/S")
    if s is None:
        return ""
    return str(s).lstrip("/")


def _node_has_direct_content(node: pikepdf.Dictionary) -> bool:
    """True if the node has marked-content references (integers or MCR dicts)."""
    return _shared_node_has_direct_content(node)


# ---------------------------------------------------------------------------
# Character encoding analysis helpers
# ---------------------------------------------------------------------------


def _decode_pdf_literal_string(data: bytes) -> bytes:
    """Decode a PDF literal string into raw one-byte font codes."""
    decoded = bytearray()
    i = 0
    while i < len(data):
        byte = data[i]
        if byte != 0x5C:
            decoded.append(byte)
            i += 1
            continue

        if i + 1 >= len(data):
            break

        nxt = data[i + 1]
        if nxt in b"nrtbf":
            decoded.append({
                ord("n"): 0x0A,
                ord("r"): 0x0D,
                ord("t"): 0x09,
                ord("b"): 0x08,
                ord("f"): 0x0C,
            }[nxt])
            i += 2
            continue

        if nxt in b"()\\":
            decoded.append(nxt)
            i += 2
            continue

        if 48 <= nxt <= 55:
            octal = [nxt]
            j = i + 2
            while j < len(data) and len(octal) < 3 and 48 <= data[j] <= 55:
                octal.append(data[j])
                j += 1
            decoded.append(int(bytes(octal), 8))
            i = j
            continue

        if nxt in (0x0A, 0x0D):
            i += 2
            if nxt == 0x0D and i < len(data) and data[i] == 0x0A:
                i += 1
            continue

        decoded.append(nxt)
        i += 2

    return bytes(decoded)


def _extract_used_font_codes(page: pikepdf.Page) -> dict[str, set[int]]:
    """Return one-byte glyph codes used by each page font in text operators."""
    contents = page.get("/Contents")
    if contents is None:
        return {}

    streams = list(contents) if isinstance(contents, pikepdf.Array) else [contents]
    data_parts = []
    for stream in streams:
        resolved = _resolve_pdf_object(stream)
        try:
            data_parts.append(resolved.read_bytes())
        except Exception:
            continue

    if not data_parts:
        return {}

    used: dict[str, set[int]] = {}
    token_re = re.compile(
        rb"/([A-Za-z0-9]+)\s+[0-9.]+\s+Tf|\[(.*?)\]\s*TJ|(<[0-9A-Fa-f\s]+>|\((?:[^\\)]|\\.)*\))\s*Tj",
        re.S,
    )
    current_font: str | None = None

    for chunk in re.findall(rb"BT(.*?)ET", b"\n".join(data_parts), re.S):
        for match in token_re.finditer(chunk):
            font_name, tj_array, tj_operand = match.groups()
            if font_name is not None:
                current_font = "/" + font_name.decode("latin-1")
                continue
            if current_font is None:
                continue

            raw_strings: list[bytes] = []
            if tj_array is not None:
                for literal in re.finditer(rb"\(([^\\)]*(?:\\.[^\\)]*)*)\)", tj_array, re.S):
                    raw_strings.append(_decode_pdf_literal_string(literal.group(1)))
                for hex_match in re.finditer(rb"<([0-9A-Fa-f\s]+)>", tj_array):
                    try:
                        raw_strings.append(bytes.fromhex(hex_match.group(1).decode("ascii")))
                    except ValueError:
                        continue
            elif tj_operand is not None:
                if tj_operand.startswith(b"("):
                    raw_strings.append(_decode_pdf_literal_string(tj_operand[1:-1]))
                elif tj_operand.startswith(b"<"):
                    try:
                        raw_strings.append(bytes.fromhex(tj_operand[1:-1].decode("ascii")))
                    except ValueError:
                        continue

            if not raw_strings:
                continue

            font_codes = used.setdefault(current_font, set())
            for raw in raw_strings:
                font_codes.update(raw)

    return used


def _parse_tounicode_mapped_codes(font: pikepdf.Dictionary) -> set[int]:
    """Return source codes explicitly covered by a font's /ToUnicode CMap."""
    tounicode = font.get("/ToUnicode")
    if tounicode is None:
        return set()

    stream = _resolve_pdf_object(tounicode)
    try:
        cmap = stream.read_bytes().decode("latin-1", errors="replace")
    except Exception:
        return set()

    mapped: set[int] = set()
    mode: str | None = None
    for raw_line in cmap.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.endswith("beginbfchar"):
            mode = "bfchar"
            continue
        if line.endswith("beginbfrange"):
            mode = "bfrange"
            continue
        if line in {"endbfchar", "endbfrange"}:
            mode = None
            continue

        if mode == "bfchar":
            for src, _dst in re.findall(r"<([0-9A-Fa-f]{2,4})>\s*<([0-9A-Fa-f]+)>", line):
                mapped.add(int(src, 16))
        elif mode == "bfrange":
            match = re.match(
                r"<([0-9A-Fa-f]{2,4})>\s*<([0-9A-Fa-f]{2,4})>\s*(<[^>]+>|\[)",
                line,
            )
            if match:
                start = int(match.group(1), 16)
                end = int(match.group(2), 16)
                mapped.update(range(start, end + 1))

    return mapped


def _find_suspicious_extracted_text(text: str) -> list[str]:
    """Return human-readable problems found in extracted page text."""
    findings: list[str] = []
    if re.search(r"\b(?:[A-Za-z]{1,}[#•][A-Za-z]{2,}|[A-Za-z]{2,}[#•][A-Za-z]{1,})\b", text):
        findings.append("words contain corrupted inline glyphs")
    common_short_words = {
        "a", "an", "and", "as", "at", "be", "by", "day", "do", "for", "from",
        "go", "he", "if", "in", "is", "it", "may", "of", "on", "or", "our",
        "pay", "so", "the", "to", "up", "us", "we",
    }
    split_word_pattern = re.compile(
        r"\b([A-Za-z]{1,3}) {2,}([A-Za-z]{4,16})\b|\b([A-Za-z]{4,16}) {2,}([A-Za-z]{1,3})\b"
    )
    for match in split_word_pattern.finditer(text):
        left = (match.group(1) or match.group(3) or "").lower()
        right = (match.group(2) or match.group(4) or "").lower()
        short_fragment = left if len(left) <= 4 else right
        if short_fragment in common_short_words:
            continue
        if len(left + right) >= 7:
            findings.append("words are split by repeated spaces")
            break
    return findings


def _sample_page_numbers(page_numbers: list[int], *, limit: int) -> list[int]:
    """Sample page numbers evenly across a document-sized page list."""
    if limit <= 0 or not page_numbers:
        return []
    if len(page_numbers) <= limit:
        return list(page_numbers)
    if limit == 1:
        return [page_numbers[0]]

    sampled: list[int] = []
    last_index = -1
    for i in range(limit):
        index = round(i * (len(page_numbers) - 1) / (limit - 1))
        if index == last_index:
            continue
        sampled.append(page_numbers[index])
        last_index = index
    return sampled


def _extracted_text_looks_trustworthy(text: str) -> bool:
    """Heuristic gate for whether extracted text is good enough for AT use."""
    normalized = " ".join(text.split())
    if not normalized:
        return False
    if _find_suspicious_extracted_text(text):
        return False
    if "\ufffd" in text:
        return False

    control_chars = re.findall(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", text)
    if len(control_chars) > max(2, len(normalized) // 1000):
        return False

    alnum = sum(ch.isalnum() for ch in normalized)
    letters = sum(ch.isalpha() for ch in normalized)
    word_like = re.findall(r"[A-Za-z]{3,}", normalized)
    if len(normalized) >= 80 and (alnum == 0 or letters / max(alnum, 1) < 0.45):
        return False
    if len(normalized) >= 80 and len(word_like) < 6:
        return False
    return True


def _analyze_character_encoding(
    pdf: pikepdf.Pdf,
    pdf_path: Path | str | None,
    *,
    max_pages: int = 10,
) -> _CharacterEncodingAnalysis:
    """Inspect whether the text layer is complete enough for assistive tech."""
    analysis = _CharacterEncodingAnalysis()
    provisional_unmapped: list[tuple[int, str]] = []

    for page_number, page in enumerate(pdf.pages, 1):
        used_font_codes = _extract_used_font_codes(page)
        resources = page.get("/Resources")
        fonts = resources.get("/Font") if resources is not None else None
        if fonts is None:
            continue

        for font_name, font_ref in fonts.items():
            font = _resolve_pdf_object(font_ref)
            if not isinstance(font, pikepdf.Dictionary):
                continue

            has_tounicode = font.get("/ToUnicode") is not None
            has_encoding = font.get("/Encoding") is not None
            if not has_tounicode and not has_encoding:
                analysis.details.append(
                    f"Page {page_number}: {font_name} missing /ToUnicode and /Encoding"
                )
                analysis.page_numbers.add(page_number)
                continue

            if not has_tounicode:
                continue

            mapped_codes = _parse_tounicode_mapped_codes(font)
            if not mapped_codes:
                continue

            used_codes = used_font_codes.get(str(font_name), set())
            unmapped_codes = sorted(
                code for code in used_codes
                if code not in mapped_codes and code not in {0x20, 0x28, 0x29, 0x5C}
            )
            if unmapped_codes:
                preview = ", ".join(f"0x{code:02X}" for code in unmapped_codes[:6])
                provisional_unmapped.append(
                    (
                        page_number,
                        f"Page {page_number}: {font_name} uses unmapped glyph codes in /ToUnicode ({preview})",
                    )
                )

    trusted_pages: set[int] = set()
    if pdf_path is not None:
        try:
            import fitz

            doc = fitz.open(str(pdf_path))
            try:
                unique_unmapped_pages = sorted({page for page, _detail in provisional_unmapped})
                sampled_unmapped_pages = set(
                    _sample_page_numbers(unique_unmapped_pages, limit=max_pages * 3)
                )
                candidate_pages = set(range(1, min(max_pages, len(doc)) + 1))
                candidate_pages.update(sampled_unmapped_pages)
                for page_number in sorted(page for page in candidate_pages if 1 <= page <= len(doc)):
                    page_idx = page_number - 1
                    text = doc[page_idx].get_text("text")[:5000]
                    if not text.strip():
                        continue
                    if _extracted_text_looks_trustworthy(text):
                        trusted_pages.add(page_number)
                        continue
                    findings = _find_suspicious_extracted_text(text)
                    if findings or not _extracted_text_looks_trustworthy(text):
                        analysis.details.append(
                            f"Page {page_idx + 1}: suspicious extracted text ({'; '.join(findings) or 'text extraction is low quality'})"
                        )
                        analysis.page_numbers.add(page_idx + 1)
                        analysis.requires_rebuild = True

                if sampled_unmapped_pages and sampled_unmapped_pages.issubset(trusted_pages):
                    trusted_pages.update(unique_unmapped_pages)
            finally:
                doc.close()
        except Exception:
            pass

    for page_number, detail in provisional_unmapped:
        if page_number in trusted_pages:
            continue
        analysis.details.append(detail)
        analysis.page_numbers.add(page_number)
        analysis.requires_rebuild = True

    return analysis


# ---------------------------------------------------------------------------
# WCAG criterion mapping for each rule_id
# ---------------------------------------------------------------------------

_WCAG_CRITERIA: dict[str, str] = {
    "doc-accessibility-permission": "WCAG 4.1.1",
    "doc-not-image-only": "WCAG 1.1.1",
    "doc-tagged": "WCAG 1.3.1",
    "doc-reading-order": "WCAG 1.3.2",
    "doc-language": "WCAG 3.1.1",
    "doc-display-title": "WCAG 2.4.2",
    "doc-bookmarks": "WCAG 2.4.5",
    "doc-color-contrast": "WCAG 1.4.3",
    "page-content-tagged": "WCAG 1.3.1",
    "page-annotations-tagged": "WCAG 1.3.1",
    "page-tab-order": "WCAG 2.4.3",
    "page-char-encoding": "WCAG 4.1.1",
    "page-multimedia-tagged": "WCAG 1.2.1",
    "page-no-flicker": "WCAG 2.3.1",
    "page-no-scripts": "WCAG 4.1.2",
    "page-no-repetitive-links": "WCAG 3.2.3",
    "page-no-timed-responses": "WCAG 2.2.1",
    "forms-fields-tagged": "WCAG 1.3.1",
    "forms-fields-description": "WCAG 1.3.1",
    "tables-tr-parent": "WCAG 1.3.1",
    "tables-th-td-parent": "WCAG 1.3.1",
    "tables-headers": "WCAG 1.3.1",
    "tables-regularity": "WCAG 1.3.1",
    "tables-summary": "WCAG 1.3.1",
    "lists-li-parent": "WCAG 1.3.1",
    "lists-lbl-lbody-parent": "WCAG 1.3.1",
    "alt-figures": "WCAG 1.1.1",
    "alt-redundant": "WCAG 1.1.1",
    "alt-associated": "WCAG 1.1.1",
    "alt-hides-annotation": "WCAG 1.1.1",
    "alt-elements": "WCAG 1.1.1",
    "headings-nesting": "WCAG 1.3.1",
}

# Maps old category names to our snake_case keys.
_CATEGORY_MAP = {
    "Document": "document",
    "Page Content": "page_content",
    "Forms Tables Lists": "forms_tables_lists",
    "Alt Text Headings": "alt_text_headings",
}


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _format_page_ranges(pages: list[int]) -> str:
    """Format ``[1,2,3,5,7,8,9]`` as ``'1-3, 5, 7-9'``."""
    if not pages:
        return ""
    pages = sorted(set(pages))
    ranges: list[str] = []
    start = pages[0]
    end = pages[0]
    for p in pages[1:]:
        if p == end + 1:
            end = p
        else:
            ranges.append(f"{start}-{end}" if start != end else str(start))
            start = end = p
    ranges.append(f"{start}-{end}" if start != end else str(start))
    return ", ".join(ranges)


def _result(
    rule_id: str,
    category: str,
    status: CheckStatus,
    message: str,
    fixable: bool = False,
) -> CheckResult:
    """Shorthand constructor that fills in ``wcag_criterion`` automatically."""
    return CheckResult(
        rule_id=rule_id,
        category=_CATEGORY_MAP.get(category, category),
        status=status,
        message=message,
        wcag_criterion=_WCAG_CRITERIA.get(rule_id, ""),
        fixable=fixable,
    )


# ---------------------------------------------------------------------------
# Checker class
# ---------------------------------------------------------------------------


class PDFAccessibilityChecker:
    """Run all 32 Adobe-equivalent accessibility checks on a PDF.

    Parameters
    ----------
    pdf_path:
        Path to the PDF file (string or Path).
    """

    def __init__(self, pdf_path: str | Path) -> None:
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF not found: {pdf_path}")
        self._path = str(pdf_path)
        self._pdf = pikepdf.open(pdf_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_all(self, file_id: str, filename: str) -> CheckReport:
        """Run all 32 checks and return a full report."""
        results: list[CheckResult] = []
        for check_method in self._all_checks():
            try:
                results.append(check_method())
            except Exception as e:
                results.append(CheckResult(
                    rule_id=check_method.__name__.replace("_check_", ""),
                    category="document",
                    status=CheckStatus.ERROR,
                    message=f"Check failed: {e}",
                ))
        return CheckReport(file_id=file_id, filename=filename, results=results)

    def close(self) -> None:
        """Close the underlying pikepdf handle."""
        try:
            self._pdf.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ------------------------------------------------------------------
    # Check registry
    # ------------------------------------------------------------------

    def _all_checks(self):
        """Return all 32 check methods in order."""
        return [
            # Category 1 — Document (8)
            self._check_accessibility_permission,
            self._check_not_image_only,
            self._check_tagged,
            self._check_logical_reading_order,
            self._check_language,
            self._check_display_doc_title,
            self._check_bookmarks,
            self._check_color_contrast,
            # Category 2 — Page Content (9)
            self._check_all_content_tagged,
            self._check_annotations_tagged,
            self._check_tab_order,
            self._check_character_encoding,
            self._check_multimedia_tagged,
            self._check_screen_flicker,
            self._check_no_scripts,
            self._check_no_repetitive_links,
            self._check_no_timed_responses,
            # Category 3 — Forms, Tables, Lists (9)
            self._check_form_fields_tagged,
            self._check_form_fields_description,
            self._check_tr_parent,
            self._check_th_td_parent,
            self._check_table_headers,
            self._check_table_regularity,
            self._check_table_summary,
            self._check_li_parent,
            self._check_lbl_lbody_parent,
            # Category 4 — Alt Text & Headings (6)
            self._check_figures_alt_text,
            self._check_redundant_alt_text,
            self._check_alt_associated_content,
            self._check_alt_hides_annotation,
            self._check_elements_alt_text,
            self._check_heading_nesting,
        ]

    # -----------------------------------------------------------------------
    # Category 1: Document (8 checks)
    # -----------------------------------------------------------------------

    def _check_accessibility_permission(self) -> CheckResult:
        """Check #1: Accessibility permission flag is set."""
        pdf = self._pdf
        if not pdf.is_encrypted:
            return _result("doc-accessibility-permission", "Document", CheckStatus.PASS,
                           "Accessibility permission flag is set")

        try:
            perms = pdf.allow
            if perms.extract or perms.accessibility:
                return _result("doc-accessibility-permission", "Document", CheckStatus.PASS,
                               "Accessibility permission flag is set")
        except Exception:
            pass

        return _result("doc-accessibility-permission", "Document", CheckStatus.FAIL,
                        "Encryption restricts assistive technology access", fixable=True)

    def _check_not_image_only(self) -> CheckResult:
        """Check #2: Document is not image-only PDF."""
        pdf = self._pdf
        pages_with_text = 0
        image_only_pages = []

        for i, page in enumerate(pdf.pages, 1):
            contents = page.get("/Contents")
            if contents is None:
                image_only_pages.append(i)
                continue

            raw = b""
            if isinstance(contents, pikepdf.Array):
                for stream in contents:
                    try:
                        raw += stream.read_bytes()
                    except Exception:
                        pass
            else:
                try:
                    raw = contents.read_bytes()
                except Exception:
                    pass

            text = raw.decode("latin-1", errors="replace")
            if re.search(r"\b(Tj|TJ|'|\")\b", text):
                pages_with_text += 1
            else:
                image_only_pages.append(i)

        if not image_only_pages:
            return _result("doc-not-image-only", "Document", CheckStatus.PASS,
                           "Document is not image-only PDF")

        if pages_with_text == 0:
            return _result("doc-not-image-only", "Document", CheckStatus.FAIL,
                           "Document appears to be image-only (no text operators found)")

        return _result("doc-not-image-only", "Document", CheckStatus.PASS,
                        f"Pages without text operators: {_format_page_ranges(image_only_pages)}")

    def _check_tagged(self) -> CheckResult:
        """Check #3: Document is tagged PDF."""
        pdf = self._pdf
        mark_info = pdf.Root.get("/MarkInfo")
        if mark_info and bool(mark_info.get("/Marked")):
            return _result("doc-tagged", "Document", CheckStatus.PASS,
                           "Document is tagged PDF")
        return _result("doc-tagged", "Document", CheckStatus.FAIL,
                        "/MarkInfo/Marked is not true", fixable=True)

    def _check_logical_reading_order(self) -> CheckResult:
        """Check #4: Document structure provides logical reading order."""
        pdf = self._pdf
        struct_root = pdf.Root.get("/StructTreeRoot")
        if struct_root is None:
            return _result("doc-reading-order", "Document", CheckStatus.FAIL,
                           "No /StructTreeRoot found")

        kids = struct_root.get("/K")
        if kids is None:
            return _result("doc-reading-order", "Document", CheckStatus.FAIL,
                           "/StructTreeRoot has no children")

        return _result("doc-reading-order", "Document", CheckStatus.NOT_APPLICABLE,
                        "Structure tree exists — verify reading order is logical")

    def _check_language(self) -> CheckResult:
        """Check #5: Text language is specified."""
        pdf = self._pdf
        lang = pdf.Root.get("/Lang")
        if lang and str(lang).strip():
            return _result("doc-language", "Document", CheckStatus.PASS,
                           f"Language: {lang}")
        return _result("doc-language", "Document", CheckStatus.FAIL,
                        "No /Lang set on document catalog", fixable=True)

    def _check_display_doc_title(self) -> CheckResult:
        """Check #6: Document title is showing in title bar."""
        pdf = self._pdf
        vp = pdf.Root.get("/ViewerPreferences")
        display = False
        if vp:
            display = bool(vp.get("/DisplayDocTitle"))

        has_title = False
        try:
            with pdf.open_metadata() as meta:
                title = meta.get("dc:title", "")
                has_title = bool(title and str(title).strip())
        except Exception:
            pass

        if display and has_title:
            return _result("doc-display-title", "Document", CheckStatus.PASS,
                           "Document title is showing in title bar")

        details = []
        if not display:
            details.append("/ViewerPreferences/DisplayDocTitle is not true")
        if not has_title:
            details.append("dc:title is empty or missing")

        return _result("doc-display-title", "Document", CheckStatus.FAIL,
                        "; ".join(details), fixable=True)

    def _check_bookmarks(self) -> CheckResult:
        """Check #7: Bookmarks are present in large documents."""
        pdf = self._pdf
        page_count = len(pdf.pages)
        if not document_requires_bookmarks(pdf):
            return _result("doc-bookmarks", "Document", CheckStatus.PASS,
                           f"Document has {page_count} pages (<=20, bookmarks not required)")

        if document_has_bookmarks(pdf):
            return _result("doc-bookmarks", "Document", CheckStatus.PASS,
                           "Bookmarks are present")

        return _result("doc-bookmarks", "Document", CheckStatus.FAIL,
                        f"Document has {page_count} pages but no bookmarks (/Outlines)", fixable=True)

    def _check_color_contrast(self) -> CheckResult:
        """Check #8: Document has appropriate color contrast."""
        return _result("doc-color-contrast", "Document", CheckStatus.NOT_APPLICABLE,
                        "Color contrast analysis requires visual inspection")

    # -----------------------------------------------------------------------
    # Category 2: Page Content (9 checks)
    # -----------------------------------------------------------------------

    def _check_all_content_tagged(self) -> CheckResult:
        """Check #9: All page content is tagged."""
        pdf = self._pdf
        untagged_pages = []

        for i, page in enumerate(pdf.pages, 1):
            contents = page.get("/Contents")
            if contents is None:
                continue

            raw = b""
            if isinstance(contents, pikepdf.Array):
                for stream in contents:
                    try:
                        raw += stream.read_bytes()
                    except Exception:
                        pass
            else:
                try:
                    raw = contents.read_bytes()
                except Exception:
                    pass

            text = raw.decode("latin-1", errors="replace")

            first_marked = re.search(r"/\w+\s*(<<.*?>>)?\s*(BDC|BMC)", text)
            if first_marked:
                before = text[: first_marked.start()].strip()
                if before:
                    untagged_pages.append(i)
            else:
                if text.strip():
                    untagged_pages.append(i)

        if not untagged_pages:
            return _result("page-content-tagged", "Page Content", CheckStatus.PASS,
                           "All page content is tagged")

        return _result("page-content-tagged", "Page Content", CheckStatus.FAIL,
                        f"Pages with untagged content: {_format_page_ranges(untagged_pages)}",
                        fixable=True)

    def _check_annotations_tagged(self) -> CheckResult:
        """Check #10: All annotations are tagged."""
        pdf = self._pdf
        untagged = []

        struct_annot_objgens: set[tuple[int, int]] = set()
        for node, _depth, _parent in walk_structure_tree(pdf):
            kids = node.get("/K")
            if kids is None:
                continue
            items = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
            for item in items:
                resolved = _resolve_pdf_object(item)
                if isinstance(resolved, pikepdf.Dictionary):
                    obj_ref = resolved.get("/Obj")
                    if obj_ref is not None:
                        try:
                            if hasattr(obj_ref, "objgen"):
                                struct_annot_objgens.add(obj_ref.objgen)
                        except Exception:
                            pass

        has_link_elements = False
        for node, _depth, _parent in walk_structure_tree(pdf):
            stype = _get_struct_type(node)
            if stype in ("Link", "Annot", "Form", "Reference"):
                has_link_elements = True
                break

        for i, page in enumerate(pdf.pages, 1):
            annots = page.get("/Annots")
            if not annots:
                continue
            for annot_ref in annots:
                annot = _resolve_pdf_object(annot_ref)
                matched = False
                if hasattr(annot_ref, "objgen"):
                    matched = annot_ref.objgen in struct_annot_objgens
                if not matched and has_link_elements:
                    matched = True
                if not matched:
                    subtype = str(annot.get("/Subtype", "unknown"))
                    untagged.append(f"Page {i}: {subtype} annotation not in structure tree")

        if not untagged:
            return _result("page-annotations-tagged", "Page Content", CheckStatus.PASS,
                           "All annotations are tagged")

        return _result("page-annotations-tagged", "Page Content", CheckStatus.FAIL,
                        "; ".join(untagged[:5]), fixable=True)

    def _check_tab_order(self) -> CheckResult:
        """Check #11: Tab order is consistent with structure order."""
        pdf = self._pdf
        bad_pages = []
        for i, page in enumerate(pdf.pages, 1):
            tabs = page.get("/Tabs")
            if tabs is None or str(tabs) != "/S":
                bad_pages.append(i)

        if not bad_pages:
            return _result("page-tab-order", "Page Content", CheckStatus.PASS,
                           "Tab order is consistent with structure order")

        return _result("page-tab-order", "Page Content", CheckStatus.FAIL,
                        f"Pages without /Tabs = /S: {_format_page_ranges(bad_pages)}",
                        fixable=True)

    def _check_character_encoding(self) -> CheckResult:
        """Check #12: Reliable character encoding is provided."""
        pdf = self._pdf
        analysis = _analyze_character_encoding(pdf, self._path)
        if not analysis.details:
            return _result("page-char-encoding", "Page Content", CheckStatus.PASS,
                           "Reliable character encoding is provided")

        return _result("page-char-encoding", "Page Content", CheckStatus.FAIL,
                        "; ".join(analysis.details[:5]), fixable=True)

    def _check_multimedia_tagged(self) -> CheckResult:
        """Check #13: All multimedia objects are tagged."""
        pdf = self._pdf
        page_tagged_multimedia: dict[int, int] = {}
        for node, _depth, _parent in walk_structure_tree(pdf):
            stype = _get_struct_type(node)
            if stype not in ("Figure", "Form"):
                continue
            if not node_has_content_association(node):
                continue
            page_idx = find_node_page(node, pdf)
            if page_idx is None:
                continue
            page_tagged_multimedia[page_idx] = page_tagged_multimedia.get(page_idx, 0) + 1

        page_failures = []
        found_multimedia = False
        for page_idx, page in enumerate(pdf.pages, 1):
            rendered = get_rendered_multimedia_names(page)
            if not rendered:
                continue
            found_multimedia = True
            if page_tagged_multimedia.get(page_idx - 1, 0) == 0:
                page_failures.append(
                    f"Page {page_idx}: rendered multimedia ({', '.join(sorted(rendered))}) has no associated /Figure or /Form tag"
                )

        if not found_multimedia:
            return _result("page-multimedia-tagged", "Page Content", CheckStatus.PASS,
                           "No multimedia objects found")

        if not page_failures:
            return _result("page-multimedia-tagged", "Page Content", CheckStatus.PASS,
                           "All multimedia objects are tagged")

        return _result("page-multimedia-tagged", "Page Content", CheckStatus.FAIL,
                        "; ".join(page_failures[:5]), fixable=True)

    def _check_screen_flicker(self) -> CheckResult:
        """Check #14: Page will not cause screen flicker."""
        pdf = self._pdf
        flicker_pages = []
        for i, page in enumerate(pdf.pages, 1):
            annots = page.get("/Annots")
            if not annots:
                continue
            for annot_ref in annots:
                annot = _resolve_pdf_object(annot_ref)
                subtype = str(annot.get("/Subtype", ""))
                if subtype in ("/Screen", "/Movie"):
                    flicker_pages.append(i)
                    break

        if not flicker_pages:
            return _result("page-no-flicker", "Page Content", CheckStatus.PASS,
                           "Page will not cause screen flicker")

        return _result("page-no-flicker", "Page Content", CheckStatus.FAIL,
                        f"Pages with animation/media: {_format_page_ranges(flicker_pages)}",
                        fixable=True)

    def _check_no_scripts(self) -> CheckResult:
        """Check #15: No inaccessible scripts."""
        pdf = self._pdf
        has_js = False
        details = []

        names = pdf.Root.get("/Names")
        if names:
            js_names = names.get("/JavaScript")
            if js_names:
                has_js = True
                details.append("Document-level /JavaScript in /Names")

        aa = pdf.Root.get("/AA")
        if aa:
            has_js = True
            details.append("Document-level additional actions (/AA)")

        for i, page in enumerate(pdf.pages, 1):
            page_aa = page.get("/AA")
            if page_aa:
                has_js = True
                details.append(f"Page {i}: additional actions (/AA)")
            annots = page.get("/Annots")
            if annots:
                for annot_ref in annots:
                    annot = _resolve_pdf_object(annot_ref)
                    action = annot.get("/A")
                    if action:
                        atype = str(action.get("/S", ""))
                        if atype in ("/JavaScript", "/JS"):
                            has_js = True
                            details.append(f"Page {i}: JavaScript action in annotation")

        if not has_js:
            return _result("page-no-scripts", "Page Content", CheckStatus.PASS,
                           "No inaccessible scripts")

        return _result("page-no-scripts", "Page Content", CheckStatus.FAIL,
                        "; ".join(details[:5]), fixable=True)

    def _check_no_repetitive_links(self) -> CheckResult:
        """Check #16: Navigation links are not repetitive."""
        pdf = self._pdf
        link_uris: dict[str, list[int]] = {}

        for i, page in enumerate(pdf.pages, 1):
            annots = page.get("/Annots")
            if not annots:
                continue
            for annot_ref in annots:
                annot = _resolve_pdf_object(annot_ref)
                if str(annot.get("/Subtype", "")) != "/Link":
                    continue
                action = annot.get("/A")
                if action:
                    uri = str(action.get("/URI", ""))
                    if uri:
                        link_uris.setdefault(uri, []).append(i)

        repetitive = {
            uri: pages for uri, pages in link_uris.items() if len(pages) > 3
        }

        if not repetitive:
            return _result("page-no-repetitive-links", "Page Content", CheckStatus.PASS,
                           "Navigation links are not repetitive")

        details = [
            f"{uri[:60]} appears on {len(pages)} pages"
            for uri, pages in list(repetitive.items())[:10]
        ]
        return _result("page-no-repetitive-links", "Page Content", CheckStatus.NOT_APPLICABLE,
                        "; ".join(details))

    def _check_no_timed_responses(self) -> CheckResult:
        """Check #17: Page does not require timed responses."""
        pdf = self._pdf
        has_timed = False
        details = []

        for i, page in enumerate(pdf.pages, 1):
            aa = page.get("/AA")
            if aa:
                if aa.get("/O") or aa.get("/C"):
                    has_timed = True
                    details.append(f"Page {i}: open/close actions found")

        if not has_timed:
            return _result("page-no-timed-responses", "Page Content", CheckStatus.PASS,
                           "Page does not require timed responses")

        return _result("page-no-timed-responses", "Page Content", CheckStatus.FAIL,
                        "; ".join(details[:5]), fixable=True)

    # -----------------------------------------------------------------------
    # Category 3: Forms, Tables and Lists (9 checks)
    # -----------------------------------------------------------------------

    def _check_form_fields_tagged(self) -> CheckResult:
        """Check #18: All form fields are tagged."""
        pdf = self._pdf
        widgets = []
        for i, page in enumerate(pdf.pages, 1):
            annots = page.get("/Annots")
            if not annots:
                continue
            for annot_ref in annots:
                annot = _resolve_pdf_object(annot_ref)
                if str(annot.get("/Subtype", "")) == "/Widget":
                    widgets.append((i, annot))

        if not widgets:
            return _result("forms-fields-tagged", "Forms Tables Lists", CheckStatus.PASS,
                           "No form fields found")

        form_elements = 0
        for node, _depth, _parent in walk_structure_tree(pdf):
            if _get_struct_type(node) == "Form":
                form_elements += 1

        if form_elements >= len(widgets):
            return _result("forms-fields-tagged", "Forms Tables Lists", CheckStatus.PASS,
                           "All form fields are tagged")

        return _result("forms-fields-tagged", "Forms Tables Lists", CheckStatus.FAIL,
                        f"{len(widgets)} widget annotations, {form_elements} /Form elements in structure tree",
                        fixable=True)

    def _check_form_fields_description(self) -> CheckResult:
        """Check #19: All form fields have description."""
        pdf = self._pdf
        missing_tu = []

        acroform = pdf.Root.get("/AcroForm")
        if acroform:
            fields = acroform.get("/Fields")
            if fields:
                for field_ref in fields:
                    fld = _resolve_pdf_object(field_ref)
                    if not isinstance(fld, pikepdf.Dictionary):
                        continue
                    tu = fld.get("/TU")
                    if tu is None or not str(tu).strip():
                        name = str(fld.get("/T", "unnamed"))
                        missing_tu.append(name)

        for i, page in enumerate(pdf.pages, 1):
            annots = page.get("/Annots")
            if not annots:
                continue
            for annot_ref in annots:
                annot = _resolve_pdf_object(annot_ref)
                if str(annot.get("/Subtype", "")) != "/Widget":
                    continue
                tu = annot.get("/TU")
                t = str(annot.get("/T", ""))
                if tu is None or not str(tu).strip():
                    if t and t not in missing_tu:
                        missing_tu.append(t)

        if not missing_tu:
            return _result("forms-fields-description", "Forms Tables Lists", CheckStatus.PASS,
                           "All form fields have description")

        return _result("forms-fields-description", "Forms Tables Lists", CheckStatus.FAIL,
                        f"Fields missing /TU: {', '.join(missing_tu[:15])}", fixable=True)

    def _check_tr_parent(self) -> CheckResult:
        """Check #20: TR must be child of Table/THead/TBody/TFoot."""
        pdf = self._pdf
        valid_parents = {"Table", "THead", "TBody", "TFoot"}
        bad = []
        for node, _depth, parent in walk_structure_tree(pdf):
            if _get_struct_type(node) == "TR" and parent is not None:
                parent_type = _get_struct_type(parent)
                if parent_type not in valid_parents:
                    bad.append(f"TR has parent {parent_type or 'unknown'}")

        if not bad:
            return _result("tables-tr-parent", "Forms Tables Lists", CheckStatus.PASS,
                           "TR is child of Table/THead/TBody/TFoot")

        return _result("tables-tr-parent", "Forms Tables Lists", CheckStatus.FAIL,
                        "; ".join(bad[:10]), fixable=True)

    def _check_th_td_parent(self) -> CheckResult:
        """Check #21: TH and TD must be children of TR."""
        pdf = self._pdf
        bad = []
        for node, _depth, parent in walk_structure_tree(pdf):
            stype = _get_struct_type(node)
            if stype in ("TH", "TD") and parent is not None:
                parent_type = _get_struct_type(parent)
                if parent_type != "TR":
                    bad.append(f"{stype} has parent {parent_type or 'unknown'}")

        if not bad:
            return _result("tables-th-td-parent", "Forms Tables Lists", CheckStatus.PASS,
                           "TH and TD are children of TR")

        return _result("tables-th-td-parent", "Forms Tables Lists", CheckStatus.FAIL,
                        "; ".join(bad[:10]), fixable=True)

    def _check_table_headers(self) -> CheckResult:
        """Check #22: Tables must have headers."""
        pdf = self._pdf
        tables_without_headers = 0
        tables_total = 0

        for node, _depth, _parent in walk_structure_tree(pdf):
            if _get_struct_type(node) != "Table":
                continue
            tables_total += 1

            has_th = False
            kids = node.get("/K")
            if kids is None:
                tables_without_headers += 1
                continue

            sub_stack = [node]
            while sub_stack and not has_th:
                current = sub_stack.pop()
                k = current.get("/K")
                if k is None:
                    continue
                items = list(k) if isinstance(k, pikepdf.Array) else [k]
                for item in items:
                    resolved = _resolve_pdf_object(item)
                    if isinstance(resolved, pikepdf.Dictionary) and "/S" in resolved:
                        if _get_struct_type(resolved) == "TH":
                            has_th = True
                            break
                        sub_stack.append(resolved)

            if not has_th:
                tables_without_headers += 1

        if tables_total == 0:
            return _result("tables-headers", "Forms Tables Lists", CheckStatus.PASS,
                           "No tables found")

        if tables_without_headers == 0:
            return _result("tables-headers", "Forms Tables Lists", CheckStatus.PASS,
                           "Tables have headers")

        return _result("tables-headers", "Forms Tables Lists", CheckStatus.FAIL,
                        f"{tables_without_headers}/{tables_total} tables lack /TH elements",
                        fixable=True)

    def _check_table_regularity(self) -> CheckResult:
        """Check #23: Tables — same cols per row, same rows per col."""
        pdf = self._pdf
        irregular = []

        for node, _depth, _parent in walk_structure_tree(pdf):
            if _get_struct_type(node) != "Table":
                continue

            row_lengths: list[int] = []
            sub_stack = [node]
            while sub_stack:
                current = sub_stack.pop()
                k = current.get("/K")
                if k is None:
                    continue
                items = list(k) if isinstance(k, pikepdf.Array) else [k]
                for item in items:
                    resolved = _resolve_pdf_object(item)
                    if not isinstance(resolved, pikepdf.Dictionary) or "/S" not in resolved:
                        continue
                    stype = _get_struct_type(resolved)
                    if stype == "TR":
                        tr_k = resolved.get("/K")
                        if tr_k is None:
                            row_lengths.append(0)
                        elif isinstance(tr_k, pikepdf.Array):
                            cells = sum(
                                1 for c in tr_k
                                if isinstance(
                                    _resolve_pdf_object(c),
                                    pikepdf.Dictionary,
                                )
                                and _get_struct_type(
                                    _resolve_pdf_object(c)
                                )
                                in ("TH", "TD")
                            )
                            row_lengths.append(cells)
                        else:
                            row_lengths.append(1)
                    elif stype in ("THead", "TBody", "TFoot"):
                        sub_stack.append(resolved)

            if row_lengths and len(set(row_lengths)) > 1:
                irregular.append(
                    f"Table has rows with {min(row_lengths)}-{max(row_lengths)} columns"
                )

        if not irregular:
            return _result("tables-regularity", "Forms Tables Lists", CheckStatus.PASS,
                           "Tables have consistent column counts")

        return _result("tables-regularity", "Forms Tables Lists", CheckStatus.NOT_APPLICABLE,
                        "; ".join(irregular[:5]))

    def _check_table_summary(self) -> CheckResult:
        """Check #24: Tables must have a summary."""
        pdf = self._pdf
        tables_without_summary = 0
        tables_total = 0

        for node, _depth, _parent in walk_structure_tree(pdf):
            if _get_struct_type(node) != "Table":
                continue
            tables_total += 1
            alt = node.get("/Alt")
            summary = node.get("/Summary")
            if alt is None and summary is None:
                tables_without_summary += 1

        if tables_total == 0:
            return _result("tables-summary", "Forms Tables Lists", CheckStatus.PASS,
                           "No tables found")

        if tables_without_summary == 0:
            return _result("tables-summary", "Forms Tables Lists", CheckStatus.PASS,
                           "Tables have summaries")

        return _result("tables-summary", "Forms Tables Lists", CheckStatus.FAIL,
                        f"{tables_without_summary}/{tables_total} tables missing /Alt or /Summary",
                        fixable=True)

    def _check_li_parent(self) -> CheckResult:
        """Check #25: LI must be child of L."""
        pdf = self._pdf
        bad = []
        for node, _depth, parent in walk_structure_tree(pdf):
            if _get_struct_type(node) == "LI" and parent is not None:
                parent_type = _get_struct_type(parent)
                if parent_type != "L":
                    bad.append(f"LI has parent {parent_type or 'unknown'}")

        if not bad:
            return _result("lists-li-parent", "Forms Tables Lists", CheckStatus.PASS,
                           "LI is child of L")

        return _result("lists-li-parent", "Forms Tables Lists", CheckStatus.FAIL,
                        "; ".join(bad[:10]), fixable=True)

    def _check_lbl_lbody_parent(self) -> CheckResult:
        """Check #26: Lbl and LBody must be children of LI."""
        pdf = self._pdf
        bad = []
        for node, _depth, parent in walk_structure_tree(pdf):
            stype = _get_struct_type(node)
            if stype in ("Lbl", "LBody") and parent is not None:
                parent_type = _get_struct_type(parent)
                if parent_type != "LI":
                    bad.append(f"{stype} has parent {parent_type or 'unknown'}")

        if not bad:
            return _result("lists-lbl-lbody-parent", "Forms Tables Lists", CheckStatus.PASS,
                           "Lbl and LBody are children of LI")

        return _result("lists-lbl-lbody-parent", "Forms Tables Lists", CheckStatus.FAIL,
                        "; ".join(bad[:10]), fixable=True)

    # -----------------------------------------------------------------------
    # Category 4: Alternate Text and Headings (6 checks)
    # -----------------------------------------------------------------------

    def _check_figures_alt_text(self) -> CheckResult:
        """Check #27: Figures require alternate text."""
        pdf = self._pdf
        figures_without_alt = 0
        figures_total = 0

        for node, _depth, _parent in walk_structure_tree(pdf):
            if _get_struct_type(node) != "Figure":
                continue
            figures_total += 1
            alt = node.get("/Alt")
            if alt is None or not str(alt).strip():
                figures_without_alt += 1

        if figures_total == 0:
            return _result("alt-figures", "Alt Text Headings", CheckStatus.PASS,
                           "No figures found")

        if figures_without_alt == 0:
            return _result("alt-figures", "Alt Text Headings", CheckStatus.PASS,
                           "All figures have alternate text")

        return _result("alt-figures", "Alt Text Headings", CheckStatus.FAIL,
                        f"{figures_without_alt}/{figures_total} figures missing /Alt",
                        fixable=True)

    def _check_redundant_alt_text(self) -> CheckResult:
        """Check #28: Alternate text that will never be read."""
        pdf = self._pdf
        redundant = []

        _SKIP_TYPES = {
            "Figure", "Table", "Form", "Formula", "Link",
            "Annot", "Reference", "Note", "BibEntry",
        }

        for node, _depth, _parent in walk_structure_tree(pdf):
            alt = node.get("/Alt")
            if alt is None:
                continue

            stype = _get_struct_type(node)
            if stype in _SKIP_TYPES:
                continue

            kids = node.get("/K")
            if kids is None:
                continue

            items = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
            all_children_tagged = True
            has_struct_children = False

            for item in items:
                resolved = _resolve_pdf_object(item)
                if isinstance(resolved, pikepdf.Dictionary) and "/S" in resolved:
                    has_struct_children = True
                else:
                    all_children_tagged = False

            if has_struct_children and all_children_tagged:
                redundant.append(
                    f"/{stype} has /Alt but all children are tagged (alt never read)"
                )

        if not redundant:
            return _result("alt-redundant", "Alt Text Headings", CheckStatus.PASS,
                           "No redundant alternate text found")

        return _result("alt-redundant", "Alt Text Headings", CheckStatus.FAIL,
                        "; ".join(redundant[:5]), fixable=True)

    def _check_alt_associated_content(self) -> CheckResult:
        """Check #29: Alternate text must be associated with content."""
        pdf = self._pdf
        orphan_alt = []

        for node, _depth, _parent in walk_structure_tree(pdf):
            alt = node.get("/Alt")
            if alt is None:
                continue
            if not node_has_content_association(node):
                stype = _get_struct_type(node)
                orphan_alt.append(f"/{stype} has /Alt but no associated content")

        if not orphan_alt:
            return _result("alt-associated", "Alt Text Headings", CheckStatus.PASS,
                           "All alternate text is associated with content")

        return _result("alt-associated", "Alt Text Headings", CheckStatus.FAIL,
                        "; ".join(orphan_alt[:5]), fixable=True)

    def _check_alt_hides_annotation(self) -> CheckResult:
        """Check #30: Alternate text should not hide annotation."""
        pdf = self._pdf
        issues = []

        _SKIP_TYPES = {"Link", "Reference", "Annot", "Form"}

        for node, _depth, _parent in walk_structure_tree(pdf):
            alt = node.get("/Alt")
            if alt is None:
                continue

            stype = _get_struct_type(node)
            if stype in _SKIP_TYPES:
                continue

            if node_has_annotation_ref(node):
                issues.append(f"/{stype} has /Alt that hides annotation content")

        if not issues:
            return _result("alt-hides-annotation", "Alt Text Headings", CheckStatus.PASS,
                           "No alternate text hides annotations")

        return _result("alt-hides-annotation", "Alt Text Headings", CheckStatus.FAIL,
                        "; ".join(issues[:5]), fixable=True)

    def _check_elements_alt_text(self) -> CheckResult:
        """Check #31: Elements require alternate text."""
        pdf = self._pdf
        missing = []

        for node, _depth, _parent in walk_structure_tree(pdf):
            if not _node_has_direct_content(node):
                continue
            alt = node.get("/Alt")
            if alt is not None:
                continue

            stype = _get_struct_type(node)
            if stype in ("Figure", "Formula", "Form"):
                missing.append(f"/{stype} element missing /Alt")

        if not missing:
            return _result("alt-elements", "Alt Text Headings", CheckStatus.PASS,
                           "All elements have alternate text")

        return _result("alt-elements", "Alt Text Headings", CheckStatus.FAIL,
                        "; ".join(missing[:5]), fixable=True)

    def _check_heading_nesting(self) -> CheckResult:
        """Check #32: Appropriate heading nesting."""
        pdf = self._pdf
        headings: list[tuple[str, int]] = []

        for node, _depth, _parent in walk_structure_tree(pdf):
            stype = _get_struct_type(node)
            match = re.match(r"^H(\d)$", stype)
            if match:
                headings.append((stype, int(match.group(1))))

        if not headings:
            return _result("headings-nesting", "Alt Text Headings", CheckStatus.PASS,
                           "No headings found")

        issues = []
        prev_level = 0
        for heading_name, level in headings:
            if prev_level > 0 and level > prev_level + 1:
                issues.append(f"Skipped from H{prev_level} to {heading_name}")
            prev_level = level

        if not issues:
            return _result("headings-nesting", "Alt Text Headings", CheckStatus.PASS,
                           "Heading nesting is correct")

        return _result("headings-nesting", "Alt Text Headings", CheckStatus.FAIL,
                        "; ".join(issues[:10]), fixable=True)
