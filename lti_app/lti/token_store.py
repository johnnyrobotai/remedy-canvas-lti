"""pylti1p3 LaunchDataStorage implementation.

DictLaunchDataStorage: In-process dict for OIDC state/nonce data (5-minute TTL).
These store ephemeral OIDC data, NOT LTI sessions.
"""

import time
from typing import Any, Optional

from pylti1p3.launch_data_storage.base import LaunchDataStorage


class DictLaunchDataStorage(LaunchDataStorage):
    """In-process dict storage for pylti1p3 OIDC state."""

    _store: dict[str, tuple[Any, float | None]] = {}

    def __init__(self):
        self._store = {}

    def can_set_keys_expiration(self) -> bool:
        return True

    def set_value(self, key: str, value: Any, exp: Optional[int] = None) -> None:
        exp_at = time.time() + exp if exp else None
        self._store[key] = (value, exp_at)

    def get_value(self, key: str) -> Any:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, exp_at = entry
        if exp_at and time.time() > exp_at:
            del self._store[key]
            return None
        return value

    def check_value(self, key: str) -> bool:
        return self.get_value(key) is not None
