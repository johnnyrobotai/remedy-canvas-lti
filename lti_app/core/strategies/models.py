"""Data models for Layer 3 strategy results."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StrategyReport:
    """Result of running a single strategy on a page."""

    strategy_name: str
    fixes_applied: int = 0
    errors: list[str] = field(default_factory=list)
