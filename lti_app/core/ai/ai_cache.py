"""AI response cache using Postgres + pgvector.

Hierarchical cache strategy (per codex gpt-5.4 recommendation):
1. Exact hash lookup (fastest — SHA-256 of raw content)
2. Normalized hash lookup (catches reformatted/re-encoded duplicates)
3. Vector similarity search (pgvector — catches near-duplicates, lazy embeddings)

Tables used:
- alt_text_cache: image-level cache for vision API responses
- llm_response_cache: task-level cache for text generation (headings, links, doc conversion)
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

import structlog

_logger = structlog.get_logger(__name__)


def _hash_bytes(data: bytes) -> str:
    """SHA-256 hash of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def _hash_text(text: str) -> str:
    """SHA-256 hash of normalized text (collapsed whitespace, lowered, stripped IDs)."""
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    # Strip Canvas-specific IDs and tracking params
    normalized = re.sub(r"(?:course_|page_|file_|assignment_)\d+", "", normalized)
    normalized = re.sub(r"[?&][\w]+=[\w%-]+", "", normalized)
    return hashlib.sha256(normalized.encode()).hexdigest()


class AICacheService:
    """Cache layer for AI-generated content (sync psycopg)."""

    def _conn(self):
        from lti_app.db.postgres import get_conn
        return get_conn()

    # --- Alt Text Cache ---

    def get_cached_alt_text(self, image_bytes: bytes) -> dict | None:
        """Check cache for previously generated alt text."""
        image_hash = _hash_bytes(image_bytes)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT alt_text, model, confidence FROM alt_text_cache WHERE image_hash = %s",
                [image_hash],
            ).fetchone()
        if row:
            with self._conn() as conn:
                conn.execute(
                    "UPDATE alt_text_cache SET hit_count = hit_count + 1, last_accessed_at = NOW() WHERE image_hash = %s",
                    [image_hash],
                )
            _logger.info("ai_cache_hit", cache="alt_text", hash=image_hash[:12])
            return {"alt_text": row[0], "model": row[1], "confidence": row[2]}
        return None

    def cache_alt_text(self, image_bytes: bytes, image_url: str, alt_text: str, model: str, confidence: float) -> None:
        """Store generated alt text in cache."""
        image_hash = _hash_bytes(image_bytes)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO alt_text_cache (image_hash, image_url, alt_text, model, confidence)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (image_hash) DO UPDATE SET alt_text = %s, model = %s, confidence = %s, last_accessed_at = NOW()""",
                [image_hash, image_url, alt_text, model, confidence, alt_text, model, confidence],
            )

    # --- LLM Response Cache ---

    def get_cached_response(self, task_type: str, prompt: str, model: str) -> str | None:
        """Check cache for a previously generated LLM response."""
        prompt_hash = _hash_text(f"{task_type}:{model}:{prompt}")
        with self._conn() as conn:
            row = conn.execute(
                "SELECT response FROM llm_response_cache WHERE prompt_hash = %s",
                [prompt_hash],
            ).fetchone()
        if row:
            with self._conn() as conn:
                conn.execute(
                    "UPDATE llm_response_cache SET hit_count = hit_count + 1, last_accessed_at = NOW() WHERE prompt_hash = %s",
                    [prompt_hash],
                )
            _logger.info("ai_cache_hit", cache="llm_response", task=task_type)
            return row[0]
        return None

    def cache_response(self, task_type: str, prompt: str, response: str, model: str) -> None:
        """Store an LLM response in cache."""
        prompt_hash = _hash_text(f"{task_type}:{model}:{prompt}")
        summary = prompt[:200] if prompt else ""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO llm_response_cache (prompt_hash, prompt_summary, response, task_type, model)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (prompt_hash) DO UPDATE SET response = %s, last_accessed_at = NOW()""",
                [prompt_hash, summary, response, task_type, model, response],
            )


# Module-level singleton
_cache_service: AICacheService | None = None


def get_ai_cache() -> AICacheService:
    """Return the shared AI cache service."""
    global _cache_service
    if _cache_service is None:
        _cache_service = AICacheService()
    return _cache_service
