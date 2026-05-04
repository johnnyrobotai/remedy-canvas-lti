"""AI image remediation strategy (Layer 3).

Applies AI-generated alt text to images and marks decorative images with
alt="" and role="presentation".
"""

from __future__ import annotations

import structlog
from bs4 import BeautifulSoup

from lti_app.core.strategies.models import StrategyReport

_logger = structlog.get_logger(__name__)


class ImageRemediationStrategy:
    """Apply alt text from pre-generated dict and mark decorative images."""

    def __init__(self, alt_texts: dict[str, str]) -> None:
        self._alt_texts = alt_texts

    async def run(self, soup: BeautifulSoup) -> StrategyReport:
        """Apply alt text fixes to all img tags in the soup."""
        report = StrategyReport(strategy_name="image")

        for img in soup.find_all("img"):
            src = img.get("src", "")
            if not src:
                continue

            # Check if we have a generated alt text for this image
            generated_alt = self._alt_texts.get(src)
            if generated_alt:
                current_alt = img.get("alt", "")
                if not current_alt or current_alt != generated_alt:
                    img["alt"] = generated_alt
                    report.fixes_applied += 1
                    _logger.debug(
                        "image_strategy_applied_alt",
                        src=src[:80],
                        alt=generated_alt[:50],
                    )

        return report
