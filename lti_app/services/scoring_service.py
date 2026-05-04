"""Single source of truth for accessibility scoring (CLU-52, CLU-55).

There used to be three independent score formulas in the codebase, all of them
broken in different ways for any course with real issue volume:

* ``dashboard_service.py``: ``100 - (errors/pages * 50)`` — clamped to 0 above
  ~2 errors/page.
* ``ScanService.calculate_score``: ``100 - errors * 5`` — clamped to 0 above
  20 errors total.
* ``CourseACR.conformance_percentage``: counted criteria as 0/0.5/1.0 ignoring
  density, so 264 alt-text failures still left a criterion at 0.5.

ENGL_C1000 with 788 errors / 189 pages produced ``score=0`` on the dashboard
and ``91.7% Excellent`` on the ACR for the same data — same label, opposite
stories.

This module owns the formulas now. Every call site delegates here. If product
later wants a different scoring philosophy, swap a body and every UI updates.

The course-score formula is severity-weighted, density-normalized, exp-decay-
bounded:

    weighted = errors + 0.25 * warnings
    density  = weighted / pages
    score    = 100 * exp(-density / 4)

The ACR conformance percentage uses density-aware partial credit: each
PARTIALLY_SUPPORTS criterion's contribution decays from 0.5 toward 0 as its
finding count grows, with a half-life of 20 findings.

Both functions are continuous, monotonic, never < 0 or > 100.
"""

import math

WARNING_WEIGHT = 0.25
DENSITY_SCALE = 4.0
PARTIAL_HALF_LIFE = 20.0  # Findings at which a PARTIALLY_SUPPORTS criterion
                          # drops from 0.5 → 0.25. Used by compute_conformance_pct.


def compute_course_score(errors: int, warnings: int, pages: int) -> float:
    """Return a 0-100 accessibility score for a course.

    Severity-weighted, density-normalized, never clamps below 0 or above 100.

    A course with zero pages scanned returns 100.0 — there's no signal to
    score against, so we treat it as vacuously perfect rather than punishing
    courses that haven't been touched yet. (This matches the old formulas'
    behavior on empty fixtures and keeps the "no scan yet" empty state from
    rendering as score=0.)

    >>> compute_course_score(0, 0, 10)
    100.0
    >>> compute_course_score(0, 0, 0)
    100.0
    """
    if pages <= 0:
        return 100.0
    weighted = errors + WARNING_WEIGHT * warnings
    density = weighted / pages
    return round(100.0 * math.exp(-density / DENSITY_SCALE), 1)


def score_band(score: float) -> str:
    """Return a coarse label for a 0-100 score.

    Frontend gauges read this so the verbal label can never drift from the
    number. Thresholds are deliberately strict — "excellent" requires near-
    perfection, not just "above average".
    """
    if score >= 90.0:
        return "excellent"
    if score >= 70.0:
        return "good"
    if score >= 40.0:
        return "needs_work"
    return "poor"


def _criterion_credit(conformance: str, issue_count: int) -> float:
    """Credit a single WCAG criterion contributes to the conformance percentage.

    * ``SUPPORTS`` always scores 1.0.
    * ``DOES_NOT_SUPPORT`` always scores 0.0.
    * ``PARTIALLY_SUPPORTS`` starts at 0.5 and decays toward 0 as findings
      accumulate (half-life of ``PARTIAL_HALF_LIFE`` issues). A criterion
      flagged "partial" with 0 findings is still a 0.5; one with 20 findings
      is a 0.25; one with 60 is ~0.07; one with 264 is ~0.0006.

    This fixes CLU-55 — the old formula counted every partial as a flat 0.5
    regardless of density, so ENGL_C1000 scored 91.7% "Excellent" despite
    having 264 alt-text failures and 30 caption failures.

    We accept ``conformance`` as a string (the ConformanceLevel enum value)
    so this module has no runtime dependency on ``models.py`` and stays
    easy to test in isolation.
    """
    # Match the ConformanceLevel string values from lti_app/models.py
    if conformance == "Supports":
        return 1.0
    if conformance == "Partially Supports":
        return 0.5 * (0.5 ** (issue_count / PARTIAL_HALF_LIFE))
    return 0.0  # Does Not Support


def _conformance_value(c) -> str:
    """Extract the string value from a criterion's ``conformance`` field.

    Accepts either the ``ConformanceLevel`` enum member (which inherits from
    str) or a plain string. Returns the raw string value so callers can
    compare against literals like ``"Supports"``.
    """
    conformance = getattr(c, "conformance", None)
    if conformance is None:
        return ""
    # str enums expose .value; plain strings don't
    return getattr(conformance, "value", conformance)


def compute_conformance_pct(criteria) -> float:
    """Return a 0-100 ACR conformance percentage.

    Density-aware: criteria that are "partially supported" contribute less as
    their finding count grows. NOT_APPLICABLE criteria are excluded from the
    denominator (they carry no information about conformance).

    ``criteria`` is any iterable of objects with ``.conformance`` (str or
    ``ConformanceLevel``) and ``.issue_count`` (int) attributes — typically
    ``list[CriterionRollup]``. Duck-typed so this module doesn't import
    ``models``.
    """
    counted = [c for c in criteria if _conformance_value(c) != "Not Applicable"]
    if not counted:
        return 0.0
    total = sum(
        _criterion_credit(
            _conformance_value(c),
            int(getattr(c, "issue_count", 0) or 0),
        )
        for c in counted
    )
    return round((total / len(counted)) * 100.0, 1)
