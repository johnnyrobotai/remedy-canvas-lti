"""HTML transformer for applying accessibility fixes."""

import logging
import re
from typing import Optional
from urllib.parse import urlparse, unquote

from bs4 import BeautifulSoup, NavigableString, Tag

from lti_app.core.accessibility.image_alt import (
    GENERIC_ALT_PREFIXES, MAX_ALT_TEXT_CHARS, truncate_alt_text,
)
from lti_app.core.remediation.math_sel import MathSELConverter
from lti_app.models import AccessibilityIssue, ColorScheme
from lti_app.core.accessibility.rules.tables import is_layout_table
from lti_app.core.accessibility.rules.contrast import (
    InsufficientContrastRule,
    hex_to_rgb,
    rgb_to_hex,
    contrast_ratio,
    adjust_color_for_contrast,
    adjust_background_for_contrast,
    NAMED_COLORS,
)

logger = logging.getLogger(__name__)


def _strip_first_marker(element, marker_type: str, strip_fn) -> bool:
    """Strip the leading list marker from the first text content of an
    element, even when the marker is split across nested text nodes.

    The PPTX/RCE pattern ``<p><span>1.<span> <strong>UNITY</strong>
    </span></span></p>`` has the digit "1." in one text node and the
    " UNITY" in another. The marker regex requires whitespace after
    the period, so neither node alone matches. We:

    1. Walk descendant text nodes in document order, building a flat
       string until we have enough to match the marker (or run out).
    2. When the joined text matches, compute how many characters of
       marker need to be consumed.
    3. Walk the same nodes again and remove the marker characters from
       the start, in order.

    Used by _fix_possible_lists.
    """
    text_nodes = [tn for tn in element.find_all(string=True)]
    if not text_nodes:
        return False

    # Collect lstripped joined text (preserving leading whitespace
    # offsets per node so we can track how much to remove from each).
    joined = "".join(str(tn) for tn in text_nodes)
    lstripped = joined.lstrip()
    consumed_ws = len(joined) - len(lstripped)
    if not lstripped:
        return False

    # Try to strip the marker from the joined text.
    after_strip = strip_fn(lstripped, marker_type)
    if after_strip == lstripped:
        return False  # No marker at the start

    marker_len = len(lstripped) - len(after_strip)

    # Now walk text nodes again, removing chars from the start. We
    # first walk past `consumed_ws` characters of leading whitespace,
    # then `marker_len` characters of marker, then stop.
    to_skip = consumed_ws + marker_len
    for tn in text_nodes:
        if to_skip <= 0:
            break
        original = str(tn)
        if len(original) <= to_skip:
            tn.replace_with("")
            to_skip -= len(original)
        else:
            tn.replace_with(original[to_skip:])
            to_skip = 0
    return True


class HTMLTransformer:
    """Transform HTML to fix accessibility issues."""

    def __init__(self, colors: Optional[ColorScheme] = None):
        """Initialize the transformer.

        Args:
            colors: Optional color scheme for styling fixes.
        """
        self.colors = colors
        self.fixes_applied = []

    def transform(
        self,
        html: str,
        issues: list[AccessibilityIssue] = None,
        alt_texts: dict[str, str] = None,
        page_title: str = "",
    ) -> str:
        """Transform HTML to fix accessibility issues.

        Args:
            html: Original HTML content.
            issues: List of detected issues to fix.
            alt_texts: Mapping of image src to generated alt text.

        Returns:
            Transformed HTML string.
        """
        self.fixes_applied = []
        soup = BeautifulSoup(html, "html.parser")

        # CLU-73 healing pre-pass: detect and collapse malformed
        # doubled-scheme URLs from prior `replace_file_links` runs
        # before any other fix touches them. The bug produced anchors
        # like `https://X.comhttps://X.com/...` which we want to
        # heal in EVERY pass, not just when a specific rule fires.
        # Runs unconditionally so the cleanup happens on any
        # transform call regardless of the issues list.
        self._fix_malformed_doubled_scheme(soup)

        # Apply fixes based on issue types
        if issues:
            issue_rules = {i.rule_id for i in issues if i.can_auto_fix}

            if "HDG001" in issue_rules:
                self._fix_h1_usage(soup)

            if "HDG002" in issue_rules:
                self._fix_heading_hierarchy(soup)

            if "TBL001" in issue_rules:
                self._fix_table_headers(soup)

            if "TBL002" in issue_rules:
                self._fix_table_scope(soup)

            if "TBL003" in issue_rules:
                self._fix_table_captions(soup)

            if "TBL004" in issue_rules:
                self._fix_empty_table_headers(soup)

            if "TBL005" in issue_rules:
                self._fix_layout_tables(soup)

            if "LIST001" in issue_rules:
                self._fix_possible_lists(soup)

            if "IMG002" in issue_rules:
                self._fix_long_alt_text(soup)

            if "LNK002" in issue_rules:
                self._fix_redundant_title_text(soup)

            if "CLR001" in issue_rules:
                self._fix_contrast(soup)
                # Rendered-scan path: any CLR001 issues with axe_meta
                # carry the actual computed fg/bg colors and need a
                # different fix strategy (inline override on the
                # source element matched by element_html).
                rendered_contrast_issues = [
                    i for i in issues
                    if i.rule_id == "CLR001" and i.axe_meta
                ]
                if rendered_contrast_issues:
                    self._fix_rendered_contrast(soup, rendered_contrast_issues)

            if "MATH001" in issue_rules:
                self._fix_math_expressions(soup)

            if "LNK003" in issue_rules:
                self._fix_empty_links(soup)

            if "HDG003" in issue_rules:
                self._fix_empty_headings(soup)

            if "STR001" in issue_rules:
                self._fix_empty_elements(soup)

            if "STR002" in issue_rules:
                self._fix_deprecated_tags(soup)

            if "STR003" in issue_rules:
                self._fix_small_text(soup)

            if "LNK006" in issue_rules:
                self._fix_adjacent_duplicate_links(soup)

            if "LNK001" in issue_rules:
                self._fix_non_descriptive_links(soup)

            if "LNK008" in issue_rules:
                self._fix_broken_link_spaces(soup)

            if "HDG004" in issue_rules:
                self._fix_fake_headings(soup)

            if "HDG005" in issue_rules:
                self._fix_no_heading_structure(soup, page_title)

            if "HDG006" in issue_rules:
                self._fix_long_headings(soup)

            if "MDA002" in issue_rules:
                self._fix_autoplay(soup)

            if "TGT001" in issue_rules:
                self._fix_target_size(soup)

            if "IMG004" in issue_rules:
                self._fix_alt_text_too_long(soup)

            if "IMG009" in issue_rules:
                self._fix_suspicious_alt_text(soup)

            if "IMG005" in issue_rules:
                self._fix_linked_image_alt(soup)

            if "LNK009" in issue_rules:
                self._fix_broken_same_page_links(soup)

            if "ARIA001" in issue_rules:
                self._fix_broken_aria_references(soup)

            if "DID001" in issue_rules:
                self._fix_duplicate_ids(soup)

            if "TBL006" in issue_rules:
                self._fix_sparse_table_cells(soup)

            if "LNK010" in issue_rules:
                self._fix_duplicate_link_text(soup)

            if "LNK011" in issue_rules:
                self._fix_redundant_empty_links(soup)

            if "LNK012" in issue_rules:
                self._fix_image_text_link_merge(soup)

            if "LNK005" in issue_rules:
                self._fix_raw_url_link_text(soup)

            if "MDA001" in issue_rules:
                self._fix_video_iframe_titles(soup)

            if "FOC001" in issue_rules:
                self._fix_focus_obscured(soup)

            if "AUTH001" in issue_rules:
                self._fix_accessible_authentication(soup)

            if "BTN001" in issue_rules:
                self._fix_empty_buttons(soup)

            if "STR004" in issue_rules:
                self._fix_underlined_text(soup)

            if "STR005" in issue_rules:
                self._fix_justified_text(soup)

        # Apply alt text fixes
        if alt_texts:
            self._apply_alt_texts(soup, alt_texts)

        # Safety net: ensure ALL images have an alt attribute
        self._fix_remaining_missing_alt(soup)

        # Safety net: truncate any alt text > MAX_ALT_TEXT_CHARS even if
        # the scan report didn't surface IMG004 issues for this page.
        # The fix is trivially safe (string truncation) and prevents the
        # 2 long-alt cases on Art103 picasso pages where the page wasn't
        # in our scan report for whatever fetch-side reason.
        self._fix_alt_text_too_long(soup)

        # Deduplicate alt text on same page
        self._deduplicate_alt_texts(soup)

        # Always clean up common issues
        self._clean_common_issues(soup)

        return str(soup)

    def _fix_h1_usage(self, soup: BeautifulSoup):
        """Convert H1 tags to H2 and shift other headings."""
        body = soup.find("body") or soup
        h1_tags = body.find_all("h1")

        if not h1_tags:
            return

        logger.info(f"Converting {len(h1_tags)} H1 tags to H2")

        for h1 in h1_tags:
            h1.name = "h2"
            self.fixes_applied.append("HDG001: Converted H1 to H2")

        # Note: We don't automatically shift all headings down
        # as this could break existing hierarchy that was correct

    def _fix_heading_hierarchy(self, soup: BeautifulSoup):
        """Fix skipped heading levels."""
        body = soup.find("body") or soup
        headings = body.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])

        if not headings:
            return

        prev_level = 1  # Assume H1 exists (page title)

        for heading in headings:
            current_level = int(heading.name[1])

            # If we skipped levels, adjust this heading
            if current_level > prev_level + 1:
                new_level = prev_level + 1
                old_name = heading.name
                heading.name = f"h{new_level}"
                logger.info(f"Fixed heading: {old_name} -> {heading.name}")
                self.fixes_applied.append(
                    f"HDG002: Adjusted {old_name} to h{new_level}"
                )
                current_level = new_level

            prev_level = current_level

    def _fix_table_headers(self, soup: BeautifulSoup):
        """Convert first row cells to header cells.

        CLU-74: Empty cells must be skipped. A blank `<td>` converted
        to `<th scope="col"></th>` would trip PopeTech's "Empty table
        header" error (TBL004). The Athletics
        `lamc-new-athlete-orientation` page shipped with exactly this
        bug — second column header was empty and we labelled it a
        header anyway, creating a new error that didn't exist in the
        source course.

        CLU-84: Layout tables must be skipped. The psych-001 OZY
        newsletter page was a nest of 63 HTML-email layout tables
        and this fixer happily promoted first-row `<td>` cells into
        fabricated `<th>` elements in all 62 of the ones with
        populated first cells. That then cascaded into TBL003's
        fake-caption injection. See `is_layout_table` for the
        heuristic.
        """
        for table in soup.find_all("table"):
            # CLU-84: don't fabricate headers inside layout tables
            if is_layout_table(table):
                continue

            th_cells = table.find_all("th")

            if th_cells:
                continue  # Already has headers

            # Find first row
            first_row = table.find("tr")
            if not first_row:
                continue

            # Convert first row cells to th — EXCEPT empty ones. Empty
            # cells stay as <td>; PopeTech correctly allows <td> at
            # any position in a table, but flags empty <th>.
            cells = first_row.find_all("td")
            converted = 0
            for cell in cells:
                # Text content OR an image with alt counts as "non-empty"
                has_text = bool(cell.get_text(strip=True))
                has_img_alt = bool([
                    img for img in cell.find_all("img")
                    if img.get("alt", "").strip()
                ])
                if not (has_text or has_img_alt):
                    continue  # Empty corner cell — leave as <td>
                cell.name = "th"
                if not cell.has_attr("scope"):
                    cell["scope"] = "col"
                converted += 1

            if converted:
                self.fixes_applied.append(
                    f"TBL001: Converted {converted} cells to headers"
                )

    def _fix_table_scope(self, soup: BeautifulSoup):
        """Add scope attribute to table headers."""
        for table in soup.find_all("table"):
            for th in table.find_all("th"):
                if not th.has_attr("scope"):
                    # Determine if column or row header
                    parent_row = th.find_parent("tr")
                    if parent_row:
                        # First row headers are usually column
                        first_row = table.find("tr")
                        if parent_row == first_row:
                            th["scope"] = "col"
                        else:
                            # Check if first cell in row
                            cells = parent_row.find_all(["th", "td"])
                            if cells and cells[0] == th:
                                th["scope"] = "row"
                            else:
                                th["scope"] = "col"

                    self.fixes_applied.append("TBL002: Added scope attribute")

    def _fix_table_captions(self, soup: BeautifulSoup):
        """Add caption elements to tables using PopeTech heuristic.

        Detects possible captions from:
        1. First cell with colspan >= 3 (extracts its text, removes the row)
        2. Short <p> immediately before the table (extracts its text, removes the <p>)
        Falls back to generating a caption from headings / first-row content.
        """
        for table in soup.find_all("table"):
            # Skip if already has caption
            if table.find("caption"):
                continue

            # CLU-84: skip layout tables (role=presentation OR nested
            # HTML-email wrappers). Without this, the fixer happily
            # injected "Data table" captions into layout tables whose
            # <th> elements had just been fabricated by TBL001.
            if is_layout_table(table):
                continue

            # CLU-84: a table with no <th> is not a data table —
            # leave it alone. TBL001 handles the "needs headers" case;
            # this fixer only handles the separate "is a data table
            # AND has headers BUT lacks a caption" case.
            if not table.find("th"):
                continue

            caption_text = None
            source_element = None  # element to remove after extracting caption

            if table.find("th"):
                # Check 1: First cell has colspan >= 3
                first_row = table.find("tr")
                if first_row:
                    first_cell = first_row.find(["td", "th"])
                    if first_cell:
                        try:
                            if int(first_cell.get("colspan", "1")) >= 3:
                                caption_text = first_cell.get_text(strip=True)
                                # Remove the spanning row since its text becomes the caption
                                source_element = first_row
                        except ValueError:
                            pass

                # Check 2: <p> immediately before table
                if not caption_text:
                    prev = table.find_previous_sibling()
                    if prev and prev.name == "p":
                        text = prev.get_text(strip=True)
                        if text:
                            is_bold = bool(
                                prev.find(["b", "strong"])
                                or "font-weight" in prev.get("style", "")
                            )
                            is_centered = (
                                "center" in prev.get("style", "")
                                or "center" in " ".join(prev.get("class", []))
                            )
                            if len(text) < 50:
                                caption_text = text
                                source_element = prev
                            elif len(text) < 100 and (is_bold or is_centered):
                                caption_text = text
                                source_element = prev

            # Fallback: generate from surrounding context
            if not caption_text:
                caption_text = self._generate_table_caption(table)

            # CLU-84: if we couldn't synthesize a real caption, leave
            # the table alone. A meaningless "Data table" caption is
            # both useless to screen readers and trips PopeTech's
            # non-descriptive caption heuristic — better to ship no
            # caption than a bad one.
            if not caption_text:
                continue

            # Create and insert caption
            caption = soup.new_tag("caption")
            caption.string = caption_text
            caption["style"] = "text-align: center; font-weight: bold; padding: 8px;"
            table.insert(0, caption)

            # Remove the source element that provided the caption text
            if source_element:
                source_element.decompose()

            self.fixes_applied.append("TBL003: Added table caption")

    def _generate_table_caption(self, table) -> str | None:
        """Fallback: generate a caption for a table based on its content.

        Returns ``None`` when no caption can be confidently synthesized —
        in that case the caller should NOT inject any caption rather
        than fall back to the meaningless string "Data table" (CLU-84).
        """
        # Check preceding heading
        prev_heading = table.find_previous_sibling(["h2", "h3", "h4", "h5", "h6"])
        if prev_heading:
            text = prev_heading.get_text(strip=True)
            if text:
                return f"Table: {text}"

        # Check first row content — only trust it if at least two
        # cells contain real text (a single-cell "preview" is likely
        # just the first paragraph of prose in a layout cell)
        first_row = table.find("tr")
        if first_row:
            headers = first_row.find_all(["th", "td"])
            header_texts = [
                h.get_text(strip=True)[:20]
                for h in headers[:3]
                if h.get_text(strip=True)
            ]
            if len(header_texts) >= 2:
                return f"Table with columns: {', '.join(header_texts)}"

        return None

    def _fix_empty_table_headers(self, soup: BeautifulSoup):
        """Convert empty <th> cells to <td> or add descriptive text."""
        for table in soup.find_all("table"):
            if table.get("role") == "presentation":
                continue

            first_row = table.find("tr")
            for th in table.find_all("th"):
                if th.get_text(strip=True):
                    continue  # Has text — not empty

                # Check for images with alt text (PopeTech: not empty if img has alt)
                imgs_with_alt = [img for img in th.find_all("img") if img.get("alt", "").strip()]
                if imgs_with_alt:
                    continue  # Has image with alt — not empty

                parent_row = th.find_parent("tr")
                cells_in_row = parent_row.find_all(["th", "td"]) if parent_row else []

                # Corner cell: in the first row AND first cell position
                is_first_row = parent_row == first_row
                is_first_cell = cells_in_row and cells_in_row[0] == th

                if is_first_row and is_first_cell:
                    # Convert corner cell to <td> — removes the empty header error
                    th.name = "td"
                    if th.has_attr("scope"):
                        del th["scope"]
                    self.fixes_applied.append("TBL004: Converted empty corner header to <td>")
                else:
                    # Non-corner empty header: convert to <td> as well
                    # since an empty header provides no useful information
                    th.name = "td"
                    if th.has_attr("scope"):
                        del th["scope"]
                    self.fixes_applied.append("TBL004: Converted empty header to <td>")

    def _fix_layout_tables(self, soup: BeautifulSoup):
        """Add role='presentation' to layout tables and remove misleading semantics."""
        for table in soup.find_all("table"):
            if table.get("role") == "presentation":
                continue
            if table.find("caption") or table.find("th"):
                continue

            rows = table.find_all("tr")
            is_layout = False

            if len(rows) <= 1:
                is_layout = True
            else:
                # Single column
                all_single = all(
                    len(row.find_all(["td", "th"])) <= 1 for row in rows
                )
                if all_single:
                    is_layout = True

            if is_layout:
                table["role"] = "presentation"
                # Remove misleading caption
                caption = table.find("caption")
                if caption:
                    caption.decompose()
                # Remove scope attributes from any cells
                for cell in table.find_all(["th", "td"]):
                    if cell.has_attr("scope"):
                        del cell["scope"]
                self.fixes_applied.append("TBL005: Added role='presentation' to layout table")

    def _fix_possible_lists(self, soup: BeautifulSoup):
        """Convert sequences of numbered/lettered paragraphs to semantic lists."""
        # Regex patterns for list markers at start of paragraph text.
        # CLU-75: `unordered_re` is relaxed to `^[-*•]+\s?` to match
        # multi-dash markers (`-----quote`) and dash-prefixed words
        # without trailing space (`-egg tempera`). Must stay in sync
        # with the rule's `UNORDERED_RE` in
        # `core/accessibility/rules/lists.py`.
        ordered_num_re = re.compile(r"^(\d+)[.)]\s")
        ordered_letter_re = re.compile(r"^([a-z])[.)]\s")
        ordered_upper_letter_re = re.compile(r"^([A-Z])[.)]\s")
        ordered_roman_re = re.compile(r"^(i{1,3}|iv|vi{0,3})[.)]\s")
        unordered_re = re.compile(r"^[-*•]+\s?")

        def get_marker_type(text):
            if ordered_num_re.match(text):
                return "numeric"
            if ordered_letter_re.match(text):
                return "letter"
            if ordered_upper_letter_re.match(text):
                return "upper_letter"
            if ordered_roman_re.match(text):
                return "roman"
            if unordered_re.match(text):
                return "unordered"
            return None

        def strip_marker(text, marker_type):
            """Strip the leading list marker from text."""
            if marker_type == "numeric":
                return ordered_num_re.sub("", text, count=1)
            if marker_type == "letter":
                return ordered_letter_re.sub("", text, count=1)
            if marker_type == "upper_letter":
                return ordered_upper_letter_re.sub("", text, count=1)
            if marker_type == "roman":
                return ordered_roman_re.sub("", text, count=1)
            if marker_type == "unordered":
                return unordered_re.sub("", text, count=1)
            return text

        def has_complex_children(tag):
            return bool(tag.find(["table", "div", "section", "article", "figure", "ol", "ul"]))

        body = soup.find("body") or soup
        all_paragraphs = body.find_all("p")
        visited = set()

        for p in all_paragraphs:
            if id(p) in visited:
                continue

            text = p.get_text(separator=" ", strip=True)
            if not text:
                continue

            marker_type = get_marker_type(text)
            if not marker_type:
                continue

            if has_complex_children(p):
                continue

            # Build group of consecutive siblings with same marker type
            group = [p]
            visited.add(id(p))
            sibling = p.next_sibling

            while sibling is not None:
                if isinstance(sibling, str) and not sibling.strip():
                    sibling = sibling.next_sibling
                    continue
                # Skip <br> tags between list items
                if hasattr(sibling, "name") and sibling.name == "br":
                    sibling = sibling.next_sibling
                    continue
                if not hasattr(sibling, "name") or sibling.name != "p":
                    break
                sib_text = sibling.get_text(separator=" ", strip=True)
                # Skip empty paragraphs between list items (common in PPTX)
                if not sib_text:
                    next_after = sibling.next_sibling
                    while next_after and isinstance(next_after, str) and not next_after.strip():
                        next_after = next_after.next_sibling
                    if (next_after and hasattr(next_after, "name")
                            and next_after.name == "p"
                            and next_after.get_text(separator=" ", strip=True)
                            and get_marker_type(next_after.get_text(separator=" ", strip=True)) == marker_type):
                        sibling = sibling.next_sibling
                        continue
                    break
                if get_marker_type(sib_text) != marker_type:
                    break
                if has_complex_children(sibling):
                    break
                group.append(sibling)
                visited.add(id(sibling))
                sibling = sibling.next_sibling

            if len(group) < 2:
                continue

            # Create the list element
            if marker_type == "unordered":
                list_tag = soup.new_tag("ul")
            elif marker_type == "letter":
                list_tag = soup.new_tag("ol", type="a")
            elif marker_type == "upper_letter":
                list_tag = soup.new_tag("ol", type="A")
            elif marker_type == "roman":
                list_tag = soup.new_tag("ol", type="i")
            else:
                list_tag = soup.new_tag("ol")

            # Build <li> elements preserving inner HTML
            for para in group:
                # Strip the marker prefix from the first descendant text
                # node that contains it. Walks the tree in document order
                # so the marker can be buried inside nested <span> /
                # <strong> wrappers from PPTX exports or RCE styling.
                _strip_first_marker(para, marker_type, strip_marker)

                li = soup.new_tag("li")
                for child in list(para.children):
                    if isinstance(child, str):
                        li.append(NavigableString(child))
                    else:
                        li.append(child.extract())
                list_tag.append(li)

            # Replace the first paragraph with the list, remove the rest
            group[0].replace_with(list_tag)
            for para in group[1:]:
                para.decompose()

            self.fixes_applied.append(
                f"LIST001: Converted {len(group)} paragraphs to {'<ul>' if marker_type == 'unordered' else '<ol>'}"
            )

        # CLU-75 — second pass: convert <br>-separated marker lines
        # inside a single <p> to a semantic list. The Art103 cases are
        # `<p>-egg tempera<br>-oil paints<br>-sfumato</p>` and
        # `<p>-----"quote 1"<br><br>-----"quote 2"</p>` where the
        # markers live inside a single <p> instead of across siblings.
        from lti_app.core.accessibility.rules.lists import _split_p_on_br
        for p in list(body.find_all("p")):
            if has_complex_children(p):
                continue
            if not p.find("br"):
                continue

            chunks = _split_p_on_br(p)
            # Filter empty chunks and pair with marker types
            line_data = []
            for chunk in chunks:
                stripped = chunk.strip()
                if not stripped:
                    continue
                mtype = get_marker_type(stripped)
                if mtype is None:
                    continue
                line_data.append((stripped, mtype))

            if len(line_data) < 2:
                continue

            # All matching markers must be the same type
            common_type = line_data[0][1]
            if not all(d[1] == common_type for d in line_data):
                continue

            # Build the list element
            if common_type == "unordered":
                list_tag = soup.new_tag("ul")
            elif common_type == "letter":
                list_tag = soup.new_tag("ol", type="a")
            elif common_type == "upper_letter":
                list_tag = soup.new_tag("ol", type="A")
            elif common_type == "roman":
                list_tag = soup.new_tag("ol", type="i")
            else:
                list_tag = soup.new_tag("ol")

            for raw_text, mtype in line_data:
                stripped_text = strip_marker(raw_text, mtype).strip()
                li = soup.new_tag("li")
                li.string = stripped_text
                list_tag.append(li)

            p.replace_with(list_tag)
            self.fixes_applied.append(
                f"LIST001: Converted {len(line_data)} <br>-separated lines "
                f"inside <p> to {'<ul>' if common_type == 'unordered' else '<ol>'}"
            )

    # CLU-73: heal `https://X.comhttps://X.com/...` corruption from
    # prior buggy `FileManager.replace_file_links` runs. Pattern:
    # any scheme `https?://`, any non-slash chars, then a SECOND
    # `https?://`. Collapse to the second occurrence.
    _DOUBLED_SCHEME_RE = re.compile(
        r'https?://[^/\s"\'>]*?(https?://)',
        re.IGNORECASE,
    )

    # Attributes that can carry the corruption.
    _DOUBLED_SCHEME_ATTRS = (
        "href",
        "src",
        "poster",
        "data",
        "data-api-endpoint",
        "data-api-returntype",
    )

    def _fix_malformed_doubled_scheme(self, soup: BeautifulSoup):
        """Collapse `https?://X.comhttps?://...` URLs to the second scheme.

        Heals existing live-page corruption from prior `replace_file_links`
        bugs. Walks every relevant attribute on every element and
        rewrites only when the doubled-scheme pattern is found —
        clean URLs and relative URLs are untouched.
        """
        healed = 0
        for tag in soup.find_all(True):
            for attr_name in self._DOUBLED_SCHEME_ATTRS:
                val = tag.get(attr_name)
                if not val or not isinstance(val, str):
                    continue
                if "://" not in val:
                    continue
                # Find the SECOND scheme occurrence and slice from there.
                # Using `find` rather than regex sub gives more
                # predictable behavior on edge cases like queries
                # containing `https://`.
                first = val.find("://")
                if first < 0:
                    continue
                second_idx = val.find("://", first + 3)
                if second_idx < 0:
                    continue
                # Walk back from second_idx to find the start of the
                # second scheme (https or http).
                # The healed URL starts at second_idx - len("https") or
                # second_idx - len("http"), whichever is present.
                # We look for the closest `http` or `https` token
                # immediately before second_idx.
                start = second_idx
                # Match `https` first (longer prefix), then `http`
                if val[max(0, second_idx - 5):second_idx].lower() == "https":
                    start = second_idx - 5
                elif val[max(0, second_idx - 4):second_idx].lower() == "http":
                    start = second_idx - 4
                else:
                    continue
                healed_url = val[start:]
                if healed_url != val:
                    tag[attr_name] = healed_url
                    healed += 1

        if healed:
            self.fixes_applied.append(
                f"CLU-73: Healed {healed} doubled-scheme URL(s)"
            )

    def _fix_long_alt_text(self, soup: BeautifulSoup):
        """Truncate alt text so it stays under 120 characters."""
        for img in soup.find_all("img"):
            alt = img.get("alt", "")
            if len(alt) > MAX_ALT_TEXT_CHARS:
                img["alt"] = truncate_alt_text(alt)
                self.fixes_applied.append("IMG002: Truncated long alt text")

    def _fix_redundant_title_text(self, soup: BeautifulSoup):
        """Remove title attributes that duplicate link text."""
        for link in soup.find_all("a"):
            title = link.get("title", "").strip()
            if not title:
                continue
            text = link.get_text(strip=True)
            norm_title = title.lower().strip()
            norm_text = text.lower().strip()
            if norm_title and norm_text and (
                norm_title == norm_text
                or norm_title.startswith(norm_text)
                or norm_text.startswith(norm_title)
            ):
                del link["title"]
                self.fixes_applied.append("LNK002: Removed redundant title")

    # Default Canvas text color for contrast calculations when no explicit color set
    _CANVAS_DEFAULT_TEXT_RGB = (45, 59, 69)  # #2d3b45

    def _resolve_inherited_color(self, element, prop: str) -> str | None:
        """Walk DOM ancestors to find the nearest inherited color value.

        Args:
            element: BeautifulSoup Tag to start from
            prop: CSS property name ('color' or 'background-color')

        Returns:
            Hex color string or None if not found in any ancestor's inline style
        """
        current = element
        while current and hasattr(current, 'get'):
            style = current.get("style", "")
            if style:
                color = self._extract_inline_color(style, prop)
                if color:
                    return color
            current = current.parent
        return None

    def _fix_contrast(self, soup: BeautifulSoup):
        """Fix inline color contrast issues to meet WCAG ratio (4.5:1 normal, 3:1 large text)."""
        for element in soup.find_all(style=True):
            if not isinstance(element, Tag):
                continue

            style = element.get("style", "")
            if not style:
                continue

            fg_hex = self._extract_inline_color(style, "color")
            bg_hex = self._extract_inline_color(style, "background-color")
            target_ratio = (
                InsufficientContrastRule.LARGE_TEXT_RATIO
                if self._is_large_text_element(element)
                else InsufficientContrastRule.NORMAL_TEXT_RATIO
            )

            if fg_hex and bg_hex:
                fg_rgb = hex_to_rgb(fg_hex)
                bg_rgb = hex_to_rgb(bg_hex)
                if not fg_rgb or not bg_rgb:
                    continue

                ratio = contrast_ratio(fg_rgb, bg_rgb)
                if ratio < target_ratio:
                    new_fg = adjust_color_for_contrast(fg_rgb, bg_rgb, target_ratio=target_ratio)
                    new_ratio = contrast_ratio(new_fg, bg_rgb)
                    new_hex = rgb_to_hex(new_fg)
                    if new_fg != fg_rgb and new_ratio >= target_ratio:
                        # Foreground adjustment actually met the target.
                        new_style = self._replace_inline_color(style, "color", new_hex)
                        element["style"] = new_style
                        self.fixes_applied.append(
                            f"CLR001: Adjusted color from {fg_hex} to {new_hex}"
                        )
                    else:
                        # Dead-end: either fg is at an extreme the
                        # adjuster can't move (e.g. #000 on a bg with
                        # luminance just above 0.18), OR adjusting fg
                        # toward black/white didn't actually reach the
                        # target (the whole fg range fails). Keep fg,
                        # shift the bg minimally so the palette stays
                        # close to the author's intent.
                        new_bg = adjust_background_for_contrast(
                            fg_rgb, bg_rgb, target_ratio=target_ratio
                        )
                        new_bg_hex = rgb_to_hex(new_bg)
                        if new_bg != bg_rgb:
                            new_style = self._replace_inline_color(
                                style, "background-color", new_bg_hex
                            )
                            element["style"] = new_style
                            self.fixes_applied.append(
                                f"CLR001: Adjusted background from "
                                f"{bg_hex} to {new_bg_hex} "
                                f"(fg {fg_hex} could not be moved)"
                            )

            elif fg_hex and not bg_hex:
                fg_rgb = hex_to_rgb(fg_hex)
                if not fg_rgb:
                    continue

                # CLU-72: walk ancestors for inherited `background-color:`
                # before falling back to white. Otherwise the adjusted
                # fg meets the threshold vs white but still fails vs
                # the actual ancestor bg color (e.g. yellow highlight
                # `#fbeeb8` in Art103), and PopeTech keeps flagging it
                # every remediation.
                ancestor_bg_hex = self._resolve_inherited_color(
                    element, "background-color"
                )
                ancestor_bg_rgb = (
                    hex_to_rgb(ancestor_bg_hex) if ancestor_bg_hex else None
                )
                bg_rgb = ancestor_bg_rgb or (255, 255, 255)
                ratio = contrast_ratio(fg_rgb, bg_rgb)
                if ratio < target_ratio:
                    new_fg = adjust_color_for_contrast(fg_rgb, bg_rgb, target_ratio=target_ratio)
                    new_hex = rgb_to_hex(new_fg)
                    new_style = self._replace_inline_color(style, "color", new_hex)
                    element["style"] = new_style
                    bg_label = ancestor_bg_hex or "white"
                    self.fixes_applied.append(
                        f"CLR001: Adjusted {fg_hex} -> {new_hex} "
                        f"(against ancestor bg {bg_label})"
                    )

            elif bg_hex and not fg_hex:
                bg_rgb = hex_to_rgb(bg_hex)
                if not bg_rgb:
                    continue

                fg_rgb = self._CANVAS_DEFAULT_TEXT_RGB
                ratio = contrast_ratio(fg_rgb, bg_rgb)
                if ratio < target_ratio:
                    new_fg = adjust_color_for_contrast(fg_rgb, bg_rgb, target_ratio=target_ratio)
                    new_hex = rgb_to_hex(new_fg)
                    element["style"] = style.rstrip("; ") + f"; color: {new_hex};"
                    self.fixes_applied.append(
                        f"CLR001: Added color {new_hex} for background {bg_hex}"
                    )

        # Second pass: elements with text but no inline color — check inherited contrast
        for element in soup.find_all(True):
            if not isinstance(element, Tag):
                continue
            # Skip if already has inline color (handled by first pass)
            style = element.get("style", "")
            if style and re.search(r"(?:^|;)\s*color\s*:", style):
                continue
            # Only process leaf text elements (no child tags)
            text = element.get_text(strip=True)
            if not text:
                continue
            if element.find(True):
                continue  # Has child elements — skip containers

            fg_hex = self._resolve_inherited_color(element, "color")
            bg_hex = self._resolve_inherited_color(element, "background-color")

            if not fg_hex or not bg_hex:
                continue  # Can't determine colors — need CSS resolution, skip

            fg_rgb = hex_to_rgb(fg_hex)
            bg_rgb = hex_to_rgb(bg_hex)
            if not fg_rgb or not bg_rgb:
                continue

            target_ratio = (
                InsufficientContrastRule.LARGE_TEXT_RATIO
                if self._is_large_text_element(element)
                else InsufficientContrastRule.NORMAL_TEXT_RATIO
            )
            ratio = contrast_ratio(fg_rgb, bg_rgb)

            if ratio < target_ratio:
                new_fg = adjust_color_for_contrast(fg_rgb, bg_rgb, target_ratio=target_ratio)
                new_hex = rgb_to_hex(new_fg)
                existing_style = element.get("style", "")
                if existing_style:
                    element["style"] = f"color: {new_hex}; {existing_style}"
                else:
                    element["style"] = f"color: {new_hex}"
                self.fixes_applied.append(
                    f"CLR001: Fixed inherited contrast from {fg_hex} to {new_hex}"
                )

    def _fix_rendered_contrast(
        self,
        soup: BeautifulSoup,
        rendered_issues: list,
    ) -> None:
        """Inject inline color overrides for class-based contrast issues
        flagged by axe-core's rendered scan.

        For each issue with axe_meta populated:
        1. Locate the source element by parsing element_html and
           matching against the source soup (tag name + key attributes
           + text content fingerprint).
        2. Compute a new fg color via adjust_color_for_contrast against
           the actual rendered bg color (from axe_meta).
        3. Inject inline ``style="color: {new}"`` on the source element,
           merging with any existing style.

        Static `_fix_contrast` only handles inline-color cases. This
        path closes the gap for `<strong>EXAMPLE:</strong>` and
        `<a class="">link</a>` patterns where the color comes from a
        CSS class somewhere in Canvas's stylesheet hierarchy.
        """
        from bs4 import BeautifulSoup as BS

        for issue in rendered_issues:
            meta = issue.axe_meta or {}
            fg = meta.get("fg_color")
            bg = meta.get("bg_color")
            if not fg or not bg:
                continue
            fg_rgb = hex_to_rgb(fg)
            bg_rgb = hex_to_rgb(bg)
            if not fg_rgb or not bg_rgb:
                continue

            # Compute the safe color targeting our usual threshold
            target_ratio = (
                InsufficientContrastRule.LARGE_TEXT_RATIO
                if self._is_large_text_meta(meta)
                else InsufficientContrastRule.NORMAL_TEXT_RATIO
            )
            new_fg = adjust_color_for_contrast(fg_rgb, bg_rgb, target_ratio=target_ratio)
            new_hex = rgb_to_hex(new_fg)

            # Locate the source element. Parse the element_html (the
            # outerHTML of the offending element from axe) to get the
            # tag name + key attributes, then find a matching element
            # in the source soup.
            element_html = issue.element_html or ""
            if not element_html:
                continue
            try:
                fragment = BS(element_html, "html.parser")
            except Exception:
                continue
            target_tag = next(iter(fragment.find_all(True)), None)
            if target_tag is None:
                continue

            target_text = target_tag.get_text(strip=True)
            target_name = target_tag.name

            # Find a matching element in the source soup. Match on:
            # tag name AND (id || class || text-content prefix)
            target_id = target_tag.get("id")
            target_classes = target_tag.get("class") or []
            candidate = None
            for el in soup.find_all(target_name):
                if target_id and el.get("id") == target_id:
                    candidate = el
                    break
                if target_classes and el.get("class") == target_classes:
                    if not target_text or el.get_text(strip=True).startswith(target_text[:30]):
                        candidate = el
                        break
                if target_text and el.get_text(strip=True) == target_text:
                    candidate = el
                    break

            if candidate is None:
                continue

            existing_style = (candidate.get("style") or "").strip()
            new_style_parts = [f"color: {new_hex}"]
            if existing_style:
                # Drop any existing color: declaration so we override cleanly
                cleaned = re.sub(
                    r"(?:^|;)\s*color\s*:[^;]*;?",
                    "",
                    existing_style,
                ).strip("; ")
                if cleaned:
                    new_style_parts.append(cleaned)
            candidate["style"] = "; ".join(new_style_parts)
            self.fixes_applied.append(
                f"CLR001: Inline-overrode rendered contrast {fg}→{new_hex} on <{target_name}>"
            )

    @staticmethod
    def _is_large_text_meta(meta: dict) -> bool:
        """Determine if axe_meta indicates large text (WCAG 1.4.3 large
        text definition: >= 18pt OR >= 14pt bold)."""
        font_size = meta.get("font_size", "")
        font_weight = (meta.get("font_weight") or "").lower()
        if not font_size:
            return False
        # axe reports font-size like "14.0pt (18.6667px)" — extract pt
        m = re.search(r"([\d.]+)\s*pt", font_size)
        if not m:
            return False
        pt = float(m.group(1))
        if pt >= 18:
            return True
        if pt >= 14 and font_weight in ("bold", "700", "800", "900"):
            return True
        return False

    @staticmethod
    def _is_large_text_element(element: Tag) -> bool:
        """Check if element has large text (WCAG: >=18pt/24px or >=14pt/18.67px bold)."""
        style = element.get("style", "")
        if not style:
            return False

        font_size_px = None
        match = re.search(r"font-size\s*:\s*([\d.]+)\s*px", style)
        if match:
            font_size_px = float(match.group(1))
        if font_size_px is None:
            match = re.search(r"font-size\s*:\s*([\d.]+)\s*pt", style)
            if match:
                font_size_px = float(match.group(1)) * 1.333
        if font_size_px is None:
            match = re.search(r"font-size\s*:\s*([\d.]+)\s*(?:em|rem)", style)
            if match:
                font_size_px = float(match.group(1)) * 16
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
        return font_size_px >= 24 or (font_size_px >= 18.67 and is_bold)

    @staticmethod
    def _extract_inline_color(style: str, property_name: str) -> Optional[str]:
        """Extract a color value from an inline style string.

        Uses negative lookbehind to avoid matching 'color' within 'background-color'.
        """
        # Negative lookbehind prevents matching 'color' inside 'background-color'
        lb = r"(?<![a-zA-Z-])" if property_name == "color" else ""

        # Hex colors
        match = re.search(
            rf"{lb}{property_name}\s*:\s*(#[0-9A-Fa-f]{{3,6}})", style
        )
        if match:
            return match.group(1)

        # rgb() colors
        match = re.search(
            rf"{lb}{property_name}\s*:\s*rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)",
            style,
        )
        if match:
            r, g, b = match.groups()
            return f"#{int(r):02x}{int(g):02x}{int(b):02x}"

        # Named colors
        match = re.search(rf"{lb}{property_name}\s*:\s*([a-zA-Z]+)", style)
        if match:
            name = match.group(1).lower()
            if name in NAMED_COLORS:
                return NAMED_COLORS[name]

        return None

    @staticmethod
    def _replace_inline_color(
        style: str, property_name: str, new_hex: str
    ) -> str:
        """Replace a color value in an inline style string.

        Uses negative lookbehind to avoid corrupting 'background-color' when
        replacing 'color'.
        """
        # Negative lookbehind prevents matching 'color' inside 'background-color'
        lb = r"(?<![a-zA-Z-])" if property_name == "color" else ""

        # Replace hex
        result, count = re.subn(
            rf"({lb}{property_name}\s*:\s*)#[0-9A-Fa-f]{{3,6}}",
            rf"\g<1>{new_hex}",
            style,
        )
        if count:
            return result

        # Replace rgb()
        result, count = re.subn(
            rf"({lb}{property_name}\s*:\s*)rgb\s*\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)",
            rf"\g<1>{new_hex}",
            style,
        )
        if count:
            return result

        # Replace named color
        result, count = re.subn(
            rf"({lb}{property_name}\s*:\s*)[a-zA-Z]+",
            rf"\g<1>{new_hex}",
            style,
        )
        if count:
            return result

        return style

    def _fix_math_expressions(self, soup: BeautifulSoup):
        """Convert LaTeX math expressions to accessible HTML."""
        converter = MathSELConverter()
        _, conversions = converter.convert_page_html(soup)
        if conversions > 0:
            self.fixes_applied.append(
                f"MATH001: Converted {conversions} LaTeX expression(s) to accessible HTML"
            )

    def _fix_empty_links(self, soup: BeautifulSoup):
        """Fix links with no accessible text per PopeTech algorithm.

        PopeTech requires: anchor has href, contains no text and no images with alt.
        Fix: inject visually hidden <span> with descriptive text.
        """
        for link in list(soup.find_all("a", href=True)):
            text = link.get_text(strip=True).replace("\u00a0", "").strip()
            if text:
                continue
            if link.get("aria-label"):
                continue

            # Check for images with alt text
            imgs_with_alt = [img for img in link.find_all("img") if img.get("alt", "").strip()]
            if imgs_with_alt:
                continue  # Image provides the accessible name

            href = link.get("href", "")

            # Generate descriptive label from href context
            filename = href.split("/")[-1].split("?")[0].split("#")[0]
            if filename and filename != href and not filename.startswith("http"):
                label = filename.replace("-", " ").replace("_", " ")
                if "." in label:
                    label = label.rsplit(".", 1)[0]
                label = label.strip().title() or "Link"
            else:
                label = "Link"

            # Inject visually hidden span (PopeTech's recommended pattern)
            hidden_span = soup.new_tag("span")
            hidden_span["style"] = (
                "position: absolute; width: 1px; height: 1px; "
                "padding: 0; margin: -1px; overflow: hidden; "
                "clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;"
            )
            hidden_span.string = label
            link.append(hidden_span)

            # Mark any icon children as aria-hidden
            for icon in link.find_all(["i", "span"]):
                if icon != hidden_span and not icon.get_text(strip=True):
                    icon["aria-hidden"] = "true"

            self.fixes_applied.append(f"LNK003: Added hidden text '{label}' to empty link")

    def _fix_empty_headings(self, soup: BeautifulSoup):
        """Remove empty heading elements per PopeTech algorithm.

        A heading is 'empty' if it contains no text AND no images with alt text.
        """
        body = soup.find("body") or soup
        for heading in list(body.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])):
            text = heading.get_text(strip=True).replace("\u00a0", "").strip()
            if text:
                continue  # Has text — not empty

            # Check for images with alt text
            imgs_with_alt = [img for img in heading.find_all("img") if img.get("alt", "").strip()]
            if imgs_with_alt:
                continue  # Has image with alt — not empty per PopeTech

            # Truly empty heading — remove it
            heading.decompose()
            self.fixes_applied.append("HDG003: Removed empty heading")

    def _fix_empty_elements(self, soup: BeautifulSoup):
        """Remove consecutive empty paragraphs and divs (2+ in a row)."""
        body = soup.find("body") or soup
        for tag_name in ("p", "div"):
            removed = True
            while removed:
                removed = False
                elements = body.find_all(tag_name)
                i = 0
                while i < len(elements) - 1:
                    el = elements[i]
                    if self._is_structural_empty(el):
                        # Check next element
                        next_el = elements[i + 1] if i + 1 < len(elements) else None
                        if next_el and self._is_structural_empty(next_el):
                            el.decompose()
                            self.fixes_applied.append(f"STR001: Removed empty <{tag_name}>")
                            removed = True
                            break
                    i += 1

    @staticmethod
    def _is_structural_empty(tag) -> bool:
        """Check if a tag is empty (for STR001 fix)."""
        if not isinstance(tag, Tag):
            return False
        if tag.get("id") or tag.get("role"):
            return False
        for attr in tag.attrs:
            if attr.startswith("aria-"):
                return False
        text = tag.get_text(strip=True).replace("\u00a0", "").strip()
        return not text

    def _fix_deprecated_tags(self, soup: BeautifulSoup):
        """Convert deprecated HTML tags to modern equivalents."""
        mappings = {
            "font": self._convert_font_tag,
            "center": lambda tag: self._rename_tag(tag, "div", style="text-align: center;"),
            "blink": lambda tag: self._rename_tag(tag, "span"),
            "marquee": lambda tag: self._rename_tag(tag, "span"),
            "big": lambda tag: self._rename_tag(tag, "span"),
            "strike": lambda tag: self._rename_tag(tag, "del"),
            "tt": lambda tag: self._rename_tag(tag, "code"),
        }
        for tag_name, converter in mappings.items():
            for tag in soup.find_all(tag_name):
                converter(tag)
                self.fixes_applied.append(f"STR002: Converted <{tag_name}> to modern element")

    @staticmethod
    def _convert_font_tag(tag):
        """Convert <font> to <span> with inline styles."""
        style_parts = []
        color = tag.get("color")
        if color:
            style_parts.append(f"color: {color}")
            del tag["color"]
        face = tag.get("face")
        if face:
            style_parts.append(f"font-family: {face}")
            del tag["face"]
        if tag.get("size"):
            del tag["size"]
        tag.name = "span"
        if style_parts:
            existing = tag.get("style", "")
            tag["style"] = "; ".join(style_parts) + (f"; {existing}" if existing else "")

    @staticmethod
    def _rename_tag(tag, new_name, style=None):
        """Rename a tag, optionally adding a style."""
        tag.name = new_name
        if style:
            tag["style"] = style

    def _fix_small_text(self, soup: BeautifulSoup):
        """Remove or normalize very small font-size declarations."""
        for element in soup.find_all(style=True):
            if not isinstance(element, Tag):
                continue
            style = element.get("style", "")
            if not style or not element.get_text(strip=True):
                continue
            # Check for small font-size
            has_small = False
            if re.search(r"font-size\s*:\s*[\d.]+\s*px", style):
                match = re.search(r"font-size\s*:\s*([\d.]+)\s*px", style)
                if match and float(match.group(1)) <= 10:
                    has_small = True
            elif re.search(r"font-size\s*:\s*[\d.]+\s*pt", style):
                match = re.search(r"font-size\s*:\s*([\d.]+)\s*pt", style)
                if match and float(match.group(1)) < 10:
                    has_small = True
            elif re.search(r"font-size\s*:\s*[\d.]+\s*(?:em|rem)", style):
                match = re.search(r"font-size\s*:\s*([\d.]+)\s*(?:em|rem)", style)
                if match and float(match.group(1)) < 0.75:
                    has_small = True
            elif re.search(r"font-size\s*:\s*[\d.]+\s*%", style):
                match = re.search(r"font-size\s*:\s*([\d.]+)\s*%", style)
                if match and float(match.group(1)) < 75:
                    has_small = True

            if has_small:
                new_style = re.sub(r"font-size\s*:[^;]+;?\s*", "", style).strip()
                if new_style:
                    element["style"] = new_style
                elif "style" in element.attrs:
                    del element["style"]
                self.fixes_applied.append("STR003: Removed small font-size")

    # Match an active "text-decoration: underline" declaration so we can
    # strip just the underline part while preserving sibling declarations
    # (color, font-weight, etc.). Mirror of _UNDERLINE_STYLE_RE in the
    # detector — kept local to avoid an import cycle.
    _UNDERLINE_DECL_RE = re.compile(
        r"\s*text-decoration\s*:\s*[^;]*\bunderline\b[^;]*;?",
        re.IGNORECASE,
    )

    def _fix_underlined_text(self, soup: BeautifulSoup):
        """Strip underline from non-link text.

        Two cases (Art103 PopeTech regression: 65 alerts):
        1. Legacy ``<u>`` tags → renamed to ``<em>``
        2. Inline ``text-decoration: underline`` on any non-link element →
           the underline declaration is removed; other declarations
           (color, font-weight, etc.) survive intact.
        """
        for u in list(soup.find_all("u")):
            u.name = "em"
            self.fixes_applied.append("STR004: Replaced <u> with <em>")

        for el in soup.find_all(style=True):
            if not isinstance(el, Tag):
                continue
            if el.name == "a":
                continue  # Links are naturally underlined; leave them alone
            style = el.get("style", "")
            if "underline" not in style.lower():
                continue
            new_style = self._UNDERLINE_DECL_RE.sub("", style).strip()
            # Tidy: collapse double semicolons / dangling separators
            new_style = re.sub(r";\s*;", ";", new_style).strip("; ")
            if new_style == style.strip().rstrip(";"):
                continue  # Pattern didn't actually match; nothing to do
            if new_style:
                el["style"] = new_style
            elif "style" in el.attrs:
                del el["style"]
            self.fixes_applied.append(
                "STR004: Removed text-decoration: underline from inline style"
            )

    def _fix_justified_text(self, soup: BeautifulSoup):
        """Replace text-align:justify with text-align:left."""
        for el in soup.find_all(style=True):
            style = el.get("style", "")
            if "text-align" in style and "justify" in style:
                el["style"] = re.sub(r"text-align\s*:\s*justify", "text-align: left", style)
                self.fixes_applied.append("STR005: Changed justified text to left-aligned")

    def _fix_adjacent_duplicate_links(self, soup: BeautifulSoup):
        """Merge adjacent links with the same href."""
        links = soup.find_all("a")
        to_remove = []
        for i in range(len(links) - 1):
            if links[i] in to_remove:
                continue
            href1 = links[i].get("href", "")
            href2 = links[i + 1].get("href", "")
            if href1 and href1 == href2:
                # Merge text
                text1 = links[i].get_text(strip=True)
                text2 = links[i + 1].get_text(strip=True)
                if text1 != text2 and text2:
                    links[i].string = f"{text1} {text2}" if text1 else text2
                to_remove.append(links[i + 1])
                self.fixes_applied.append("LNK006: Merged adjacent duplicate link")
        for link in to_remove:
            link.decompose()

    def _fix_redundant_empty_links(self, soup: BeautifulSoup):
        """Remove redundant empty <a> tags (LNK011).

        Two cases, mirroring RedundantEmptyLinkRule.check:

        - Branch A: empty link has a visible-content companion to the
          same href elsewhere on the page → remove the empty one.
        - Branch B: 2+ empty links to the same href with no visible
          companion (mutual-empty case) → keep the first occurrence
          and remove the rest.
        """
        hrefs_with_visible: set[str] = set()
        empty_by_href: dict[str, list] = {}

        for link in soup.find_all("a"):
            href = (link.get("href") or "").strip()
            if not href or href.startswith("#"):
                continue

            text = link.get_text(strip=True)
            has_visible = bool(text)
            if not has_visible:
                for img in link.find_all("img"):
                    if (img.get("alt") or "").strip():
                        has_visible = True
                        break

            if has_visible:
                hrefs_with_visible.add(href)
            else:
                empty_by_href.setdefault(href, []).append(link)

        for href, empties in empty_by_href.items():
            if href in hrefs_with_visible:
                # Branch A: drop all the empty copies
                for link in empties:
                    link.decompose()
                    self.fixes_applied.append(
                        f"LNK011: Removed redundant empty link to {href[:60]}"
                    )
            elif len(empties) >= 2:
                # Branch B: keep the first, drop the rest
                for link in empties[1:]:
                    link.decompose()
                    self.fixes_applied.append(
                        f"LNK011: Removed duplicate empty link to {href[:60]}"
                    )

    def _fix_image_text_link_merge(self, soup: BeautifulSoup):
        """Merge adjacent image-link + text-link to same href (LNK012).

        Pattern: ``<a href="x"><img alt="icon"/></a><a href="x">filename</a>``.
        We keep the image link, set its img alt to the text-link's
        visible text (which is more descriptive than "icon"), and
        remove the text link.

        Mirrors ImageTextLinkMergeRule._find_image_text_link_pairs so
        detection and fix never disagree.
        """
        from lti_app.core.accessibility.rules.links import (
            _find_image_text_link_pairs,
        )

        for img_link, text_link in _find_image_text_link_pairs(soup):
            text = text_link.get_text(strip=True)
            img = img_link.find("img")
            if img is not None and text:
                # Replace the icon alt with the descriptive filename
                img["alt"] = text
            text_link.decompose()
            self.fixes_applied.append(
                f"LNK012: Merged image-link and text-link to "
                f"{(img_link.get('href') or '')[:60]}"
            )

    def _fix_fake_headings(self, soup: BeautifulSoup):
        """Convert bold paragraphs and large-font paragraphs to proper heading elements."""
        body = soup.find("body") or soup
        # Common decorative symbols that should not become headings
        _decorative_symbols = {"▼", "►", "▶", "◀", "▲", "●", "○", "■", "□", "★", "☆", "→", "←", "↑", "↓", "•"}

        for p in list(body.find_all("p")):
            if p.find_parent(["li", "td", "th", "ul", "ol", "table"]):
                continue
            text = p.get_text(strip=True)
            if not text or len(text) > 80:
                continue

            # Check for bold-wrapped paragraph
            children = [c for c in p.children if not (isinstance(c, str) and not c.strip())]
            is_all_bold = (
                len(children) == 1
                and hasattr(children[0], "name")
                and children[0].name in ("strong", "b")
            )

            # Check for large font-size in style
            style = p.get("style", "")
            has_large_font = bool(style) and self._has_heading_font_size(style)

            if not is_all_bold and not has_large_font:
                continue

            # Decorative symbol-only paragraphs: add aria-hidden instead
            if text.strip() in _decorative_symbols:
                p["aria-hidden"] = "true"
                self.fixes_applied.append("HDG004: Marked decorative symbol as aria-hidden")
                continue

            # Determine heading level from context
            prev_heading = p.find_previous(["h2", "h3", "h4", "h5"])
            level = 2
            if prev_heading:
                level = min(int(prev_heading.name[1]) + 1, 6)

            p.name = f"h{level}"
            # Remove the font-size style since it's now a heading
            if has_large_font and style:
                new_style = re.sub(r"font-size\s*:[^;]+;?\s*", "", style).strip()
                if new_style:
                    p["style"] = new_style
                elif "style" in p.attrs:
                    del p["style"]
            # Unwrap bold wrapper if present
            if is_all_bold:
                children[0].unwrap()
            self.fixes_applied.append(f"HDG004: Converted styled paragraph to <h{level}>")

    def _fix_no_heading_structure(self, soup: BeautifulSoup, page_title: str = ""):
        """Add page title as H2 at top of body when no headings exist."""
        from lti_app.core.accessibility.rules.headings import (
            _CANVAS_FILE_LINK_CLASSES,
        )

        body = soup.find("body") or soup

        # Skip if headings already exist
        if body.find(["h2", "h3", "h4", "h5", "h6"]):
            return

        # Inject the page title as H2 when the body has any of:
        #   - substantial text (> 50 chars)
        #   - embedded media (iframe / video / PPT-as-object)
        #   - Canvas Scribd / file embed link (Canvas injects the
        #     preview client-side; the static body is short but the
        #     rendered page has real content)
        # Truly empty bodies (no text, no embed, no file link) are
        # left alone — those are usually unpublished draft stubs.
        text = body.get_text(strip=True)
        has_embeds = bool(body.find(["iframe", "embed", "object", "video"]))
        has_canvas_file_link = bool(
            body.find("a", class_=_CANVAS_FILE_LINK_CLASSES)
        )
        if len(text) <= 50 and not has_embeds and not has_canvas_file_link:
            return

        if not page_title:
            return

        h2 = soup.new_tag("h2")
        h2.string = page_title
        # Insert at beginning of body
        if body.contents:
            body.contents[0].insert_before(h2)
        else:
            body.append(h2)
        self.fixes_applied.append(f"HDG005: Added page title as H2: {page_title[:50]}")

    def _fix_long_headings(self, soup: BeautifulSoup):
        """Truncate headings longer than 120 characters at word boundary."""
        body = soup.find("body") or soup
        max_chars = 120

        for heading in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
            text = heading.get_text(strip=True)
            if len(text) <= max_chars:
                continue

            # Find word boundary for truncation
            truncated = text[:max_chars]
            last_space = truncated.rfind(" ")
            if last_space > max_chars // 2:
                truncated = truncated[:last_space].rstrip()
            remainder = text[len(truncated):].strip()

            heading.string = truncated

            # Add remainder as a <p> after the heading
            if remainder:
                p = soup.new_tag("p")
                p.string = remainder
                heading.insert_after(p)

            self.fixes_applied.append(f"HDG006: Truncated heading from {len(text)} to {len(truncated)} chars")

    @staticmethod
    def _has_heading_font_size(style: str) -> bool:
        """Check if inline style has a font-size large enough to be a heading."""
        match = re.search(r"font-size\s*:\s*([\d.]+)\s*px", style)
        if match and float(match.group(1)) >= 20:
            return True
        match = re.search(r"font-size\s*:\s*([\d.]+)\s*(?:em|rem)", style)
        if match and float(match.group(1)) >= 1.3:
            return True
        match = re.search(r"font-size\s*:\s*([\d.]+)\s*pt", style)
        if match and float(match.group(1)) >= 14:
            return True
        match = re.search(r"font-size\s*:\s*([\d.]+)\s*%", style)
        if match and float(match.group(1)) >= 130:
            return True
        return False

    def _fix_autoplay(self, soup: BeautifulSoup):
        """Remove autoplay attribute from media elements."""
        for tag in soup.find_all(["video", "audio", "embed"]):
            if tag.has_attr("autoplay"):
                del tag["autoplay"]
                self.fixes_applied.append(f"MDA002: Removed autoplay from <{tag.name}>")

    # Non-descriptive link text patterns (mirrors LNK001 detection set)
    _NON_DESCRIPTIVE_LINK_TEXTS = {
        "click here", "click", "here", "link", "read more", "more",
        "learn more", "this", "this link", "this page", "continue",
        "go", "see more", "view", "view more", "details", "info",
        "information", "download",
    }

    _NON_DESCRIPTIVE_PREFIXES = (
        "click here", "click this", "click on", "click to", "click for",
        "tap here", "tap to", "go here", "read more", "learn more",
        "see more", "view more", "find out more", "more info",
    )

    _COMMON_DOMAIN_SUFFIXES = {
        "ac", "ai", "app", "ca", "co", "com", "edu", "gov", "info",
        "io", "ly", "mil", "net", "org", "tv", "uk", "us",
    }

    _UPPERCASE_URL_WORDS = {
        "api", "faq", "hr", "id", "it", "la", "lms", "lti",
        "tv", "ui", "url", "ux",
        # LACCD campus abbreviations
        "laccd", "elac", "lacc", "lahc", "lamc", "lapc",
        "lasc", "lattc", "lavc", "wlac",
    }

    def _fix_non_descriptive_links(self, soup: BeautifulSoup):
        """Fix links with non-descriptive text like 'Click Here' or 'Click Here for...'."""
        from urllib.parse import unquote, urlparse

        for link in list(soup.find_all("a")):
            text = link.get_text(strip=True).lower()
            is_exact = text in self._NON_DESCRIPTIVE_LINK_TEXTS
            is_prefix = any(text.startswith(p) for p in self._NON_DESCRIPTIVE_PREFIXES)
            if not is_exact and not is_prefix:
                continue

            href = link.get("href", "")
            original_text = link.get_text(strip=True)

            # Placeholder links (href="#") — unwrap to plain text
            if not href or href == "#":
                link.unwrap()
                self.fixes_applied.append("LNK001: Unwrapped broken placeholder link")
                continue

            # For "click here for X" patterns, extract the meaningful part
            if is_prefix and not is_exact:
                for prefix in self._NON_DESCRIPTIVE_PREFIXES:
                    if text.startswith(prefix):
                        remainder = original_text[len(prefix):].strip().strip("!.,;:")
                        if remainder and len(remainder) > 3:
                            link.string = remainder
                            self.fixes_applied.append(f"LNK001: Extracted '{remainder}' from '{original_text[:40]}'")
                            break
                else:
                    # Couldn't extract — fall through to URL-based fix
                    pass
                if link.string != original_text:
                    continue

            # Real URLs — derive descriptive text from URL path
            parsed = urlparse(href)
            path = unquote(parsed.path).rstrip("/")
            if path:
                segment = path.split("/")[-1]
                if "." in segment:
                    segment = segment.rsplit(".", 1)[0]
                descriptive = segment.replace("-", " ").replace("_", " ").strip()
                if descriptive and len(descriptive) > 2:
                    descriptive = descriptive[0].upper() + descriptive[1:]
                    link.string = descriptive
                    self.fixes_applied.append(f"LNK001: Replaced '{text[:30]}' with '{descriptive}'")
                    continue

            # Fallback: add aria-label from URL
            domain = parsed.netloc or href[:40]
            link["aria-label"] = f"Link to {domain}"
            self.fixes_applied.append(f"LNK001: Added aria-label for '{text[:30]}' link")

    def _fix_target_size(self, soup: BeautifulSoup):
        """Ensure interactive elements meet 24x24px minimum target size."""
        min_px = 24

        for element in soup.find_all(["a", "button"]):
            # Skip inline links in running text
            parent = element.parent
            if parent and parent.name in ("p", "span", "em", "strong", "b", "i", "li", "td", "th"):
                parent_text = parent.get_text(strip=True)
                element_text = element.get_text(strip=True)
                if len(parent_text) - len(element_text) > 5:
                    continue

            fixed = False

            # Fix image-only links with small images
            img = element.find("img")
            if img and not element.get_text(strip=True):
                w = img.get("width")
                h = img.get("height")
                try:
                    w_val = float(str(w).replace("px", "")) if w else None
                except (ValueError, TypeError):
                    w_val = None
                try:
                    h_val = float(str(h).replace("px", "")) if h else None
                except (ValueError, TypeError):
                    h_val = None

                if w_val is not None and w_val < min_px:
                    img["width"] = str(min_px)
                    fixed = True
                if h_val is not None and h_val < min_px:
                    img["height"] = str(min_px)
                    fixed = True

            # Fix explicit small dimensions in inline styles
            style = element.get("style", "")
            if style:
                new_style = style
                for prop in ("width", "height"):
                    match = re.search(rf"(?:^|;|\s){prop}\s*:\s*([\d.]+)\s*px", new_style)
                    if match and float(match.group(1)) < min_px:
                        new_style = re.sub(
                            rf"({prop}\s*:\s*)[\d.]+(\s*px)",
                            rf"\g<1>{min_px}\2",
                            new_style,
                        )
                        fixed = True
                if new_style != style:
                    element["style"] = new_style

            if fixed:
                self.fixes_applied.append("TGT001: Ensured minimum 24px target size")

    def _fix_broken_link_spaces(self, soup: BeautifulSoup):
        """URL-encode spaces in link hrefs."""
        for link in soup.find_all("a"):
            href = link.get("href", "")
            if " " in href and not href.startswith("#") and not href.startswith("mailto:"):
                link["href"] = href.replace(" ", "%20")
                self.fixes_applied.append("LNK008: Encoded spaces in link URL")

    def _fix_alt_text_too_long(self, soup: BeautifulSoup):
        """Truncate alt text exceeding the max character limit."""
        for img in soup.find_all("img"):
            alt = img.get("alt", "")
            if len(alt) > MAX_ALT_TEXT_CHARS:
                img["alt"] = truncate_alt_text(alt)
                self.fixes_applied.append("IMG004: Truncated long alt text")

    def _fix_suspicious_alt_text(self, soup: BeautifulSoup):
        """Strip leading/trailing 'image' / 'picture' noise from alt text.

        Mirrors SuspiciousAltTextRule (IMG009) and uses the shared
        _suspicious_alt_match helper so detection and fix never
        disagree.

        CLU-76 extension: when the WHOLE alt text is navigation noise
        like "(opens in new window)", `_suspicious_alt_match` returns
        `("navigation", "")`. We mark the image decorative
        (`alt="" role="presentation"`) so the IMG001 safety net at
        `_fix_remaining_missing_alt` doesn't re-add a filename-based
        fallback alt later in the pipeline.
        """
        from lti_app.core.accessibility.rules.images import (
            _suspicious_alt_match,
        )

        for img in soup.find_all("img"):
            alt = img.get("alt", "")
            if not alt:
                continue
            match = _suspicious_alt_match(alt)
            if match is None:
                continue
            position, cleaned = match
            img["alt"] = cleaned
            # Empty cleaned text → image is now decorative. Mark
            # role=presentation so IMG001's safety net leaves the
            # empty alt alone (otherwise it adds a filename fallback
            # like "X.gif" → "X").
            if not cleaned:
                img["role"] = "presentation"
            self.fixes_applied.append(
                f"IMG009: Stripped suspicious noise from alt text"
                f"{' (marked decorative)' if not cleaned else ''}"
            )

    def _fix_linked_image_alt(self, soup: BeautifulSoup):
        """Add alt text to images within links per PopeTech algorithm.

        The alt should describe the link destination/function, not just the image.
        Linked images must never have empty alt.
        """
        for link in soup.find_all("a", href=True):
            link_text = link.get_text(strip=True).replace("\u00a0", "").strip()
            if link_text:
                continue  # Link already has text content

            for img in link.find_all("img"):
                alt = (img.get("alt") or "").strip()
                if alt:
                    continue  # Already has meaningful alt

                # Priority order for generating alt:
                href = link.get("href", "")
                title = link.get("title", "")
                aria_label = link.get("aria-label", "")

                if title:
                    img["alt"] = title
                elif aria_label:
                    img["alt"] = aria_label
                elif href:
                    filename = href.split("/")[-1].split("?")[0].split("#")[0]
                    if filename and "." in filename:
                        name = filename.rsplit(".", 1)[0]
                        img["alt"] = name.replace("-", " ").replace("_", " ").strip().title() or "Link"
                    elif filename:
                        img["alt"] = filename.replace("-", " ").replace("_", " ").strip().title() or "Link"
                    else:
                        img["alt"] = "Link"
                else:
                    img["alt"] = "Link"

                self.fixes_applied.append(f"IMG005: Set linked image alt to '{img['alt'][:40]}'")

    def _fix_broken_same_page_links(self, soup: BeautifulSoup):
        """Unwrap broken same-page links (href='#' or href='#nonexistent-id')."""
        all_ids = {tag["id"] for tag in soup.find_all(id=True)}

        for link in list(soup.find_all("a")):
            href = link.get("href", "")
            if not href or not href.startswith("#"):
                continue
            fragment = href[1:]
            # Empty fragment or nonexistent target
            if not fragment or fragment not in all_ids:
                link.unwrap()
                self.fixes_applied.append(f"LNK009: Unwrapped broken anchor link ({href})")

    def _fix_broken_aria_references(self, soup: BeautifulSoup):
        """Remove ARIA attributes that reference nonexistent IDs."""
        all_ids = {tag["id"] for tag in soup.find_all(id=True)}
        aria_ref_attrs = ("aria-labelledby", "aria-describedby", "aria-controls", "aria-owns")

        for attr_name in aria_ref_attrs:
            for element in list(soup.find_all(attrs={attr_name: True})):
                if not isinstance(element, Tag):
                    continue
                ref_value = element.get(attr_name, "").strip()
                if not ref_value:
                    continue
                ref_ids = ref_value.split()
                valid_ids = [rid for rid in ref_ids if rid in all_ids]
                if len(valid_ids) == len(ref_ids):
                    continue  # All references valid
                if valid_ids:
                    element[attr_name] = " ".join(valid_ids)
                    self.fixes_applied.append(f"ARIA001: Removed broken refs from {attr_name}")
                else:
                    del element[attr_name]
                    self.fixes_applied.append(f"ARIA001: Removed {attr_name} with all broken refs")

    def _fix_duplicate_ids(self, soup: BeautifulSoup):
        """DID001: Rename duplicate id attributes so every id is unique.

        HTML spec: `id` must be unique within a document. When duplicates
        exist, browsers silently resolve references (href="#foo",
        aria-labelledby="foo", etc.) to the FIRST occurrence — so the
        first one keeps its name and later occurrences get a `-2`, `-3`…
        suffix. References are left alone: they continue to resolve to
        the first occurrence, which still has the original id. This
        avoids guessing which occurrence an ambiguous reference meant.

        Collision-safe: if `foo-2` is already in use for an unrelated
        element, the fixer skips to `foo-3`, etc.
        """
        # Collect every existing id — seed the "taken" set so we never
        # pick a suffix that would collide with something else.
        taken: set[str] = set()
        for tag in soup.find_all(id=True):
            id_val = tag.get("id", "")
            if id_val:
                taken.add(id_val)

        # Walk in document order; the first occurrence of each id
        # stays, every later occurrence gets a unique suffix.
        seen: set[str] = set()
        renamed = 0
        for tag in soup.find_all(id=True):
            id_val = tag.get("id", "")
            if not id_val:
                continue  # Empty ids — leave for authors to resolve
            if id_val not in seen:
                seen.add(id_val)
                continue
            # Duplicate: find the smallest unused suffix
            suffix = 2
            new_id = f"{id_val}-{suffix}"
            while new_id in taken:
                suffix += 1
                new_id = f"{id_val}-{suffix}"
            tag["id"] = new_id
            taken.add(new_id)
            seen.add(new_id)
            renamed += 1

        if renamed:
            self.fixes_applied.append(
                f"DID001: Renamed {renamed} duplicate ID{'s' if renamed != 1 else ''}"
            )

    def _fix_sparse_table_cells(self, soup: BeautifulSoup):
        """Fill empty table cells with &nbsp; for screen reader compatibility."""
        for table in soup.find_all("table"):
            if table.get("role") == "presentation":
                continue

            data_cells = table.find_all("td")
            if not data_cells:
                continue

            empty_count = 0
            for td in data_cells:
                text = td.get_text(strip=True).replace("\u00a0", "").strip()
                has_content_child = any(
                    isinstance(c, Tag) and c.name in ("img", "input", "select", "textarea")
                    for c in td.descendants if isinstance(c, Tag)
                )
                if not text and not has_content_child:
                    td.string = "\u00a0"
                    empty_count += 1

            if empty_count > 0:
                # Add worksheet label if mostly empty
                total = len(data_cells)
                if empty_count > total * 0.6 and not table.get("aria-label"):
                    table["aria-label"] = "Worksheet table"
                self.fixes_applied.append(f"TBL006: Filled {empty_count} empty cells with nbsp")

    def _fix_duplicate_link_text(self, soup: BeautifulSoup):
        """Disambiguate links with identical text pointing to different destinations."""
        # Build map: text_lower -> list of (link, href)
        text_to_links: dict[str, list[tuple[Tag, str]]] = {}
        for link in soup.find_all("a"):
            text = link.get_text(strip=True)
            href = link.get("href", "")
            if not text or not href:
                continue
            key = text.lower()
            if key not in text_to_links:
                text_to_links[key] = []
            text_to_links[key].append((link, href))

        for text_lower, entries in text_to_links.items():
            # Only process if same text maps to different hrefs
            unique_hrefs = set(href for _, href in entries)
            if len(unique_hrefs) <= 1:
                continue

            # Skip the first link, disambiguate the rest. The previous
            # implementation tracked "first occurrence of THIS href",
            # which — when every link had a unique href — treated every
            # entry as a first occurrence and fixed nothing. Here we
            # simply keep the 0th link canonical and append a context
            # suffix to the 1st, 2nd, ... Nth.
            for i, (link, href) in enumerate(entries):
                if i == 0:
                    continue

                # Derive suffix from href. Prefer fragment, then last
                # meaningful path segment, then hostname, then ordinal.
                parsed = urlparse(href)
                suffix = ""
                if parsed.fragment:
                    suffix = f" ({parsed.fragment})"
                elif parsed.path:
                    segment = unquote(parsed.path).rstrip("/").split("/")[-1]
                    if "." in segment:
                        segment = segment.rsplit(".", 1)[0]
                    if segment:
                        suffix = f" ({segment})"
                if not suffix and parsed.hostname:
                    suffix = f" ({parsed.hostname})"
                if not suffix:
                    suffix = f" ({i + 1})"

                original_text = link.get_text(strip=True)
                link.string = original_text + suffix
                self.fixes_applied.append(
                    f"LNK010: Disambiguated '{original_text[:30]}' link"
                )

    def _fix_raw_url_link_text(self, soup: BeautifulSoup):
        """Replace raw URL link text with a deterministic label derived from the URL."""
        for link in soup.find_all("a"):
            text = link.get_text(" ", strip=True)
            if not self._looks_like_url(text):
                continue

            replacement = self._build_link_label_from_url(text)
            if not replacement:
                replacement = self._build_link_label_from_url(link.get("href", ""))

            if not replacement or replacement.lower() == text.lower():
                continue

            link.clear()
            link.append(replacement)
            self.fixes_applied.append(
                f"LNK005: Replaced raw URL text '{text[:40]}' with '{replacement[:40]}'"
            )

    @staticmethod
    def _looks_like_url(text: str) -> bool:
        """Check if text looks like a URL."""
        text = text.lower()
        return any(indicator in text for indicator in ("http://", "https://", "www."))

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Normalize bare URLs so urlparse handles them consistently."""
        url = re.sub(r"\s*\(opens in new window\)\s*$", "", (url or "").strip(), flags=re.IGNORECASE)
        if not url:
            return ""
        if url.startswith("//"):
            return f"https:{url}"
        if "://" not in url:
            return f"https://{url}"
        return url

    def _build_link_label_from_url(self, url: str) -> str:
        """Build readable link text from a URL's domain and optional path."""
        normalized_url = self._normalize_url(url)
        if not normalized_url:
            return ""

        parsed = urlparse(normalized_url)
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return ""

        if hostname.startswith("www."):
            hostname = hostname[4:]

        host_parts = [part for part in hostname.split(".") if part]
        while len(host_parts) > 1 and host_parts[-1] in self._COMMON_DOMAIN_SUFFIXES:
            host_parts.pop()

        domain_words = self._url_parts_to_words(host_parts)

        path_segments = [segment for segment in unquote(parsed.path).split("/") if segment]
        if path_segments:
            last_segment = path_segments[-1]
            if "." in last_segment:
                last_segment = last_segment.rsplit(".", 1)[0]
            path_words = self._url_parts_to_words([last_segment])
            if path_words:
                return " ".join(domain_words + path_words).strip()

        if domain_words:
            return " ".join(domain_words).strip()

        return hostname

    def _url_parts_to_words(self, parts: list[str]) -> list[str]:
        """Convert host/path parts into human-readable words."""
        words: list[str] = []
        for part in parts:
            for token in re.split(r"[-_]+", part):
                if not token:
                    continue
                words.extend(self._format_url_token(token))
        return words

    def _format_url_token(self, token: str) -> list[str]:
        """Format a URL token, preserving common acronyms and campus names."""
        # Handle compound campus names like "laharborcollege", "lamissioncollege"
        compound_match = re.fullmatch(
            r"([a-z]{2,3})(mission|harbor|valley|pierce|southwest|trade|tech)"
            r"(college|campus)?",
            token,
        )
        if compound_match:
            parts = [compound_match.group(1).upper(), compound_match.group(2).title()]
            if compound_match.group(3):
                parts.append(compound_match.group(3).title())
            return parts

        parts = re.findall(r"[a-zA-Z]+|\d+", token)
        if not parts:
            return [token]

        formatted: list[str] = []
        for part in parts:
            lower = part.lower()
            if lower in self._UPPERCASE_URL_WORDS:
                formatted.append(lower.upper())
            else:
                formatted.append(part.title())
        return formatted

    def _fix_video_iframe_titles(self, soup: BeautifulSoup):
        """Add title attributes to video iframes that are missing them.

        Tries to fetch the actual video title via oEmbed API for YouTube/Vimeo.
        Falls back to 'Embedded video from {platform}' if oEmbed fails.
        """
        video_hosts = re.compile(
            r"youtube|youtu\.be|vimeo|kaltura|panopto|wistia"
            r"|instructuremedia|arc\.instructure|canvastudio"
            r"|echo360|mediasite|media\.instructure",
            re.IGNORECASE,
        )
        oembed_cache: dict[str, Optional[str]] = {}
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src", "")
            if not video_hosts.search(src):
                continue
            if iframe.get("title") or iframe.get("aria-label"):
                continue

            platform = self._get_video_platform_name(src)
            if src not in oembed_cache:
                oembed_cache[src] = self._fetch_video_title(src)
            video_title = oembed_cache[src]

            if video_title:
                iframe["title"] = video_title
                self.fixes_applied.append(f"MDA001: Added video title to {platform} iframe")
            else:
                iframe["title"] = f"Embedded video from {platform}"
                self.fixes_applied.append(f"MDA001: Added title to {platform} iframe")

    @staticmethod
    def _get_video_platform_name(src: str) -> str:
        """Derive a human-readable platform name from an iframe URL."""
        src_lower = src.lower()
        if any(p in src_lower for p in ("instructuremedia", "arc.instructure", "media.instructure", "canvastudio")):
            return "Canvas Studio"

        platform_names = (
            ("youtube", "YouTube"), ("youtu.be", "YouTube"),
            ("vimeo", "Vimeo"), ("kaltura", "Kaltura"),
            ("panopto", "Panopto"), ("wistia", "Wistia"),
            ("echo360", "Echo360"), ("mediasite", "Mediasite"),
        )
        for token, name in platform_names:
            if token in src_lower:
                return name
        return "video platform"

    def _fetch_video_title(self, src: str) -> Optional[str]:
        """Fetch a video title via oEmbed when the host supports it."""
        import httpx

        normalized_src = self._normalize_url(src)
        if not normalized_src:
            return None

        src_lower = normalized_src.lower()
        if "youtube" in src_lower or "youtu.be" in src_lower:
            # Convert embed URL to watch URL for oEmbed
            video_id = ""
            if "/embed/" in normalized_src:
                video_id = normalized_src.split("/embed/")[-1].split("?")[0]
            elif "youtu.be/" in normalized_src:
                video_id = normalized_src.split("youtu.be/")[-1].split("?")[0]
            if not video_id:
                return None
            endpoint = "https://www.youtube.com/oembed"
            params = {"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"}
        elif "vimeo" in src_lower:
            video_id = normalized_src.split("/")[-1].split("?")[0]
            if not video_id or not video_id.isdigit():
                return None
            endpoint = "https://vimeo.com/api/oembed.json"
            params = {"url": f"https://vimeo.com/{video_id}"}
        else:
            return None

        try:
            response = httpx.get(endpoint, params=params, timeout=5.0)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            logger.debug("Failed to fetch oEmbed title for %s: %s", src, exc)
            return None

        title = str(payload.get("title", "")).strip()
        return title or None

    def _strip_alt_text_prefixes(self, soup: BeautifulSoup):
        """Strip redundant prefixes like 'Image of' from alt text."""
        for img in soup.find_all("img"):
            alt = img.get("alt", "")
            if not alt:
                continue
            alt_lower = alt.lower()
            for prefix in GENERIC_ALT_PREFIXES:
                if alt_lower.startswith(prefix):
                    remainder = alt[len(prefix):].lstrip(" ,:-")
                    if remainder:
                        img["alt"] = remainder[0].upper() + remainder[1:]
                        self.fixes_applied.append(f"IMG: Stripped '{prefix}' prefix from alt text")
                    break

    def _fix_inherited_white_text(self, soup: BeautifulSoup):
        """Fix white-on-white issues caused by CSS inheritance.

        Joshua templates use 'color: white; background-color: #xxx' on parent
        containers.  Child text elements (p, em, strong, h3 etc.) inherit the
        white color but don't have an explicit background-color, so Canvas's
        accessibility checker flags them as white-on-white (1:1 ratio).

        Fix: for every element with an explicit near-white color AND a
        background-color, push the background-color down to each direct child
        block that contains text but lacks its own background.  Also, if an
        element has a white color with no background anywhere up the tree,
        strip the color entirely.
        """
        for parent in soup.find_all(style=True):
            if not isinstance(parent, Tag):
                continue
            style = parent.get("style", "")
            fg_hex = self._extract_inline_color(style, "color")
            bg_hex = self._extract_inline_color(style, "background-color")

            if not fg_hex or not bg_hex:
                continue

            fg_rgb = hex_to_rgb(fg_hex)
            if not fg_rgb:
                continue

            # Only care about very light foreground colors (near-white)
            if not all(c > 230 for c in fg_rgb):
                continue

            # Parent has white text + background-color.  Push background-color
            # to child text elements so they are self-contained for Canvas checker.
            for child in parent.find_all(["p", "em", "strong", "span", "h2", "h3", "h4", "h5", "h6", "li", "a"]):
                if not child.get_text(strip=True):
                    continue
                child_style = child.get("style", "")
                child_bg = self._extract_inline_color(child_style, "background-color") if child_style else None
                child_fg = self._extract_inline_color(child_style, "color") if child_style else None

                # Child has no explicit background — it would inherit page white
                # from Canvas's perspective.  Add the parent's background.
                if not child_bg:
                    if child_style:
                        child["style"] = child_style.rstrip("; ") + f"; background-color: {bg_hex}; color: {fg_hex};"
                    else:
                        child["style"] = f"background-color: {bg_hex}; color: {fg_hex};"

        # Second pass: remove orphaned white colors with no background anywhere
        for element in soup.find_all(style=True):
            if not isinstance(element, Tag):
                continue
            style = element.get("style", "")
            fg_hex = self._extract_inline_color(style, "color")
            bg_hex = self._extract_inline_color(style, "background-color")

            if not fg_hex or bg_hex:
                continue  # Has background or no foreground — skip

            fg_rgb = hex_to_rgb(fg_hex)
            if not fg_rgb or not all(c > 230 for c in fg_rgb):
                continue  # Not near-white — skip

            # Check ancestors for a background-color
            has_ancestor_bg = False
            ancestor = element.parent
            while ancestor and hasattr(ancestor, "get"):
                anc_style = ancestor.get("style", "")
                if anc_style and self._extract_inline_color(anc_style, "background-color"):
                    has_ancestor_bg = True
                    break
                ancestor = ancestor.parent

            if not has_ancestor_bg:
                # White text with no background anywhere — remove the color
                new_style = self._replace_inline_color(style, "color", "")
                # Clean up empty property
                new_style = re.sub(r"color\s*:\s*;?\s*", "", new_style).strip().strip(";").strip()
                if new_style:
                    element["style"] = new_style
                elif "style" in element.attrs:
                    del element["style"]
                self.fixes_applied.append("CLR001: Removed orphaned white text color")

    def _deduplicate_alt_texts(self, soup: BeautifulSoup):
        """Differentiate duplicate alt text on the same page."""
        seen: dict[str, int] = {}
        for img in soup.find_all("img"):
            alt = img.get("alt", "")
            if not alt:
                continue
            if alt in seen:
                seen[alt] += 1
                img["alt"] = f"{alt} ({seen[alt]})"
                self.fixes_applied.append("IMG003: Deduplicated nearby alt text")
            else:
                seen[alt] = 1

    def _apply_alt_texts(self, soup: BeautifulSoup, alt_texts: dict[str, str]):
        """Apply generated alt texts to images."""
        for img in soup.find_all("img"):
            src = img.get("src", "")
            if not src:
                continue

            # Exact match first (keys are now image src values)
            if src in alt_texts:
                img["alt"] = alt_texts[src]
                self.fixes_applied.append("IMG: Set AI-generated alt text")
                continue

            # Fallback: substring match for path variations
            for key, alt_text in alt_texts.items():
                if key in src or src in key:
                    img["alt"] = alt_text
                    self.fixes_applied.append("IMG: Set AI-generated alt text")
                    break

    def _fix_remaining_missing_alt(self, soup: BeautifulSoup):
        """Ensure ALL images have a non-empty alt attribute or role=presentation.

        IMG001 fires in TWO cases (see core/accessibility/rules/images.py):
          1. Image has no alt attribute at all
          2. Image has alt="" but no role="presentation"

        This safety net handles both. Real alt text (alt="something") is left
        alone — that's what the AI alt-text pipeline produces. Empty alt
        without an explicit decorative role gets the same filename-derived
        fallback as missing-alt images, OR is marked role=presentation if
        the image looks structurally decorative (1px spacers, etc.) — see
        CLU-66.
        """
        for img in soup.find_all("img"):
            # Has real (non-empty) alt text → leave alone
            if img.has_attr("alt") and img.get("alt", "").strip():
                continue
            # Empty alt with explicit decorative role → leave alone
            if (
                img.has_attr("alt")
                and img.get("alt", "").strip() == ""
                and img.get("role", "") == "presentation"
            ):
                continue

            src = img.get("src", "")

            # Equation images — descriptive alt
            if "/equation_images/" in src:
                img["alt"] = "Mathematical equation"
                self.fixes_applied.append("IMG001: Added alt for equation image")
                continue

            # Spacer/decorative images (1x1 px, small dimensions)
            width = img.get("width", "")
            height = img.get("height", "")
            if str(width) in ("1", "0") or str(height) in ("1", "0"):
                img["alt"] = ""
                # role=presentation is required so IMG001 doesn't re-fire on
                # the empty alt (rules/images.py case 2). CLU-66.
                img["role"] = "presentation"
                self.fixes_applied.append("IMG001: Marked spacer as decorative")
                continue

            # Inside a link — IMG005 handles linked images, but ensure alt exists
            parent_link = img.find_parent("a")
            if parent_link:
                continue  # IMG005 should have handled this

            # Generate alt from filename as last resort
            applied_label = ""
            if src:
                filename = src.split("/")[-1].split("?")[0]
                if filename and "." in filename:
                    name = filename.rsplit(".", 1)[0].replace("-", " ").replace("_", " ").strip()
                    img["alt"] = name.title() if name else "Image"
                    applied_label = img["alt"][:30]
                else:
                    # Unknown filename — decorative role to satisfy IMG001
                    img["alt"] = ""
                    img["role"] = "presentation"
                    applied_label = "(decorative)"
            else:
                # No src — decorative role to satisfy IMG001
                img["alt"] = ""
                img["role"] = "presentation"
                applied_label = "(decorative)"

            self.fixes_applied.append(f"IMG001: Added fallback alt='{applied_label}'")

        # Image map areas: <area> without alt
        for area in soup.find_all("area"):
            if area.has_attr("alt"):
                continue
            href = area.get("href", "")
            area["alt"] = href.split("/")[-1] if href else "Map area"
            self.fixes_applied.append("IMG001: Added alt to image map area")

    def _clean_common_issues(self, soup: BeautifulSoup):
        """Clean up common accessibility issues."""
        # Strip redundant alt text prefixes (runs on every page)
        self._strip_alt_text_prefixes(soup)

        # Propagate white/near-white color from parents to children so contrast
        # checks work on the text element level (Canvas checker evaluates each
        # element independently, not via inheritance).  If a parent has
        # color: white + background-color, push both down to text children so
        # they are self-contained.  If color: white exists without any
        # background-color on the element OR any ancestor, remove it entirely
        # (it would be invisible white-on-white).
        self._fix_inherited_white_text(soup)

        # NOTE: Empty link handling removed — now done in _fix_empty_links()
        # with visually hidden spans per PopeTech's recommended pattern.

        # Add aria-hidden to decorative icons and fix large font-size on them
        _decorative_chars = {"▼", "►", "▶", "◀", "▲", "●", "○", "■", "□", "★", "☆", "→", "←", "↑", "↓", "•"}
        for element in soup.find_all(["span", "p", "div"]):
            text = element.get_text(strip=True)
            if text in _decorative_chars:
                element["aria-hidden"] = "true"
                # Remove large font-size that triggers "possible heading" alerts
                style = element.get("style", "")
                if style and re.search(r"font-size\s*:", style):
                    new_style = re.sub(r"font-size\s*:[^;]+;?\s*", "", style).strip()
                    if new_style:
                        element["style"] = new_style
                    elif "style" in element.attrs:
                        del element["style"]

        # Fix empty alt attributes (not the same as no alt)
        for img in soup.find_all("img"):
            alt = img.get("alt", None)
            if alt == "":
                # Empty alt should have role="presentation" for decorative
                if not img.get("role"):
                    # Check if it looks decorative
                    src = img.get("src", "").lower()
                    if any(
                        word in src
                        for word in ["spacer", "divider", "separator", "blank"]
                    ):
                        img["role"] = "presentation"

    def apply_color_scheme(self, html: str) -> str:
        """Apply color scheme to existing styled elements.

        Args:
            html: HTML content.

        Returns:
            HTML with updated colors.
        """
        if not self.colors:
            return html

        soup = BeautifulSoup(html, "html.parser")

        # Update background colors in styles
        for elem in soup.find_all(style=True):
            style = elem["style"]

            # Replace common background colors with primary
            style = re.sub(
                r"background-color:\s*#[0-9A-Fa-f]{6}",
                f"background-color: {self.colors.primary}",
                style,
            )

            # Replace border colors with secondary
            style = re.sub(
                r"border-color:\s*#[0-9A-Fa-f]{6}",
                f"border-color: {self.colors.secondary}",
                style,
            )

            elem["style"] = style

        return str(soup)

    def _fix_focus_obscured(self, soup: BeautifulSoup):
        """Add scroll-margin-top to elements following fixed/sticky positioned elements.

        Elements with position:fixed or position:sticky (e.g. sticky headers) can
        obscure the focus indicator of keyboard-navigable content below them.
        Adding scroll-margin-top: 80px to subsequent sibling/child elements ensures
        the browser scrolls them into view past the fixed element.
        """
        body = soup.find("body") or soup
        fixed_found = False

        for el in soup.find_all(style=True):
            if not isinstance(el, Tag):
                continue
            style = el.get("style", "").lower()
            if "position" in style and ("fixed" in style or "sticky" in style):
                fixed_found = True
                break

        if not fixed_found:
            return

        # Add scroll-margin-top to all focusable elements so they scroll
        # into view past the fixed header when focused via keyboard.
        focusable_selectors = ["a", "button", "input", "select", "textarea", "details", "summary"]
        for tag_name in focusable_selectors:
            for el in body.find_all(tag_name):
                if not isinstance(el, Tag):
                    continue
                existing_style = el.get("style", "")
                if "scroll-margin-top" not in existing_style:
                    if existing_style:
                        el["style"] = existing_style.rstrip("; ") + "; scroll-margin-top: 80px;"
                    else:
                        el["style"] = "scroll-margin-top: 80px;"
                    self.fixes_applied.append("FOC001: Added scroll-margin-top to focusable element")

    def _fix_accessible_authentication(self, soup: BeautifulSoup):
        """Add autocomplete attribute to password inputs for accessible authentication."""
        for inp in soup.find_all("input"):
            if not isinstance(inp, Tag):
                continue
            if inp.get("type", "").lower() != "password":
                continue
            autocomplete = inp.get("autocomplete", "").strip().lower()
            valid = {"current-password", "new-password"}
            if autocomplete not in valid:
                # Heuristic: if there's another password field in the same form
                # (e.g. confirm password), mark the first as current-password and
                # others as new-password. Default to current-password.
                inp["autocomplete"] = "current-password"
                self.fixes_applied.append("AUTH001: Added autocomplete to password input")

    def _fix_empty_buttons(self, soup: BeautifulSoup):
        """Fix empty buttons per PopeTech algorithm.

        For <button> with image: fix the image alt (PopeTech example pattern).
        For <input type=submit/button/reset>: add value attribute.
        """
        for button in soup.find_all("button"):
            if not isinstance(button, Tag):
                continue
            text = button.get_text(strip=True).replace("\u00a0", "").strip()
            if text or button.get("aria-label", "").strip():
                continue
            if button.get("aria-labelledby", "").strip():
                continue
            if any(img.get("alt", "").strip() for img in button.find_all("img")):
                continue

            # If button contains image without alt, fix the image
            img = button.find("img")
            if img and not img.get("alt", "").strip():
                btn_type = button.get("type", "button")
                img["alt"] = btn_type.title()
                self.fixes_applied.append("BTN001: Added alt to image inside empty button")
                continue

            # Otherwise add aria-label
            btn_type = button.get("type", "button")
            button["aria-label"] = btn_type.title()
            self.fixes_applied.append(f"BTN001: Added aria-label '{btn_type.title()}' to empty button")

        for inp in soup.find_all("input", type=["submit", "button", "reset"]):
            if not isinstance(inp, Tag):
                continue
            if inp.get("value", "").strip() or inp.get("aria-label", "").strip():
                continue
            inp_type = inp.get("type", "submit")
            inp["value"] = inp_type.title()
            self.fixes_applied.append(f"BTN001: Added value '{inp_type.title()}' to empty input")

    def get_fixes_applied(self) -> list[str]:
        """Get list of fixes that were applied."""
        return self.fixes_applied
