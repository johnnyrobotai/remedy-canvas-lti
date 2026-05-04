"""Accessibility rules for tables (WCAG 1.3.1 Info and Relationships)."""

from bs4 import BeautifulSoup, Tag

from lti_app.models import AccessibilityIssue, IssueCategory, IssueSeverity
from lti_app.core.accessibility.rules.base import AccessibilityRule


def is_layout_table(table: Tag) -> bool:
    """Detect a layout table using conservative heuristics.

    Returns True only when the table is clearly being used for visual
    layout rather than tabular data. False means "treat as a data
    table" (the existing behavior) — a safe default that preserves
    remediation for legitimate data tables.

    CLU-84: added after the psych-001 OZY newsletter page
    `complexities-and-roots-of-suicide` got 63 fake `<th>` and 63
    `"Data table"` captions injected into its nested HTML-email
    layout. The fixer couldn't tell data tables from layout tables
    and cascaded: TBL001 fabricated `<th>` elements in layout
    tables, which then tripped TBL003's "data table has <th> so
    caption it" branch, which in turn fell through to the
    meaningless `"Data table"` fallback caption.

    Signal order (earliest return wins):

    1. Explicit opt-out via ``role="presentation"``/``role="none"``
       → layout (definitive).
    2. Strong data-table signals (``<caption>``, ``<thead>``,
       ``<tfoot>``, ``summary`` attribute, or any ``<th>`` with
       non-empty text) → NOT layout. Authors who added these
       clearly intended the table as data.
    3. Nesting — either contains another ``<table>`` descendant OR
       is itself nested inside another ``<table>``. Nested tables
       are almost always HTML-email layout artefacts; real data
       tables are not nested. → layout.
    4. Default: NOT layout. Flat tables without strong signals stay
       in the existing fix path so TBL001 can promote first-row
       cells to ``<th>`` the way it does today.
    """
    # 1. Explicit opt-out
    if table.get("role") in ("presentation", "none"):
        return True

    # 2. Strong data-table signals → not layout
    if table.find("caption"):
        return False
    if table.find("thead") or table.find("tfoot"):
        return False
    if table.get("summary"):
        return False
    for th in table.find_all("th"):
        if th.get_text(strip=True):
            return False

    # 3. Nesting heuristic
    if table.find("table"):
        return True
    if table.find_parent("table") is not None:
        return True

    # 4. Conservative default
    return False


class MissingTableHeadersRule(AccessibilityRule):
    """TBL001: Check for tables missing header cells."""

    rule_id = "TBL001"
    severity = IssueSeverity.ERROR
    category = IssueCategory.TABLES
    wcag_criterion = "1.3.1"
    message_template = "Table missing header cells"
    can_auto_fix = True
    fix_description = "Convert first row cells to <th> elements"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find data tables without <th> elements.

        Layout tables are skipped (CLU-84) so the fixer doesn't
        fabricate header cells in HTML-email layout wrappers.
        """
        issues = []

        for table in soup.find_all("table"):
            # CLU-84: skip layout tables so TBL001 only flags real
            # data tables. Without this, TBL001 would cascade into
            # TBL003 via the transformer's fabricated <th> elements.
            if is_layout_table(table):
                continue

            # Check if table has any th elements
            th_cells = table.find_all("th")

            if not th_cells:
                # Get preview of table content
                first_row = table.find("tr")
                preview = ""
                if first_row:
                    cells = first_row.find_all(["td", "th"])
                    preview = ", ".join(c.get_text(strip=True)[:15] for c in cells[:3])

                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f'Table missing header cells. First row: "{preview}..."',
                        element_html=str(table)[:300],
                    )
                )

        return issues


class MissingScopeAttributeRule(AccessibilityRule):
    """TBL002: Check for table headers missing scope attribute."""

    rule_id = "TBL002"
    severity = IssueSeverity.WARNING
    category = IssueCategory.TABLES
    wcag_criterion = "1.3.1"
    message_template = "Table header missing scope attribute"
    can_auto_fix = True
    fix_description = "Add scope='col' or scope='row' to header cells"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find <th> elements without scope attribute."""
        issues = []

        for table in soup.find_all("table"):
            for th in table.find_all("th"):
                if not th.has_attr("scope"):
                    text = th.get_text(strip=True)[:30]
                    issues.append(
                        self.create_issue(
                            page_id=page_id,
                            message=f'Table header "{text}" missing scope attribute',
                            element_html=str(th),
                        )
                    )

        return issues


class MissingTableCaptionRule(AccessibilityRule):
    """TBL003: Check for tables missing caption (PopeTech heuristic)."""

    rule_id = "TBL003"
    severity = IssueSeverity.WARNING
    category = IssueCategory.TABLES
    wcag_criterion = "1.3.1"
    message_template = "Table missing caption"
    can_auto_fix = True
    fix_description = "Add a descriptive caption to the table"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Find data tables without <caption> elements.

        PopeTech heuristic: a data table (has at least one <th>) without a
        <caption> that has either:
          1. A colspan >= 3 on the first cell, or
          2. A <p> immediately before the table with < 50 chars, or
             < 100 chars and bold/centered.

        CLU-84: skip layout tables AND require an existing ``<th>``
        before flagging. The previous main loop only skipped
        ``role="presentation"`` tables and then flagged EVERY
        remaining table, ignoring the "must be a data table (has
        ``<th>``)" promise in this very docstring. Combined with
        TBL001's fabricated ``<th>`` elements, that cascade injected
        63 fake "Data table" captions into the psych-001 OZY
        newsletter page.
        """
        issues = []

        for table in soup.find_all("table"):
            # CLU-84: skip layout tables (role=presentation, nested,
            # or otherwise structurally indistinguishable from email
            # layout wrappers).
            if is_layout_table(table):
                continue
            # Skip tables that already have a caption
            if table.find("caption"):
                continue
            # CLU-84: enforce the data-table requirement the docstring
            # already promises. Layout tables and tables that TBL001
            # could still fix shouldn't be flagged for missing caption.
            if not table.find("th"):
                continue

            possible = self._detect_possible_caption(table)
            if possible:
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f'Table has possible caption that should be a <caption>: "{possible[:50]}"',
                        element_html=str(table)[:300],
                    )
                )
            else:
                # Still flag data tables without caption even when no
                # possible caption text is detected
                first_row = table.find("tr")
                preview = ""
                if first_row:
                    cells = first_row.find_all(["td", "th"])
                    preview = ", ".join(c.get_text(strip=True)[:15] for c in cells[:3])

                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message=f'Table missing caption. Content preview: "{preview}..."',
                        element_html=str(table)[:300],
                    )
                )

        return issues

    @staticmethod
    def _detect_possible_caption(table: Tag) -> str | None:
        """PopeTech possible-table-caption heuristic."""
        # Must be a data table (has <th>)
        if not table.find("th"):
            return None
        # Must not already have a caption
        if table.find("caption"):
            return None

        # Check 1: First cell has colspan >= 3
        first_row = table.find("tr")
        if first_row:
            first_cell = first_row.find(["td", "th"])
            if first_cell:
                try:
                    if int(first_cell.get("colspan", "1")) >= 3:
                        return first_cell.get_text(strip=True)
                except ValueError:
                    pass

        # Check 2: <p> immediately before table
        prev = table.find_previous_sibling()
        if prev and prev.name == "p":
            text = prev.get_text(strip=True)
            if not text:
                return None
            is_bold = bool(
                prev.find(["b", "strong"])
                or "font-weight" in prev.get("style", "")
            )
            is_centered = (
                "center" in prev.get("style", "")
                or "center" in " ".join(prev.get("class", []))
            )

            if len(text) < 50:
                return text
            if len(text) < 100 and (is_bold or is_centered):
                return text

        return None


class EmptyTableHeaderRule(AccessibilityRule):
    """TBL004: Check for empty table header cells."""

    rule_id = "TBL004"
    severity = IssueSeverity.ERROR
    category = IssueCategory.TABLES
    wcag_criterion = "1.3.1"
    message_template = "Table header cell is empty"
    can_auto_fix = True
    fix_description = "Convert empty header to data cell or add descriptive text"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        """Detect empty table headers per PopeTech algorithm.

        A <th> is empty ONLY if it has no text AND no images with alt text.
        """
        issues = []

        for table in soup.find_all("table"):
            # Skip layout tables
            if table.get("role") == "presentation":
                continue

            for th in table.find_all("th"):
                if th.get_text(strip=True):
                    continue  # Has text — not empty

                # Check for images with alt text
                imgs_with_alt = [img for img in th.find_all("img") if img.get("alt", "").strip()]
                if imgs_with_alt:
                    continue  # Has image with alt — not empty per PopeTech

                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message="Table header cell is empty",
                        element_html=str(th)[:200],
                    )
                )

        return issues


class LayoutTableRule(AccessibilityRule):
    """TBL005: Detect tables used for layout rather than data."""

    rule_id = "TBL005"
    severity = IssueSeverity.WARNING
    category = IssueCategory.TABLES
    wcag_criterion = "1.3.1"
    message_template = "Table appears to be used for layout"
    can_auto_fix = True
    fix_description = "Add role='presentation' to layout tables"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        for table in soup.find_all("table"):
            if table.get("role") == "presentation":
                continue
            if table.find("caption"):
                continue
            if table.find("th"):
                continue

            rows = table.find_all("tr")
            if len(rows) <= 1:
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message="Single-row table may be used for layout",
                        element_html=str(table)[:300],
                    )
                )
                continue

            # Check if single column
            all_single = all(
                len(row.find_all(["td", "th"])) <= 1
                for row in rows
            )
            if all_single:
                issues.append(
                    self.create_issue(
                        page_id=page_id,
                        message="Single-column table may be used for layout",
                        element_html=str(table)[:300],
                    )
                )

        return issues


class SparseTableRule(AccessibilityRule):
    """TBL006: Detect data tables with many empty cells."""

    rule_id = "TBL006"
    severity = IssueSeverity.WARNING
    category = IssueCategory.TABLES
    wcag_criterion = "1.3.1"
    message_template = "Data table has empty cells that may confuse screen readers"
    can_auto_fix = True
    fix_description = "Fill empty cells with non-breaking space for screen reader compatibility"

    def check(self, soup: BeautifulSoup, page_id: str) -> list[AccessibilityIssue]:
        issues = []
        for table in soup.find_all("table"):
            if table.get("role") == "presentation":
                continue

            data_cells = table.find_all("td")
            if not data_cells:
                continue

            empty_count = sum(1 for td in data_cells if self._is_empty_cell(td))
            if empty_count == 0:
                continue

            total = len(data_cells)
            caption = table.find("caption")
            caption_text = caption.get_text(strip=True)[:30] if caption else "unnamed"

            issues.append(
                self.create_issue(
                    page_id=page_id,
                    message=f'Table "{caption_text}" has {empty_count}/{total} empty cells',
                    element_html=str(table)[:300],
                )
            )

        return issues

    @staticmethod
    def _is_empty_cell(td: Tag) -> bool:
        """Check if a table cell is completely empty.

        A cell containing only ``&nbsp;`` (U+00A0 NO-BREAK SPACE) is
        *not* considered empty — that's the marker the TBL006 fixer
        writes to intentionally-blank cells. Treating it as empty
        makes the fixer non-idempotent: every re-scan of a remediated
        worksheet table would re-flag every cell the fixer just
        filled, and TBL006 would never clear from the ACR.

        Implementation note: ``get_text(strip=True)`` and Python's
        ``str.strip()`` both treat U+00A0 as whitespace (via
        ``isspace()``), so we strip only the ASCII whitespace set
        explicitly and keep nbsp as content.
        """
        text = td.get_text().strip(" \t\n\r\v\f")
        if text:
            return False
        # Check for any child elements with content (img, input, etc.)
        for child in td.descendants:
            if isinstance(child, Tag) and child.name in ("img", "input", "select", "textarea"):
                return False
        return True
