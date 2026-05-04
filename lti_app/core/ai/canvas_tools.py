"""OpenAI-compatible function/tool schemas for LLM-driven Canvas remediation."""

REMEDIATION_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "fix_html_content",
            "description": (
                "Return the accessibility-fixed HTML for a Canvas content item. "
                "Apply all WCAG 2.1 AA fixes: heading hierarchy, table headers/scope, "
                "image alt text, link text, color contrast, list structure, lang attributes. "
                "Preserve all existing content and styling. Only modify what is needed for accessibility."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fixed_html": {
                        "type": "string",
                        "description": "The complete corrected HTML with all accessibility issues resolved",
                    },
                    "changes_made": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of specific changes applied (e.g., 'Added scope=col to table headers')",
                    },
                },
                "required": ["fixed_html", "changes_made"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_alt_text",
            "description": (
                "Generate descriptive alt text for an image based on its context. "
                "Alt text should be concise (under 125 characters), descriptive, and "
                "convey the meaning/purpose of the image in its context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "image_src": {
                        "type": "string",
                        "description": "The image source URL",
                    },
                    "alt_text": {
                        "type": "string",
                        "description": "Concise, descriptive alt text for the image",
                    },
                    "is_decorative": {
                        "type": "boolean",
                        "description": "True if the image is purely decorative and should have empty alt text",
                    },
                },
                "required": ["image_src", "alt_text"],
            },
        },
    },
]

REMEDIATION_SYSTEM_PROMPT = """You are an expert web accessibility remediation tool for Canvas LMS course content.

Your job is to fix HTML content to meet WCAG 2.1 AA standards. You will receive HTML and a list of accessibility issues.

Rules:
1. Fix ALL listed issues. Do not skip any.
2. Preserve ALL existing content, links, images, and styling. Only change what is needed for accessibility.
3. For images missing alt text: generate descriptive alt text based on the image filename, surrounding context, and page title. If the image is purely decorative, use alt="".
4. For heading issues: fix the hierarchy (h1 → h2 → h3, no skipping levels). Generate descriptive text for empty headings based on surrounding content.
5. For table issues: add scope="col" to header cells, add <caption> if missing, ensure proper <thead>/<tbody> structure.
6. For link issues: replace "click here" / "read more" with descriptive link text. Never change the href.
7. For list issues: convert fake lists (lines starting with - or * or numbers) to proper <ul>/<ol> HTML lists.
8. For contrast issues: adjust text color to meet 4.5:1 ratio for normal text, 3:1 for large text. Prefer darker text on light backgrounds.
9. Return the COMPLETE fixed HTML, not just the changed parts.
10. Use the fix_html_content tool to return your result.

You are working on Canvas LMS content. Canvas strips certain HTML tags and attributes. Avoid:
- <script>, <style>, <iframe> (unless already present)
- Custom data-* attributes
- Inline JavaScript (onclick, onload, etc.)
"""
