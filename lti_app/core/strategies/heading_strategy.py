"""AI heading remediation strategy (Layer 3).

Uses LLM to generate descriptive heading text when headings are empty or generic.
"""

from __future__ import annotations

import structlog
from bs4 import BeautifulSoup, Tag

from lti_app.core.ai.prompt_library import get_heading_generation_prompt
from lti_app.core.ai.vision_client import VisionClient
from lti_app.core.strategies.models import StrategyReport

_logger = structlog.get_logger(__name__)

_GENERIC_HEADINGS = {
    "", "heading", "title", "section", "untitled", "header",
    "section title", "new section", "overview",
}


def _is_empty_or_generic(tag: Tag) -> bool:
    """Check if a heading tag has empty or generic text."""
    text = tag.get_text(strip=True).lower()
    return text in _GENERIC_HEADINGS


def _get_surrounding_text(tag: Tag, max_chars: int = 500) -> str:
    """Extract text from siblings following the heading."""
    parts: list[str] = []
    total = 0
    sibling = tag.next_sibling
    while sibling and total < max_chars:
        if hasattr(sibling, "get_text"):
            text = sibling.get_text(strip=True)
        else:
            text = str(sibling).strip()
        if text:
            parts.append(text)
            total += len(text)
        sibling = sibling.next_sibling
    return " ".join(parts)[:max_chars]


class HeadingRemediationStrategy:
    """Generate descriptive heading text for empty/generic headings."""

    def __init__(self, vision_client: VisionClient) -> None:
        self._client = vision_client

    async def run(self, soup: BeautifulSoup) -> StrategyReport:
        report = StrategyReport(strategy_name="heading")

        for level in range(2, 7):
            for heading in soup.find_all(f"h{level}"):
                if not _is_empty_or_generic(heading):
                    continue

                surrounding = _get_surrounding_text(heading)
                if not surrounding:
                    continue

                try:
                    prompt = get_heading_generation_prompt(surrounding, level)
                    model = self._client.get_primary_model()
                    generated = await self._client.chat(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    generated = generated.strip().strip("\"'")
                    if generated and len(generated) <= 80:
                        heading.string = generated
                        report.fixes_applied += 1
                        _logger.debug(
                            "heading_strategy_applied",
                            level=level,
                            text=generated[:50],
                        )
                except Exception as exc:
                    report.errors.append(f"h{level}: {exc}")
                    _logger.warning(
                        "heading_strategy_error",
                        level=level,
                        error=str(exc),
                    )

        return report
