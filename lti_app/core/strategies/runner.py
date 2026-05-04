"""Strategy runner orchestrator for Layer 3 AI remediation."""

from __future__ import annotations

import structlog
from bs4 import BeautifulSoup

from lti_app.core.ai.vision_client import VisionClient
from lti_app.core.strategies.heading_strategy import HeadingRemediationStrategy
from lti_app.core.strategies.image_strategy import ImageRemediationStrategy
from lti_app.core.strategies.link_strategy import LinkRemediationStrategy
from lti_app.core.strategies.models import StrategyReport

_logger = structlog.get_logger(__name__)


class StrategyRunner:
    """Run Layer 3 AI strategies in sequence on an HTML document."""

    def __init__(
        self,
        vision_client: VisionClient,
        alt_texts: dict[str, str] | None = None,
    ) -> None:
        self._vision_client = vision_client
        self._alt_texts = alt_texts or {}

    async def run(self, html: str) -> tuple[str, list[StrategyReport]]:
        """Run all strategies and return (modified_html, reports).

        Strategies run in order: Image -> Heading -> Link.
        Per-strategy errors do not stop the pipeline.
        """
        soup = BeautifulSoup(html, "html.parser")
        reports: list[StrategyReport] = []

        strategies = [
            ImageRemediationStrategy(self._alt_texts),
            HeadingRemediationStrategy(self._vision_client),
            LinkRemediationStrategy(self._vision_client),
        ]

        for strategy in strategies:
            try:
                report = await strategy.run(soup)
                reports.append(report)
                _logger.info(
                    "strategy_completed",
                    strategy=report.strategy_name,
                    fixes=report.fixes_applied,
                    errors=len(report.errors),
                )
            except Exception as exc:
                name = getattr(strategy, "__class__", type(strategy)).__name__
                reports.append(StrategyReport(
                    strategy_name=name,
                    errors=[str(exc)],
                ))
                _logger.error(
                    "strategy_failed",
                    strategy=name,
                    error=str(exc),
                )

        return str(soup), reports
