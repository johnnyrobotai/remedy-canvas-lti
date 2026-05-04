"""Shared concurrency controls for AI tasks."""

from __future__ import annotations

import asyncio
import random
import time
from contextlib import asynccontextmanager
from typing import Optional

import structlog

from lti_app.config import get_settings

_logger = structlog.get_logger(__name__)

_GLOBAL_AI_SEMAPHORE: Optional[asyncio.Semaphore] = None
_RUN_SEMAPHORES: dict[str, asyncio.Semaphore] = {}

_MAX_RETRIES = 3
_BASE_DELAY = 2.0
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503}


def get_global_ai_semaphore() -> asyncio.Semaphore:
    """Return the process-wide semaphore for AI work."""
    global _GLOBAL_AI_SEMAPHORE
    if _GLOBAL_AI_SEMAPHORE is None:
        settings = get_settings()
        _GLOBAL_AI_SEMAPHORE = asyncio.Semaphore(
            max(settings.ai_max_concurrency, 1)
        )
    return _GLOBAL_AI_SEMAPHORE


def get_run_ai_semaphore(run_id: str) -> asyncio.Semaphore:
    """Return the per-run semaphore for AI work."""
    semaphore = _RUN_SEMAPHORES.get(run_id)
    if semaphore is None:
        settings = get_settings()
        semaphore = asyncio.Semaphore(max(settings.ai_per_run_cap, 1))
        _RUN_SEMAPHORES[run_id] = semaphore
    return semaphore


def release_run_semaphore(run_id: str) -> None:
    """Clean up a per-run semaphore after the run completes."""
    _RUN_SEMAPHORES.pop(run_id, None)


@asynccontextmanager
async def ai_run_slot(run_id: str | None = None):
    """Acquire a global slot and, optionally, a per-run slot."""
    wait_start = time.monotonic()
    async with get_global_ai_semaphore():
        if run_id:
            async with get_run_ai_semaphore(run_id):
                wait_ms = round((time.monotonic() - wait_start) * 1000, 1)
                if wait_ms > 100:
                    _logger.debug("ai_slot_acquired", run_id=run_id, wait_ms=wait_ms)
                yield
            return
        wait_ms = round((time.monotonic() - wait_start) * 1000, 1)
        if wait_ms > 100:
            _logger.debug("ai_slot_acquired", wait_ms=wait_ms)
        yield


def retry_delay(attempt: int) -> float:
    """Exponential backoff with jitter: base * 2^attempt + random jitter."""
    return _BASE_DELAY * (2 ** attempt) + random.uniform(0, 1.0)


def is_retryable_status(status_code: int) -> bool:
    """Check if an HTTP status code warrants a retry."""
    return status_code in _RETRYABLE_STATUS_CODES


def should_retry(attempt: int) -> bool:
    """Check if another retry attempt is allowed."""
    return attempt < _MAX_RETRIES


def is_retryable_openai_error(exc: Exception) -> bool:
    """Check if an openai SDK exception warrants a retry."""
    try:
        import openai
        if isinstance(exc, openai.APIStatusError):
            return exc.status_code in _RETRYABLE_STATUS_CODES
        if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
            return True
    except ImportError:
        pass
    return False
