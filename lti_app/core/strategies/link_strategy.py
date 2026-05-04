"""AI link text remediation strategy (Layer 3).

Uses LLM to expand non-descriptive link text ("click here", "read more", etc.)
using surrounding paragraph context.
"""

from __future__ import annotations

import structlog
from bs4 import BeautifulSoup, Tag

from lti_app.core.ai.prompt_library import get_link_expansion_prompt
from lti_app.core.ai.vision_client import VisionClient
from lti_app.core.strategies.models import StrategyReport

_logger = structlog.get_logger(__name__)

_NONDESCRIPTIVE_LINKS = {
    "click here", "here", "read more", "more", "link", "this link",
    "learn more", "click", "go", "go here", "see more", "view",
    "details", "more info", "more information", "info",
}


def _is_nondescriptive(tag: Tag) -> bool:
    """Check if a link has non-descriptive text."""
    text = tag.get_text(strip=True).lower()
    return text in _NONDESCRIPTIVE_LINKS


def _get_parent_text(tag: Tag, max_chars: int = 300) -> str:
    """Get surrounding paragraph/container text for context."""
    parent = tag.parent
    if parent and hasattr(parent, "get_text"):
        return parent.get_text(strip=True)[:max_chars]
    return ""


class LinkRemediationStrategy:
    """Expand non-descriptive link text using LLM."""

    def __init__(self, vision_client: VisionClient) -> None:
        self._client = vision_client

    async def run(self, soup: BeautifulSoup) -> StrategyReport:
        report = StrategyReport(strategy_name="link")

        for link in soup.find_all("a"):
            if not _is_nondescriptive(link):
                continue

            href = link.get("href", "")
            if not href:
                continue

            link_text = link.get_text(strip=True)
            surrounding = _get_parent_text(link)

            try:
                prompt = get_link_expansion_prompt(link_text, surrounding, href)
                model = self._client.get_primary_model()
                generated = await self._client.chat(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                )
                generated = generated.strip().strip("\"'")
                if generated and len(generated) <= 80 and generated.lower() not in _NONDESCRIPTIVE_LINKS:
                    link.string = generated
                    report.fixes_applied += 1
                    _logger.debug(
                        "link_strategy_applied",
                        original=link_text,
                        replacement=generated[:50],
                    )
            except Exception as exc:
                report.errors.append(f"link '{link_text}': {exc}")
                _logger.warning(
                    "link_strategy_error",
                    link_text=link_text,
                    error=str(exc),
                )

        return report
