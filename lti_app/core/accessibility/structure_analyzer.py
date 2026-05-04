"""Course structure analysis -- modules, navigation, rubrics."""

from pydantic import BaseModel


class StructureIssue(BaseModel):
    category: str  # "modules", "navigation", "rubrics"
    severity: str  # "error", "warning", "info"
    message: str
    location: str = ""


class CourseStructureReport(BaseModel):
    total_modules: int = 0
    total_items: int = 0
    total_rubrics: int = 0
    issues: list[StructureIssue] = []
    score: float = 100.0


class CourseStructureAnalyzer:
    """Analyze course structure for accessibility best practices."""

    def analyze(
        self, modules: list[dict], rubrics: list[dict] | None = None
    ) -> CourseStructureReport:
        issues: list[StructureIssue] = []
        total_items = 0

        for mod in modules:
            items = mod.get("items", [])
            total_items += len(items)

            # Check module has items
            if not items:
                issues.append(
                    StructureIssue(
                        category="modules",
                        severity="warning",
                        message=f"Module '{mod.get('name', 'Untitled')}' has no items",
                        location=f"Module: {mod.get('name', '')}",
                    )
                )

            # Check for unnamed items
            for item in items:
                if not item.get("title", "").strip():
                    issues.append(
                        StructureIssue(
                            category="modules",
                            severity="error",
                            message="Module item has no title",
                            location=(
                                f"Module: {mod.get('name', '')}, "
                                f"Position: {item.get('position', '?')}"
                            ),
                        )
                    )

            # Check for logical ordering (items should have sequential positions)
            positions = [i.get("position", 0) for i in items]
            if positions != sorted(positions):
                issues.append(
                    StructureIssue(
                        category="navigation",
                        severity="warning",
                        message=(
                            f"Module '{mod.get('name', '')}' has non-sequential "
                            "item positions"
                        ),
                        location=f"Module: {mod.get('name', '')}",
                    )
                )

        # Rubric analysis
        for rubric in rubrics or []:
            criteria = rubric.get("data", [])
            for criterion in criteria:
                if not criterion.get("description", "").strip():
                    issues.append(
                        StructureIssue(
                            category="rubrics",
                            severity="warning",
                            message=(
                                "Rubric criterion has no description in "
                                f"'{rubric.get('title', 'Untitled')}'"
                            ),
                            location=f"Rubric: {rubric.get('title', '')}",
                        )
                    )

        # Calculate score
        error_count = sum(1 for i in issues if i.severity == "error")
        warning_count = sum(1 for i in issues if i.severity == "warning")
        deductions = error_count * 10 + warning_count * 3
        score = max(0.0, 100.0 - deductions)

        return CourseStructureReport(
            total_modules=len(modules),
            total_items=total_items,
            total_rubrics=len(rubrics or []),
            issues=issues,
            score=score,
        )
