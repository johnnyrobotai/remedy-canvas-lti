"""VPAT 2.5 WCAG 2.2 criteria definitions and utilities.

Loads criteria from vpat_criteria.json and provides mapping utilities
for Remedy Canvas LTI rule IDs to WCAG criteria.
"""

import json
from pathlib import Path
from typing import Optional

current_dir = Path(__file__).parent

# Load VPAT criteria data
with open(current_dir / "vpat_criteria.json", "r") as f:
    _VPAT_DATA = json.load(f)

# Build lookup dictionaries
_CRITERIA_BY_ID = {c["id"]: c for c in _VPAT_DATA["criteria"]}
_RULE_TO_CRITERION = _VPAT_DATA["rule_mapping"]


def get_criterion(criterion_id: str) -> Optional[dict]:
    """Get a WCAG criterion by ID (e.g., '1.1.1')."""
    return _CRITERIA_BY_ID.get(criterion_id)


def get_all_criteria(level: Optional[str] = None) -> list[dict]:
    """Get all WCAG criteria, optionally filtered by level ('A' or 'AA')."""
    if level:
        return [c for c in _VPAT_DATA["criteria"] if c["level"] == level]
    return _VPAT_DATA["criteria"]


def get_aa_criteria() -> list[dict]:
    """Get all Level A and AA criteria for VPAT reporting."""
    return [c for c in _VPAT_DATA["criteria"] if c["level"] in ("A", "AA")]


def map_rule_to_criterion(rule_id: str) -> Optional[str]:
    """Map a Remedy Canvas LTI rule ID to its WCAG criterion ID."""
    return _RULE_TO_CRITERION.get(rule_id)


def get_rules_for_criterion(criterion_id: str) -> list[str]:
    """Get all Remedy Canvas LTI rule IDs that map to a given WCAG criterion."""
    return [rule for rule, criterion in _RULE_TO_CRITERION.items() if criterion == criterion_id]


def get_vpat_metadata() -> dict:
    """Get VPAT template metadata."""
    return _VPAT_DATA["metadata"]


def get_categories() -> list[dict]:
    """Get WCAG principle categories (1.1, 1.2, etc.)."""
    return _VPAT_DATA["categories"]


def calculate_conformance_level(issue_count: int, total_items: int) -> str:
    """Calculate conformance level based on issues found.

    Returns:
        One of: 'Supports', 'Partially Supports', 'Does Not Support', 'Not Applicable'
    """
    if total_items == 0:
        return "Not Applicable"
    if issue_count == 0:
        return "Supports"
    if issue_count == total_items:
        return "Does Not Support"
    return "Partially Supports"


# Pre-computed AA criteria list for fast lookup
AA_CRITERIA_IDS = {c["id"] for c in get_aa_criteria()}
