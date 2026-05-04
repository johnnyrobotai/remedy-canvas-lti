"""FirestoreToolConf — pylti1p3 ToolConfAbstract backed by Firestore.

Key design: pylti1p3 calls find_registration/find_deployment synchronously
during MessageLaunch.validate(). We solve the async/sync impedance mismatch
by pre-warming an in-memory cache with `preload_registration()` before
handing control to pylti1p3.
"""

from typing import Optional

import structlog
from pylti1p3.deployment import Deployment
from pylti1p3.registration import Registration
from pylti1p3.tool_config import ToolConfAbstract

from lti_app.db.repositories import RegistrationRepository

_logger = structlog.get_logger(__name__)


class FirestoreToolConf(ToolConfAbstract):
    """ToolConf backed by Firestore with in-memory cache pre-warming."""

    def __init__(self, registration_repo: RegistrationRepository):
        super().__init__()
        self._repo = registration_repo
        self._cache: dict[str, dict] = {}  # "iss|client_id" → registration dict

    def preload_registration(self, iss: str, client_id: str | None = None) -> None:
        """Pre-warm cache from Firestore. Call this BEFORE pylti1p3 validate()."""
        if client_id:
            reg = self._repo.get_by_issuer_client(iss, client_id)
        else:
            reg = self._repo.get_by_issuer(iss)

        if reg:
            key = f"{reg['iss']}|{reg.get('client_id', '')}"
            self._cache[key] = reg
            _logger.debug("toolconf_cache_loaded", iss=iss, client_id=client_id)

    def preload_all(self) -> None:
        """Load all registrations into cache. Used at startup."""
        for reg in self._repo.get_all():
            key = f"{reg['iss']}|{reg.get('client_id', '')}"
            self._cache[key] = reg

    def _find_cached(self, iss: str, client_id: str | None = None) -> dict | None:
        if client_id:
            return self._cache.get(f"{iss}|{client_id}")
        # Search by iss only
        for key, reg in self._cache.items():
            if key.startswith(f"{iss}|"):
                return reg
        return None

    def _to_registration(self, reg: dict) -> Registration:
        r = Registration()
        r.set_issuer(reg["iss"])
        r.set_client_id(reg["client_id"])
        r.set_auth_login_url(reg["auth_login_url"])
        r.set_auth_token_url(reg["auth_token_url"])
        r.set_key_set_url(reg["jwks_url"])
        if reg.get("tool_private_key_pem"):
            r.set_tool_private_key(reg["tool_private_key_pem"])
        if reg.get("tool_public_key_pem"):
            r.set_tool_public_key(reg["tool_public_key_pem"])
        return r

    def _to_deployment(self, reg: dict) -> Deployment:
        return Deployment().set_deployment_id(reg["deployment_id"])

    # --- pylti1p3 abstract methods (called synchronously during validate) ---

    def find_registration_by_issuer(self, iss: str, *args, **kwargs) -> Registration:
        reg = self._find_cached(iss)
        if not reg:
            from pylti1p3.exception import LtiException
            raise LtiException(f"Registration not found for iss={iss}")
        return self._to_registration(reg)

    def find_registration_by_params(self, iss: str, client_id: str, *args, **kwargs) -> Registration:
        reg = self._find_cached(iss, client_id)
        if not reg:
            from pylti1p3.exception import LtiException
            raise LtiException(f"Registration not found for iss={iss} client_id={client_id}")
        return self._to_registration(reg)

    def find_deployment(self, iss: str, deployment_id: str) -> Optional[Deployment]:
        reg = self._find_cached(iss)
        if reg and reg.get("deployment_id") == deployment_id:
            return self._to_deployment(reg)
        return None

    def find_deployment_by_params(self, iss: str, deployment_id: str, client_id: str, *args, **kwargs) -> Optional[Deployment]:
        reg = self._find_cached(iss, client_id)
        if reg and reg.get("deployment_id") == deployment_id:
            return self._to_deployment(reg)
        return None

    # --- Convenience for JWKS endpoint ---

    def get_all_registrations_dicts(self) -> list[dict]:
        """Return all cached registrations for JWKS aggregation."""
        if not self._cache:
            self.preload_all()
        return list(self._cache.values())
