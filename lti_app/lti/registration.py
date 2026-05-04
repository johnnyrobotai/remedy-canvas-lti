"""Platform registration management.

CRUD operations for Canvas LTI platform registrations stored in Firestore.
"""

import structlog
from ulid import ULID

from lti_app.db.repositories import RegistrationRepository
from lti_app.lti.models import PlatformRegistration

_logger = structlog.get_logger(__name__)


def get_registration(
    repo: RegistrationRepository,
    iss: str,
    client_id: str | None = None,
) -> PlatformRegistration | None:
    """Look up a platform registration by issuer and optional client_id."""
    if client_id:
        data = repo.get_by_issuer_client(iss, client_id)
    else:
        data = repo.get_by_issuer(iss)

    if not data:
        return None
    return PlatformRegistration(**data)


def get_all_registrations(repo: RegistrationRepository) -> list[PlatformRegistration]:
    return [PlatformRegistration(**data) for data in repo.get_all()]


def save_registration(
    repo: RegistrationRepository,
    registration: PlatformRegistration,
) -> str:
    """Save or update a platform registration. Returns the registration ID."""
    if not registration.id:
        registration = registration.model_copy(update={"id": str(ULID())})

    data = registration.model_dump()
    repo.save_registration(registration.id, data)
    _logger.info(
        "registration_saved",
        reg_id=registration.id,
        iss=registration.iss,
        client_id=registration.client_id,
    )
    return registration.id


def delete_registration(repo: RegistrationRepository, reg_id: str) -> None:
    repo.delete_registration(reg_id)
    _logger.info("registration_deleted", reg_id=reg_id)
