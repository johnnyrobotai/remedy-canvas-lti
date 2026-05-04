"""PostgreSQL + pgvector connection and schema migration.

Uses a simple connection-per-request pattern with psycopg (sync driver).
This avoids async/sync bridging issues since pylti1p3 and many repo callers
are synchronous.
"""

import json
from contextlib import contextmanager
from urllib.parse import urlparse, urlunparse

import structlog

_logger = structlog.get_logger(__name__)

_dsn: str | None = None


def initialize_postgres(database_url: str) -> None:
    """Store DSN and run schema migration."""
    global _dsn
    # Normalize URL scheme
    _dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
    _run_migration()
    _logger.info("postgres_initialized", dsn=_dsn.split("@")[-1])


def get_dsn() -> str:
    """Return the stored DSN."""
    if _dsn is None:
        raise RuntimeError("Postgres not initialized")
    return _dsn


@contextmanager
def get_conn():
    """Get a database connection (auto-commit, auto-close)."""
    import psycopg
    conn = psycopg.connect(_dsn, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def _run_migration() -> None:
    """Create tables and indexes if they don't exist."""
    import psycopg
    with psycopg.connect(_dsn, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        for stmt in _TABLE_DDL + _INDEX_DDL:
            conn.execute(stmt)
    _logger.info("postgres_migration_complete")


_TABLE_DDL = [
    "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data JSONB DEFAULT '{}')",
    "CREATE TABLE IF NOT EXISTS registrations (id TEXT PRIMARY KEY, iss TEXT NOT NULL DEFAULT '', client_id TEXT NOT NULL DEFAULT '', data JSONB DEFAULT '{}')",
    "CREATE TABLE IF NOT EXISTS tool_settings (deployment_id TEXT PRIMARY KEY, data JSONB NOT NULL DEFAULT '{}')",
    "CREATE TABLE IF NOT EXISTS scan_jobs (id TEXT PRIMARY KEY, course_id TEXT NOT NULL DEFAULT '', data JSONB DEFAULT '{}')",
    "CREATE TABLE IF NOT EXISTS scan_reports (id TEXT PRIMARY KEY, course_id TEXT NOT NULL DEFAULT '', data JSONB DEFAULT '{}')",
    "CREATE TABLE IF NOT EXISTS remediation_jobs (id TEXT PRIMARY KEY, course_id TEXT NOT NULL DEFAULT '', data JSONB DEFAULT '{}')",
    "CREATE TABLE IF NOT EXISTS remediation_previews (id TEXT PRIMARY KEY, job_id TEXT NOT NULL DEFAULT '', data JSONB DEFAULT '{}')",
    "CREATE TABLE IF NOT EXISTS autoremedy_jobs (id TEXT PRIMARY KEY, course_id TEXT NOT NULL DEFAULT '', data JSONB DEFAULT '{}')",
    "CREATE TABLE IF NOT EXISTS file_audit_jobs (id TEXT PRIMARY KEY, course_id TEXT NOT NULL DEFAULT '', data JSONB DEFAULT '{}')",
    "CREATE TABLE IF NOT EXISTS file_audit_reports (id TEXT PRIMARY KEY, course_id TEXT NOT NULL DEFAULT '', data JSONB DEFAULT '{}')",
    "CREATE TABLE IF NOT EXISTS pdf_fix_jobs (id TEXT PRIMARY KEY, course_id TEXT NOT NULL DEFAULT '', data JSONB DEFAULT '{}')",
    "CREATE TABLE IF NOT EXISTS conversion_jobs (id TEXT PRIMARY KEY, course_id TEXT NOT NULL DEFAULT '', data JSONB DEFAULT '{}')",
    "CREATE TABLE IF NOT EXISTS content_versions (id TEXT PRIMARY KEY, course_id TEXT NOT NULL DEFAULT '', data JSONB DEFAULT '{}')",
    "CREATE TABLE IF NOT EXISTS transcription_jobs (id TEXT PRIMARY KEY, course_id TEXT NOT NULL DEFAULT '', data JSONB DEFAULT '{}')",
    "CREATE TABLE IF NOT EXISTS course_videos (id TEXT PRIMARY KEY, course_id TEXT NOT NULL DEFAULT '', data JSONB DEFAULT '{}')",
    "CREATE TABLE IF NOT EXISTS acr_jobs (id TEXT PRIMARY KEY, course_id TEXT NOT NULL DEFAULT '', data JSONB DEFAULT '{}')",
    "CREATE TABLE IF NOT EXISTS acr_reports (id TEXT PRIMARY KEY, course_id TEXT NOT NULL DEFAULT '', data JSONB DEFAULT '{}')",
    "CREATE TABLE IF NOT EXISTS alt_text_cache (image_hash TEXT PRIMARY KEY, image_url TEXT, alt_text TEXT NOT NULL, model TEXT, confidence FLOAT DEFAULT 0, hit_count INT DEFAULT 0, embedding vector(768), created_at TIMESTAMPTZ DEFAULT NOW(), last_accessed_at TIMESTAMPTZ DEFAULT NOW())",
    "CREATE TABLE IF NOT EXISTS llm_response_cache (prompt_hash TEXT PRIMARY KEY, prompt_summary TEXT, response TEXT NOT NULL, task_type TEXT, model TEXT, hit_count INT DEFAULT 0, embedding vector(768), created_at TIMESTAMPTZ DEFAULT NOW(), last_accessed_at TIMESTAMPTZ DEFAULT NOW())",
    """CREATE TABLE IF NOT EXISTS course_exclusions (
        id TEXT PRIMARY KEY,
        course_id TEXT NOT NULL,
        item_identifier TEXT NOT NULL,
        item_type TEXT NOT NULL,
        reason TEXT,
        excluded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        excluded_by TEXT NOT NULL,
        UNIQUE (course_id, item_identifier)
    )""",
]

_INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_scan_jobs_course ON scan_jobs (course_id)",
    "CREATE INDEX IF NOT EXISTS idx_scan_reports_course ON scan_reports (course_id)",
    "CREATE INDEX IF NOT EXISTS idx_remed_jobs_course ON remediation_jobs (course_id)",
    "CREATE INDEX IF NOT EXISTS idx_remed_previews_job ON remediation_previews (job_id)",
    "CREATE INDEX IF NOT EXISTS idx_autoremedy_course ON autoremedy_jobs (course_id)",
    "CREATE INDEX IF NOT EXISTS idx_file_audit_course ON file_audit_jobs (course_id)",
    "CREATE INDEX IF NOT EXISTS idx_file_reports_course ON file_audit_reports (course_id)",
    "CREATE INDEX IF NOT EXISTS idx_transcription_course ON transcription_jobs (course_id)",
    "CREATE INDEX IF NOT EXISTS idx_course_videos_course ON course_videos (course_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_reg_iss_client ON registrations (iss, client_id)",
    "CREATE INDEX IF NOT EXISTS idx_acr_jobs_course ON acr_jobs (course_id)",
    "CREATE INDEX IF NOT EXISTS idx_acr_reports_course ON acr_reports (course_id)",
    "CREATE INDEX IF NOT EXISTS idx_course_exclusions_course ON course_exclusions(course_id)",
]
