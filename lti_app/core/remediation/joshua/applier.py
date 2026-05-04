"""Apply Joshua templates to course pages."""

import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

from lti_app.models import Campus, ColorScheme, CoursePage, TemplateType
from lti_app.core.remediation.joshua.templates import JoshuaTemplates
from lti_app.core.remediation.joshua.color_schemes import ColorSchemes

logger = logging.getLogger(__name__)


class TemplateApplier:
    """Detect page types and apply appropriate Joshua templates."""

    # Keywords for detecting page types
    PAGE_TYPE_KEYWORDS = {
        TemplateType.FRONT_PAGE: [
            "welcome", "front page", "home", "course overview",
            "start here", "course information"
        ],
        TemplateType.MEET_INSTRUCTOR: [
            "meet your instructor", "about your instructor",
            "instructor information", "about me", "instructor bio"
        ],
        TemplateType.MODULE_OVERVIEW: [
            "week", "module", "unit", "overview", "objectives",
            "learning objectives", "what you will do"
        ],
        TemplateType.ASSIGNMENT: [
            "assignment", "homework", "submit", "essay", "paper",
            "project", "due date", "submission"
        ],
        TemplateType.DISCUSSION: [
            "discussion", "forum", "post", "reply", "peer response",
            "discussion board", "respond to"
        ],
        TemplateType.QUIZ: [
            "quiz", "exam", "assessment", "attempts"
        ],
        TemplateType.ANNOUNCEMENT: [
            "announcement", "update", "reminder", "notice"
        ],
    }

    def __init__(self, campus: Campus, custom_colors: Optional[ColorScheme] = None):
        """Initialize the template applier.

        Args:
            campus: Campus for color scheme selection.
            custom_colors: Optional custom color scheme.
        """
        self.campus = campus

        if campus == Campus.CUSTOM and custom_colors:
            self.colors = custom_colors
        else:
            self.colors = ColorSchemes.get_scheme(campus)

    def detect_page_type(self, page: CoursePage) -> TemplateType:
        """Detect the appropriate template type for a page.

        Only matches keywords in the page title to avoid false positives
        from body text (e.g. "home" appearing in an abstract about DACA).

        Args:
            page: CoursePage to analyze.

        Returns:
            Detected TemplateType.
        """
        title_lower = page.title.lower()

        # Check each template type's keywords against title only
        scores = {}

        for template_type, keywords in self.PAGE_TYPE_KEYWORDS.items():
            score = 0

            for keyword in keywords:
                if keyword in title_lower:
                    score += 3

            scores[template_type] = score

        # Find highest scoring type — require at least one title keyword match
        best_match = max(scores.items(), key=lambda x: x[1])

        if best_match[1] >= 3:
            logger.info(f"Detected page type: {best_match[0].value} for '{page.title}'")
            return best_match[0]

        # Default to general content
        return TemplateType.GENERAL_CONTENT

    def apply_template(self, page: CoursePage) -> str:
        """Apply the appropriate template to a page.

        If a specialized template extraction leaves bracketed placeholders
        (e.g. [Course Title], [Instructor Name]), falls back to
        GENERAL_CONTENT which preserves all original page content.

        Args:
            page: CoursePage to transform.

        Returns:
            Transformed HTML string.
        """
        template_type = self.detect_page_type(page)
        page.detected_template = template_type

        # Extract content from existing page
        content_data = self._extract_content(page, template_type)

        # If specialized extraction left placeholders or mostly empty content,
        # fall back to GENERAL_CONTENT which preserves all original HTML
        if template_type != TemplateType.GENERAL_CONTENT:
            placeholder_count = sum(
                1 for v in content_data.values()
                if isinstance(v, str) and re.search(r'\[.+?\]', v)
            )
            # Count content fields that are empty (excluding title-like fields)
            title_keys = {"title", "topic_name", "assignment_title", "discussion_title"}
            empty_count = sum(
                1 for k, v in content_data.items()
                if k not in title_keys and isinstance(v, str) and not v.strip()
            )
            total_content_fields = sum(
                1 for k in content_data if k not in title_keys
            )
            should_fallback = (
                placeholder_count >= 2
                or (total_content_fields > 0 and empty_count >= total_content_fields * 0.6)
            )
            if should_fallback:
                reason = (
                    f"{placeholder_count} placeholders" if placeholder_count >= 2
                    else f"{empty_count}/{total_content_fields} empty fields"
                )
                logger.info(
                    f"Falling back to GENERAL_CONTENT for '{page.title}' "
                    f"({reason} in {template_type.value})"
                )
                soup = BeautifulSoup(page.html_content, "html.parser")
                body = soup.find("body") or soup
                template_type = TemplateType.GENERAL_CONTENT
                content_data = {
                    "title": page.title,
                    "content_sections": self._extract_sections(body),
                }
                page.detected_template = template_type

        # Render template with extracted content
        html = JoshuaTemplates.render(
            template_type=template_type,
            primary=self.colors.primary,
            secondary=self.colors.secondary,
            **content_data,
        )

        return html

    def _extract_content(
        self, page: CoursePage, template_type: TemplateType
    ) -> dict:
        """Extract content from page for template variables.

        Args:
            page: Source CoursePage.
            template_type: Target template type.

        Returns:
            Dictionary of template variables.
        """
        soup = BeautifulSoup(page.html_content, "html.parser")
        body = soup.find("body") or soup

        # Common extraction
        data = {
            "title": page.title,
        }

        if template_type == TemplateType.GENERAL_CONTENT:
            data["content_sections"] = self._extract_sections(body)

        elif template_type == TemplateType.MODULE_OVERVIEW:
            data.update(self._extract_module_overview(body, page.title))

        elif template_type == TemplateType.FRONT_PAGE:
            data.update(self._extract_front_page(body))

        elif template_type == TemplateType.MEET_INSTRUCTOR:
            data.update(self._extract_instructor_info(body))

        elif template_type == TemplateType.ASSIGNMENT:
            data.update(self._extract_assignment(body, page.title))

        elif template_type == TemplateType.DISCUSSION:
            data.update(self._extract_discussion(body, page.title))

        elif template_type == TemplateType.QUIZ:
            data.update(self._extract_quiz(body))

        elif template_type == TemplateType.ANNOUNCEMENT:
            data["content_sections"] = self._extract_sections(body)

        return data

    def _extract_sections(self, body: BeautifulSoup) -> str:
        """Extract content as sections with headings.

        Preserves ALL content including text nodes, images, and links.
        When headings are direct children of body, groups content between them.
        When headings are nested (e.g. inside tables), wraps all body content
        in a single section to avoid losing content in other branches.
        When no headings exist, wraps all body content in a single section.
        """
        headings = body.find_all(["h2", "h3", "h4"])

        # Check if headings are direct children of body (safe to section)
        # vs nested inside tables/divs (not safe — sibling iteration misses content)
        direct_headings = [h for h in headings if h.parent == body]

        if direct_headings:
            sections = []

            # Capture content BEFORE the first heading
            pre_heading_parts = []
            for child in body.children:
                if child in direct_headings:
                    break
                pre_heading_parts.append(str(child))
            pre_html = "".join(pre_heading_parts).strip()
            if pre_html:
                section = JoshuaTemplates.CONTENT_SECTION.safe_substitute(
                    primary=self.colors.primary,
                    secondary=self.colors.secondary,
                    heading="Content",
                    body=pre_html,
                )
                sections.append(section)

            for heading in direct_headings:
                # Extract images/media from inside the heading tag.
                # Canvas exports sometimes place <img> tags inside headings.
                rich_elements = heading.find_all(["img", "video", "iframe", "audio"])
                rich_html_parts = [str(el) for el in rich_elements]

                heading_text = heading.get_text(strip=True)

                # If heading had no text, use alt text from first image or fallback
                if not heading_text and rich_elements:
                    first_alt = rich_elements[0].get("alt", "").strip()
                    heading_text = first_alt if first_alt else "Content"
                elif not heading_text:
                    heading_text = "Content"

                # Collect content until next heading
                content_parts = []
                for sibling in heading.find_next_siblings():
                    if sibling.name in ["h2", "h3", "h4"]:
                        break
                    content_parts.append(str(sibling))

                # Prepend rich content extracted from the heading itself
                if rich_html_parts:
                    content_parts = rich_html_parts + content_parts

                if content_parts:
                    section = JoshuaTemplates.CONTENT_SECTION.safe_substitute(
                        primary=self.colors.primary,
                        secondary=self.colors.secondary,
                        heading=heading_text,
                        body="\n".join(content_parts),
                    )
                    sections.append(section)

            if sections:
                return "\n".join(sections)

        # Headings are nested or no usable sections found — wrap all body content
        body_html = "".join(str(child) for child in body.children)
        if not body_html.strip():
            body_html = "<p><em>No content available.</em></p>"
        return JoshuaTemplates.CONTENT_SECTION.safe_substitute(
            primary=self.colors.primary,
            secondary=self.colors.secondary,
            heading="Content",
            body=body_html,
        )

    def _extract_module_overview(self, body: BeautifulSoup, title: str) -> dict:
        """Extract module overview content."""
        data = {
            "topic_name": title,
            "overview": "",
            "objectives": "",
            "readings": "",
            "videos": "",
            "assignments": "",
        }

        # Look for overview text
        overview_heading = body.find(
            ["h2", "h3"], string=re.compile(r"overview", re.I)
        )
        if overview_heading:
            next_elem = overview_heading.find_next_sibling("p")
            if next_elem:
                data["overview"] = str(next_elem)

        # Look for objectives list
        objectives_heading = body.find(
            ["h2", "h3"], string=re.compile(r"objective", re.I)
        )
        if objectives_heading:
            ul = objectives_heading.find_next_sibling("ul")
            if ul:
                data["objectives"] = "\n".join(
                    f"                    <li>{li.get_text(strip=True)}</li>"
                    for li in ul.find_all("li")
                )

        # Extract lists for readings, videos, assignments
        for ul in body.find_all("ul"):
            items = [li.get_text(strip=True) for li in ul.find_all("li")]
            items_html = "\n".join(f"                    <li>{item}</li>" for item in items)

            # Guess category from context
            prev_text = ""
            prev = ul.find_previous_sibling()
            if prev:
                prev_text = prev.get_text(strip=True).lower()

            if "read" in prev_text:
                data["readings"] = items_html
            elif "watch" in prev_text or "video" in prev_text:
                data["videos"] = items_html
            elif "assignment" in prev_text or "complete" in prev_text:
                data["assignments"] = items_html

        return data

    def _extract_front_page(self, body: BeautifulSoup) -> dict:
        """Extract front page content."""
        return {
            "course_title": "[Course Title]",
            "course_name": "[Course Name]",
            "banner_image": "",
            "location": "[Location]",
            "meeting_time": "[Meeting Time]",
            "instructor_name": "[Instructor Name]",
            "office_hours": "[Office Hours]",
            "office_location": "[Office Location]",
            "online_hours": "[Online Hours]",
            "zoom_link": "[Zoom Link]",
            "course_description": body.get_text(strip=True)[:500],
            "slo_items": "                    <li>[Learning Outcome 1]</li>",
        }

    def _extract_instructor_info(self, body: BeautifulSoup) -> dict:
        """Extract instructor information."""
        return {
            "instructor_name": "[Instructor Name]",
            "instructor_image": "",
            "introduction": body.get_text(strip=True)[:300],
            "fun_facts": "                    <li>[Fun Fact 1]</li>\n                    <li>[Fun Fact 2]</li>\n                    <li>[Fun Fact 3]</li>",
            "office_hours": "[Office Hours]",
            "office_location": "[Office Location]",
            "online_hours": "[Online Hours]",
            "zoom_link": "[Zoom Link]",
            "contact_method": "[Contact Method]",
            "response_time": "[Response Time]",
        }

    def _extract_assignment(self, body: BeautifulSoup, title: str) -> dict:
        """Extract assignment content."""
        return {
            "assignment_title": title,
            "description": body.get_text(strip=True)[:500],
            "instructions": "",
            "due_date": "[Due Date]",
            "length_requirement": "[Length Requirement]",
            "formatting": "[Formatting Style]",
            "grading_info": "View the assignment rubric for more details.",
        }

    def _extract_discussion(self, body: BeautifulSoup, title: str) -> dict:
        """Extract discussion content."""
        # Try to find numbered questions
        questions = []
        for ol in body.find_all("ol"):
            for li in ol.find_all("li"):
                questions.append(f"                    <li>{li.get_text(strip=True)}</li>")

        return {
            "discussion_title": title,
            "topic_overview": body.get_text(strip=True)[:300],
            "questions": "\n".join(questions) if questions else "                    <li>[Discussion Question]</li>",
            "first_due": "[First Response Due Date]",
            "peer_due": "[Peer Response Due Date]",
        }

    def _extract_quiz(self, body: BeautifulSoup) -> dict:
        """Extract quiz content."""
        return {
            "goal_purpose": "".join(str(child) for child in body.children),
            "attempts": "[Number]",
            "timed_status": "untimed",
        }

    def wrap_with_joshua_styling(self, html: str) -> str:
        """Wrap existing HTML with basic Joshua styling.

        Use this when full template application isn't appropriate
        but we still want consistent colors.

        Args:
            html: HTML content to wrap.

        Returns:
            HTML wrapped with Joshua container.
        """
        return f'''<div style="padding: 20px; border-radius: 5px;">
    <div style="padding: 20px; border-radius: 5px;">
        {html}
    </div>
</div>'''

    def inject_callout_box(self, html: str, title: str, content: str) -> str:
        """Wrap content in a callout box using campus colors.

        Args:
            html: Existing HTML to append the callout to.
            title: Callout box title.
            content: Callout box body content.

        Returns:
            HTML with callout box appended.
        """
        callout = JoshuaTemplates.render_component(
            "CALLOUT_BOX",
            primary=self.colors.primary,
            secondary=self.colors.secondary,
            title=title,
            content=content,
        )
        return html + "\n" + callout

    def wrap_with_alternating_blocks(self, html: str) -> str:
        """Detect sections in HTML and alternate white/gray backgrounds.

        Args:
            html: HTML content with sections delimited by headings.

        Returns:
            HTML with sections wrapped in alternating-color blocks.
        """
        soup = BeautifulSoup(html, "html.parser")
        body = soup.find("body") or soup
        headings = body.find_all(["h2", "h3"])
        direct_headings = [h for h in headings if h.parent == body]

        if len(direct_headings) < 2:
            return html

        sections: list[list[str]] = []
        current_section: list[str] = []

        for child in body.children:
            if child in direct_headings and current_section:
                sections.append(current_section)
                current_section = []
            current_section.append(str(child))

        if current_section:
            sections.append(current_section)

        result_parts = []
        for i, section_parts in enumerate(sections):
            content = "".join(section_parts)
            template_name = "ALTERNATING_BLOCK_WHITE" if i % 2 == 0 else "ALTERNATING_BLOCK_GRAY"
            result_parts.append(
                JoshuaTemplates.render_component(
                    template_name,
                    primary=self.colors.primary,
                    secondary=self.colors.secondary,
                    content=content,
                )
            )

        return "\n".join(result_parts)

    def wrap_with_banner(self, html: str, title: str) -> str:
        """Add a campus-colored banner header above existing content.

        Args:
            html: Existing HTML content.
            title: Banner title text.

        Returns:
            HTML with banner prepended.
        """
        banner = JoshuaTemplates.render_component(
            "BANNER",
            primary=self.colors.primary,
            secondary=self.colors.secondary,
            title=title,
        )
        return banner + "\n" + html
