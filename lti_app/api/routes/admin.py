"""Admin panel API endpoints: rule config, batch ops, dashboard, scheduled scans."""

import asyncio
from datetime import UTC, datetime
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from lti_app.auth.dependencies import AdminSession
from lti_app.config import get_settings
from lti_app.core.accessibility.analyzer import AccessibilityAnalyzer
from lti_app.db.repositories import (
    ScanRepository,
    RemediationRepository,
    SettingsRepository,
    get_scan_repository,
    get_remediation_repository,
    get_settings_repository,
)
from lti_app.models import (
    AdminSettings,
    BatchJob,
    InstitutionDashboard,
    RuleConfig,
    ScheduledScan,
    ScanStatus,
)
from lti_app.services.batch_service import BatchService
from lti_app.services.dashboard_service import DashboardService

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Shared analyzer for reading rule metadata
_analyzer = AccessibilityAnalyzer()


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


def _get_settings_repo() -> SettingsRepository:
    return get_settings_repository()


def _get_batch_service(
    settings_repo: SettingsRepository = Depends(_get_settings_repo),
    scan_repo: ScanRepository = Depends(get_scan_repository),
) -> BatchService:
    return BatchService(settings_repo, scan_repo)


def _get_dashboard_service(
    scan_repo: ScanRepository = Depends(get_scan_repository),
    remediation_repo: RemediationRepository = Depends(get_remediation_repository),
) -> DashboardService:
    return DashboardService(scan_repo, remediation_repo)


# ---------------------------------------------------------------------------
# Feature 1: Configurable severity levels per rule
# ---------------------------------------------------------------------------


class RuleInfo(BaseModel):
    """A rule with its default metadata and any admin overrides."""

    rule_id: str
    severity: str
    category: str
    wcag_criterion: str
    can_auto_fix: bool
    message_template: str
    # Admin overrides (if any)
    severity_override: Optional[str] = None
    enabled: bool = True
    notes: str = ""


class RuleConfigUpdate(BaseModel):
    """Payload for updating a rule's admin config."""

    severity_override: Optional[str] = None
    enabled: bool = True
    notes: str = ""


@router.get("/rules", response_model=list[RuleInfo])
async def list_rules(
    session: AdminSession,
    settings_repo: SettingsRepository = Depends(_get_settings_repo),
):
    """List all accessibility rules with current admin configuration."""
    # Load admin settings
    admin_data = settings_repo.get_settings(session.deployment_id)
    admin_settings = (
        AdminSettings.model_validate(admin_data) if admin_data else None
    )

    rules = []
    for rule in _analyzer.rules:
        info = RuleInfo(
            rule_id=rule.rule_id,
            severity=rule.severity.value,
            category=rule.category.value,
            wcag_criterion=rule.wcag_criterion,
            can_auto_fix=rule.can_auto_fix,
            message_template=getattr(rule, "message_template", ""),
        )

        # Apply admin overrides if any
        if admin_settings and rule.rule_id in admin_settings.rule_configs:
            cfg = admin_settings.rule_configs[rule.rule_id]
            info.severity_override = cfg.severity_override
            info.enabled = cfg.enabled
            info.notes = cfg.notes

        rules.append(info)

    return rules


@router.put("/rules/{rule_id}", response_model=RuleInfo)
async def update_rule_config(
    rule_id: str,
    update: RuleConfigUpdate,
    session: AdminSession,
    settings_repo: SettingsRepository = Depends(_get_settings_repo),
):
    """Update admin configuration for a specific rule."""
    # Validate rule_id exists
    rule = _find_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")

    # Validate severity_override value
    if update.severity_override and update.severity_override not in (
        "error",
        "warning",
        "info",
    ):
        raise HTTPException(
            status_code=400,
            detail="severity_override must be 'error', 'warning', 'info', or null",
        )

    # Load or create admin settings
    admin_data = settings_repo.get_settings(session.deployment_id)
    if admin_data:
        admin_settings = AdminSettings.model_validate(admin_data)
    else:
        admin_settings = AdminSettings(
            deployment_id=session.deployment_id,
            updated_at=datetime.now(UTC),
        )

    # Update the rule config
    admin_settings.rule_configs[rule_id] = RuleConfig(
        rule_id=rule_id,
        severity_override=update.severity_override,
        enabled=update.enabled,
        notes=update.notes,
    )
    admin_settings.updated_at = datetime.now(UTC)

    # Persist
    settings_repo.save_settings(
        session.deployment_id,
        admin_settings.model_dump(mode="json"),
    )

    # Return updated rule info
    return RuleInfo(
        rule_id=rule.rule_id,
        severity=rule.severity.value,
        category=rule.category.value,
        wcag_criterion=rule.wcag_criterion,
        can_auto_fix=rule.can_auto_fix,
        message_template=getattr(rule, "message_template", ""),
        severity_override=update.severity_override,
        enabled=update.enabled,
        notes=update.notes,
    )


# ---------------------------------------------------------------------------
# Admin settings
# ---------------------------------------------------------------------------


class AdminSettingsUpdate(BaseModel):
    """Payload for updating admin settings."""

    auto_scan_enabled: Optional[bool] = None
    auto_scan_interval_hours: Optional[int] = None


@router.get("/settings")
async def get_admin_settings(
    session: AdminSession,
    settings_repo: SettingsRepository = Depends(_get_settings_repo),
):
    """Get admin settings for this institution."""
    admin_data = settings_repo.get_settings(session.deployment_id)
    if not admin_data:
        return AdminSettings(
            deployment_id=session.deployment_id,
            updated_at=datetime.now(UTC),
        ).model_dump(mode="json")
    return admin_data


@router.put("/settings")
async def update_admin_settings(
    update: AdminSettingsUpdate,
    session: AdminSession,
    settings_repo: SettingsRepository = Depends(_get_settings_repo),
):
    """Update admin settings for this institution."""
    admin_data = settings_repo.get_settings(session.deployment_id)
    if admin_data:
        admin_settings = AdminSettings.model_validate(admin_data)
    else:
        admin_settings = AdminSettings(
            deployment_id=session.deployment_id,
            updated_at=datetime.now(UTC),
        )

    if update.auto_scan_enabled is not None:
        admin_settings.auto_scan_enabled = update.auto_scan_enabled
    if update.auto_scan_interval_hours is not None:
        if update.auto_scan_interval_hours < 1:
            raise HTTPException(
                status_code=400,
                detail="auto_scan_interval_hours must be >= 1",
            )
        admin_settings.auto_scan_interval_hours = update.auto_scan_interval_hours

    admin_settings.updated_at = datetime.now(UTC)

    settings_repo.save_settings(
        session.deployment_id,
        admin_settings.model_dump(mode="json"),
    )

    return admin_settings.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Feature 2: Multi-course batch operations
# ---------------------------------------------------------------------------


class BatchRequest(BaseModel):
    """Payload for starting a batch operation."""

    course_ids: list[str]
    operation: str  # "scan", "remediate", "audit_files"


@router.post("/batch", response_model=BatchJob)
async def start_batch(
    request: BatchRequest,
    session: AdminSession,
    batch_service: BatchService = Depends(_get_batch_service),
):
    """Start a multi-course batch operation."""
    if not request.course_ids:
        raise HTTPException(status_code=400, detail="course_ids must not be empty")

    if request.operation not in ("scan", "remediate", "audit_files"):
        raise HTTPException(
            status_code=400,
            detail="operation must be 'scan', 'remediate', or 'audit_files'",
        )

    job = batch_service.create_batch_job(
        deployment_id=session.deployment_id,
        course_ids=request.course_ids,
        operation=request.operation,
    )

    # Fire background task
    asyncio.create_task(
        batch_service.run_batch(
            job.id, session, request.operation, request.course_ids
        )
    )

    return job


@router.get("/batch/{job_id}", response_model=BatchJob)
async def get_batch_status(
    job_id: str,
    session: AdminSession,
    batch_service: BatchService = Depends(_get_batch_service),
):
    """Poll a batch job's status."""
    job = batch_service.get_batch_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Batch job not found")
    return job


# ---------------------------------------------------------------------------
# Feature 3: Institution-wide dashboard
# ---------------------------------------------------------------------------


@router.get("/dashboard", response_model=InstitutionDashboard)
async def get_dashboard(
    session: AdminSession,
    dashboard_service: DashboardService = Depends(_get_dashboard_service),
):
    """Get institution-wide accessibility dashboard statistics."""
    return dashboard_service.get_institution_dashboard(session.deployment_id)


# ---------------------------------------------------------------------------
# Feature 5: Scheduled scans
# ---------------------------------------------------------------------------


class ScheduledScanUpdate(BaseModel):
    """Payload for enabling/disabling a scheduled scan."""

    enabled: bool = True


@router.get("/scheduled-scans", response_model=list[ScheduledScan])
async def list_scheduled_scans(
    session: AdminSession,
    settings_repo: SettingsRepository = Depends(_get_settings_repo),
):
    """List all scheduled scans for this institution."""
    data = settings_repo.get_settings(f"scheduled_scans:{session.deployment_id}")
    if not data or "scans" not in data:
        return []

    return [ScheduledScan.model_validate(s) for s in data["scans"]]


@router.post("/scheduled-scans/{course_id}", response_model=ScheduledScan)
async def upsert_scheduled_scan(
    course_id: str,
    update: ScheduledScanUpdate,
    session: AdminSession,
    settings_repo: SettingsRepository = Depends(_get_settings_repo),
):
    """Enable or disable a scheduled scan for a specific course."""
    key = f"scheduled_scans:{session.deployment_id}"
    data = settings_repo.get_settings(key)
    scans: list[dict] = data.get("scans", []) if data else []

    # Find or create the entry
    found = False
    for scan_dict in scans:
        if scan_dict.get("course_id") == course_id:
            scan_dict["enabled"] = update.enabled
            found = True
            break

    if not found:
        scan = ScheduledScan(course_id=course_id, enabled=update.enabled)
        scans.append(scan.model_dump(mode="json"))

    settings_repo.save_settings(key, {"scans": scans})

    # Return the current state
    for scan_dict in scans:
        if scan_dict.get("course_id") == course_id:
            return ScheduledScan.model_validate(scan_dict)

    # Should not reach here, but satisfy the type checker
    return ScheduledScan(course_id=course_id, enabled=update.enabled)


# ---------------------------------------------------------------------------
# Feature 4: Remediation guides
# ---------------------------------------------------------------------------


@router.get("/guides")
async def list_guides(
    session: AdminSession,
    category: Optional[str] = Query(None, description="Filter by category"),
    severity: Optional[str] = Query(None, description="Filter by severity"),
):
    """List all remediation guides, optionally filtered."""
    from lti_app.core.accessibility.remediation_guides import get_all_guides

    guides = get_all_guides()

    if category:
        guides = {
            k: v for k, v in guides.items() if v.get("category") == category
        }
    if severity:
        guides = {
            k: v for k, v in guides.items() if v.get("severity") == severity
        }

    return {"guides": guides, "total": len(guides)}


@router.get("/guides/{rule_id}")
async def get_guide(
    rule_id: str,
    session: AdminSession,
):
    """Get the remediation guide for a specific rule."""
    from lti_app.core.accessibility.remediation_guides import get_guide

    guide = get_guide(rule_id)
    if not guide:
        raise HTTPException(
            status_code=404,
            detail=f"No remediation guide found for rule {rule_id}",
        )
    return {"rule_id": rule_id, **guide}


# ---------------------------------------------------------------------------
# Feature 6: Canvas custom JS/CSS injection
# ---------------------------------------------------------------------------


@router.get("/canvas-js")
async def get_canvas_custom_js(
    session: AdminSession,
):
    """Get the Canvas custom JS snippet for inline score badges."""
    from lti_app.core.canvas_injection import generate_score_badge_css, generate_score_badge_js

    settings = get_settings()
    base_url = getattr(settings, "app_base_url", "") or ""
    return {"js": generate_score_badge_js(base_url), "css": generate_score_badge_css()}


# ---------------------------------------------------------------------------
# Feature 7: Blueprint course support
# ---------------------------------------------------------------------------


@router.get("/courses/{course_id}/blueprint")
async def get_blueprint_info(
    course_id: int,
    session: AdminSession,
):
    """Check if a course is a blueprint and list associated courses."""
    from lti_app.services.blueprint_service import BlueprintService

    service = BlueprintService()
    return await service.get_blueprint_info(session, course_id)


@router.post("/courses/{course_id}/blueprint/sync")
async def trigger_blueprint_sync(
    course_id: int,
    session: AdminSession,
):
    """Trigger a blueprint sync to push accessibility fixes to associated courses."""
    from lti_app.services.blueprint_service import BlueprintService

    service = BlueprintService()
    return await service.trigger_sync(session, course_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_rule(rule_id: str):
    """Find an accessibility rule by its ID."""
    for rule in _analyzer.rules:
        if rule.rule_id == rule_id:
            return rule
    return None
