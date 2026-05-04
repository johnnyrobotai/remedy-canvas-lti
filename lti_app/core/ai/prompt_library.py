"""Prompt library for AI-assisted accessibility remediation.

Separates prompt content from API interaction logic so prompts can evolve
independently.
"""


def get_alt_text_generation_prompt(context: str = "") -> str:
    """Prompt for generating image alt text."""
    prompt = (
        "Describe what you physically see in this image in under 100 characters (99 max).\n"
        "Rules:\n"
        "- Describe the visible objects, not surrounding page text\n"
        "- Do not start with 'image of', 'photo of', or 'picture of'\n"
        "- Do not add quotes or explanation\n"
        "- Prefer specific educational concepts when visible"
    )
    if context:
        prompt += (
            f"\nContext: The image appears in {context}. "
            "Use that only if it clarifies the visible concept."
        )
    return prompt


def get_alt_text_judge_prompt(
    candidates: list[dict],
    context: str = "",
) -> str:
    """Prompt for judging alt text candidates and selecting the best."""
    import json

    return (
        "You are choosing the strongest alt text for accessibility.\n"
        "Prefer concise, specific, plain-language descriptions of the visible image.\n"
        "Reject candidates that sound generic, vague, or like filenames.\n"
        "Return strictly valid JSON with keys: text, model, confidence.\n"
        "confidence should be a float between 0.0 and 1.0.\n"
        f"Context: {context or 'No additional page context.'}\n"
        f"Candidates: {json.dumps(candidates)}"
    )


def get_heading_generation_prompt(
    surrounding_text: str = "",
    heading_level: int = 2,
) -> str:
    """Prompt for generating descriptive heading text."""
    return (
        f"Generate a concise, descriptive heading (H{heading_level}) for this content section.\n"
        "Rules:\n"
        "- Maximum 60 characters\n"
        "- Use plain language, no jargon\n"
        "- Describe what the section is about, not how it looks\n"
        "- Do not add quotes around the heading text\n"
        "- Return only the heading text, nothing else\n"
        f"\nContent:\n{surrounding_text[:500]}"
    )


def get_link_expansion_prompt(
    link_text: str,
    surrounding_text: str = "",
    href: str = "",
) -> str:
    """Prompt for expanding non-descriptive link text."""
    return (
        "Rewrite this link text to be descriptive and accessible.\n"
        "Rules:\n"
        "- Maximum 60 characters\n"
        "- Describe what the link leads to or does\n"
        "- Do not use 'click here', 'read more', 'link to', or 'go to'\n"
        "- Return only the new link text, nothing else\n"
        f"\nCurrent link text: \"{link_text}\"\n"
        f"Link URL: {href}\n"
        f"Surrounding context: {surrounding_text[:300]}"
    )


# ---------------------------------------------------------------------------
# Module-level constants (aliases for backwards compatibility and direct import)
# ---------------------------------------------------------------------------

ALT_TEXT_SYSTEM_PROMPT: str = (
    "You are an accessibility expert generating concise, descriptive alt text for images "
    "in educational course materials. Your descriptions help screen reader users understand "
    "what sighted users see. Always be specific, factual, and brief."
)

ALT_TEXT_USER_PROMPT_TEMPLATE: str = (
    "Describe what you physically see in this image in under 100 characters (99 max).\n"
    "Rules:\n"
    "- Describe the visible objects, not surrounding page text\n"
    "- Do not start with 'image of', 'photo of', or 'picture of'\n"
    "- Do not add quotes or explanation\n"
    "- Prefer specific educational concepts when visible\n"
    "{context}"
)

ALT_TEXT_JUDGE_PROMPT_TEMPLATE: str = (
    "You are choosing the strongest alt text for accessibility.\n"
    "Prefer concise, specific, plain-language descriptions of the visible image.\n"
    "Reject candidates that sound generic, vague, or like filenames.\n"
    "Return strictly valid JSON with keys: text, model, confidence.\n"
    "confidence should be a float between 0.0 and 1.0.\n"
    "Candidates: {candidates}"
)

HEADING_GENERATION_PROMPT: str = (
    "Generate a concise, descriptive heading for this content section.\n"
    "Rules:\n"
    "- Maximum 60 characters\n"
    "- Use plain language, no jargon\n"
    "- Describe what the section is about, not how it looks\n"
    "- Do not add quotes around the heading text\n"
    "- Return only the heading text, nothing else"
)

LINK_EXPANSION_PROMPT: str = (
    "Rewrite this link text to be descriptive and accessible.\n"
    "Rules:\n"
    "- Maximum 60 characters\n"
    "- Describe what the link leads to or does\n"
    "- Do not use 'click here', 'read more', 'link to', or 'go to'\n"
    "- Return only the new link text, nothing else"
)

DOCUMENT_TO_HTML_SYSTEM_PROMPT: str = (
    "You are an accessibility expert converting structured document text into "
    "accessible HTML fragments for Canvas LMS wiki pages. "
    "Produce only valid HTML — no markdown, no code fences, no preamble.\n\n"
    "Follow WCAG 2.2 AA strictly:\n"
    "- Use h2 through h6 for headings. Never emit h1 — Canvas owns the page title.\n"
    "- Use <ul> and <ol> for lists.\n"
    "- Use <table> with <th scope=\"col\"> for column headers and <th scope=\"row\"> for row headers. "
    "Never use <table> for layout.\n"
    "- Use <strong> for bold and <em> for italic. Never use <b> or <i>.\n"
    "- Use <p> for body paragraphs.\n"
    "- For image references in the source, emit "
    "<img alt=\"[describe image based on context]\" src=\"\"> and leave src empty — "
    "alt text is filled in by a separate pipeline.\n"
    "- Preserve the reading order from the input. The input lines arrive in the "
    "order they were laid out on the page.\n"
    "- Do not emit inline styles, font tags, color attributes, or deprecated elements."
)


def get_document_to_html_prompt(
    structured_text: str,
    heading_map: str = "",
    page_num: int = 0,
    total_pages: int = 1,
) -> str:
    """Prompt for converting structured document text to accessible HTML."""
    page_context = (
        f"Page {page_num + 1} of {total_pages}."
        if total_pages > 1
        else "Single-page document."
    )
    heading_section = (
        f"\nFont size → heading level mapping:\n{heading_map}\n"
        if heading_map
        else ""
    )
    return (
        f"{page_context}"
        f"{heading_section}"
        "\nConvert the following structured text to accessible HTML. "
        "Each line may be prefixed with [H2], [H3], etc. to indicate heading level. "
        "Unprefixed lines are body text. "
        "Return only the HTML fragment — no surrounding tags, no explanation.\n\n"
        f"{structured_text}"
    )
