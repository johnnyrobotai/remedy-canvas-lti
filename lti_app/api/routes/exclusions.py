"""Permanent course exclusion routes (CLU-85 selective remediation).

Durable per-course item-level opt-outs from AutoRemedy. Items in this
list are pre-unchecked on every content-type review view AND merged
into the skip lists at the start of every AutoRemedy run.
"""
from datetime import datetime, UTC
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Path, Response, status
from pydantic import BaseModel
from ulid import ULID

from lti_app.auth.dependencies import InstructorSession, verify_session_course_id
from lti_app.db.repositories import (
    ExclusionRepository,
    get_exclusion_repository,
)
from lti_app.models import CourseExclusion

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/courses", tags=["exclusions"])


class AddExclusionRequest(BaseModel):
    item_identifier: str
    item_type: str
    reason: str | None = None


class AddExclusionResponse(BaseModel):
    status: str


class ExclusionSummary(BaseModel):
    """Slightly richer shape than CourseExclusion for the UI."""
    item_identifier: str
    item_type: str
    reason: str | None
    excluded_at: datetime
    excluded_by: str


class ListExclusionsResponse(BaseModel):
    exclusions: list[ExclusionSummary]


@router.get("/{course_id}/exclusions", response_model=ListExclusionsResponse)
async def list_exclusions(
    course_id: int,
    session: InstructorSession,
    repo: ExclusionRepository = Depends(get_exclusion_repository),
):
    """List all permanent exclusions for the current course."""
    verify_session_course_id(session, course_id)  # CLU-82
    rows = repo.list_for_course(str(course_id))
    return ListExclusionsResponse(
        exclusions=[
            ExclusionSummary(
                item_identifier=r.item_identifier,
                item_type=r.item_type,
                reason=r.reason,
                excluded_at=r.excluded_at,
                excluded_by=r.excluded_by,
            )
            for r in rows
        ]
    )


@router.post(
    "/{course_id}/exclusions",
    status_code=status.HTTP_201_CREATED,
    response_model=AddExclusionResponse,
)
async def add_exclusion(
    course_id: int,
    body: AddExclusionRequest,
    session: InstructorSession,
    repo: ExclusionRepository = Depends(get_exclusion_repository),
):
    """Add a permanent exclusion. Idempotent — adding the same
    (course_id, item_identifier) twice is a no-op and still returns 201."""
    verify_session_course_id(session, course_id)  # CLU-82
    exclusion = CourseExclusion(
        id=str(ULID()),
        course_id=str(course_id),
        item_identifier=body.item_identifier,
        item_type=body.item_type,
        reason=body.reason,
        excluded_at=datetime.now(UTC),
        excluded_by=session.user_id,
    )
    repo.add(exclusion)
    _logger.info(
        "exclusion_added",
        course_id=course_id,
        item_identifier=body.item_identifier,
        user_id=session.user_id,
    )
    return {"status": "added"}


@router.delete(
    "/{course_id}/exclusions/{item_identifier}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_exclusion(
    course_id: int,
    item_identifier: Annotated[str, Path(max_length=256)],
    session: InstructorSession,
    repo: ExclusionRepository = Depends(get_exclusion_repository),
):
    """Remove a permanent exclusion. Idempotent — 204 whether or not
    the row existed."""
    verify_session_course_id(session, course_id)  # CLU-82
    repo.remove(str(course_id), item_identifier)
    _logger.info(
        "exclusion_removed",
        course_id=course_id,
        item_identifier=item_identifier,
        user_id=session.user_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
