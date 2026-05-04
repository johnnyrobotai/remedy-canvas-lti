# Accessibility rules package
from lti_app.core.accessibility.rules.base import AccessibilityRule
from lti_app.core.accessibility.rules.images import MissingAltTextRule, InadequateAltTextRule
from lti_app.core.accessibility.rules.headings import H1UsedRule, SkippedHeadingLevelRule
from lti_app.core.accessibility.rules.tables import (
    MissingTableHeadersRule,
    MissingScopeAttributeRule,
    MissingTableCaptionRule,
)
from lti_app.core.accessibility.rules.links import NonDescriptiveLinkTextRule
from lti_app.core.accessibility.rules.contrast import InsufficientContrastRule

__all__ = [
    "AccessibilityRule",
    "MissingAltTextRule",
    "InadequateAltTextRule",
    "H1UsedRule",
    "SkippedHeadingLevelRule",
    "MissingTableHeadersRule",
    "MissingScopeAttributeRule",
    "MissingTableCaptionRule",
    "NonDescriptiveLinkTextRule",
    "InsufficientContrastRule",
]
