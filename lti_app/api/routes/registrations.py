"""LTI platform registration management endpoints.

POST   /api/admin/registrations       — Register a new Canvas platform
GET    /api/admin/registrations       — List all registrations
GET    /api/admin/registrations/{id}  — Get a single registration
DELETE /api/admin/registrations/{id}  — Delete a registration
"""

from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lti_app.auth.dependencies import AdminSession
from lti_app.db.repositories import RegistrationRepository, get_registration_repository
from lti_app.lti.models import PlatformRegistration
from lti_app.lti.registration import (
    delete_registration,
    get_all_registrations,
    get_registration,
    save_registration,
)

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/admin/registrations", tags=["admin-registrations"])


class RegistrationCreate(BaseModel):
    """Payload for registering a Canvas platform."""

    iss: str
    client_id: str
    deployment_id: str
    canvas_base_url: str
    auth_login_url: str
    auth_token_url: str
    jwks_url: str
    tool_private_key_pem: str
    tool_public_key_pem: str = ""
    oauth2_client_id: str = ""
    oauth2_client_secret: str = ""


class RegistrationResponse(BaseModel):
    id: str
    iss: str
    client_id: str
    deployment_id: str
    canvas_base_url: str
    auth_login_url: str
    auth_token_url: str
    jwks_url: str
    tool_public_key_pem: str = ""
    oauth2_client_id: str = ""


def _to_response(reg: PlatformRegistration) -> RegistrationResponse:
    return RegistrationResponse(
        id=reg.id,
        iss=reg.iss,
        client_id=reg.client_id,
        deployment_id=reg.deployment_id,
        canvas_base_url=reg.canvas_base_url,
        auth_login_url=reg.auth_login_url,
        auth_token_url=reg.auth_token_url,
        jwks_url=reg.jwks_url,
        tool_public_key_pem=reg.tool_public_key_pem,
        oauth2_client_id=reg.oauth2_client_id,
    )


@router.get("", response_model=list[RegistrationResponse])
async def list_registrations(
    session: AdminSession,
    repo: RegistrationRepository = Depends(get_registration_repository),
):
    """List all LTI platform registrations."""
    regs = get_all_registrations(repo)
    return [_to_response(r) for r in regs]


@router.post("", response_model=RegistrationResponse, status_code=201)
async def create_registration(
    body: RegistrationCreate,
    session: AdminSession,
    repo: RegistrationRepository = Depends(get_registration_repository),
):
    """Register a new Canvas platform. Reloads the ToolConf cache."""
    reg = PlatformRegistration(id="", **body.model_dump())
    reg_id = save_registration(repo, reg)

    # Reload tool_conf so new registration is available for launches
    _reload_tool_conf(repo)

    saved = get_registration(repo, body.iss, body.client_id)
    if not saved:
        raise HTTPException(status_code=500, detail="Registration save failed")

    return _to_response(saved)


@router.delete("/{reg_id}", status_code=204)
async def remove_registration(
    reg_id: str,
    session: AdminSession,
    repo: RegistrationRepository = Depends(get_registration_repository),
):
    """Delete a platform registration."""
    delete_registration(repo, reg_id)
    _reload_tool_conf(repo)


def _reload_tool_conf(repo: RegistrationRepository) -> None:
    """Reload the global ToolConf singleton with current registrations."""
    from lti_app.api.routes.lti import _tool_conf

    if _tool_conf is not None:
        _tool_conf.preload_all()
        _logger.info(
            "tool_conf_reloaded",
            registrations=len(_tool_conf.get_all_registrations_dicts()),
        )
