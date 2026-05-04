"""PDF accessibility check models."""

from enum import Enum
from typing import List

from pydantic import BaseModel, computed_field


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    ERROR = "error"


class CheckResult(BaseModel):
    rule_id: str
    category: str
    status: CheckStatus
    message: str
    wcag_criterion: str = ""
    fixable: bool = False


class CheckReport(BaseModel):
    file_id: str
    filename: str
    results: List[CheckResult] = []

    @computed_field
    @property
    def total_checks(self) -> int:
        return len(self.results)

    @computed_field
    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == CheckStatus.PASS)

    @computed_field
    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == CheckStatus.FAIL)

    @computed_field
    @property
    def not_applicable(self) -> int:
        return sum(1 for r in self.results if r.status == CheckStatus.NOT_APPLICABLE)

    @computed_field
    @property
    def errors(self) -> int:
        return sum(1 for r in self.results if r.status == CheckStatus.ERROR)

    @computed_field
    @property
    def pass_rate(self) -> float:
        """Percentage of applicable checks that passed (0.0 if no applicable checks)."""
        applicable = self.passed + self.failed
        if applicable == 0:
            return 0.0
        return round(self.passed / applicable * 100, 2)


class FixReport(BaseModel):
    """Result of running PDF accessibility fixes."""
    fixes_applied: list[str] = []
    fixes_skipped: list[str] = []
    errors: list[str] = []


class TagTreeSeverity(str, Enum):
    """Tag tree validation issue severity."""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ScreenReaderIssue(BaseModel):
    """A single issue found during tag tree validation."""
    description: str
    severity: TagTreeSeverity
    location: str = ""


class TagTreeReport(BaseModel):
    """Result of tag tree validation."""
    issues: list[ScreenReaderIssue] = []
    reading_order_text: str = ""
    summary: str = ""
