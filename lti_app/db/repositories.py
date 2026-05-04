"""Repository pattern with Postgres and in-memory implementations.

Factory functions return the correct implementation based on AUTH_BYPASS_FOR_LOCAL.
Postgres repos use psycopg (sync) with JSONB for flexibility.
Pydantic model_dump/model_validate handles serialization.
"""

from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import UTC, datetime
import json

import structlog

from lti_app.models import (
    ACRJob,
    AccessibilityReport,
    AutoRemedyJob,
    ContentVersion,
    ConversionJob,
    CourseACR,
    CourseExclusion,
    CourseVideoRecord,
    FileAuditJob,
    FileReport,
    OCRJob,
    PDFFixJob,
    RemediationJob,
    RemediationPreview,
    ScanJob,
    TranscriptionJob,
)

_logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Abstract repositories
# ---------------------------------------------------------------------------


class SessionRepository(ABC):
    @abstractmethod
    def get_session(self, session_id: str) -> dict | None: ...

    @abstractmethod
    def save_session(self, session_id: str, data: dict) -> None: ...

    @abstractmethod
    def delete_session(self, session_id: str) -> None: ...


class RegistrationRepository(ABC):
    @abstractmethod
    def get_by_issuer_client(self, iss: str, client_id: str) -> dict | None: ...

    @abstractmethod
    def get_by_issuer(self, iss: str) -> dict | None: ...

    @abstractmethod
    def get_all(self) -> list[dict]: ...

    @abstractmethod
    def save_registration(self, reg_id: str, data: dict) -> None: ...

    @abstractmethod
    def delete_registration(self, reg_id: str) -> None: ...


class SettingsRepository(ABC):
    @abstractmethod
    def get_settings(self, deployment_id: str) -> dict | None: ...

    @abstractmethod
    def save_settings(self, deployment_id: str, data: dict) -> None: ...


class ScanRepository(ABC):
    @abstractmethod
    def save_job(self, job: "ScanJob") -> None: ...

    @abstractmethod
    def get_job(self, job_id: str) -> "ScanJob | None": ...

    @abstractmethod
    def save_report(self, report_id: str, report: "AccessibilityReport") -> None: ...

    @abstractmethod
    def get_report(self, report_id: str) -> "AccessibilityReport | None": ...

    @abstractmethod
    def get_latest_report(self, course_id: str) -> "AccessibilityReport | None": ...

    def get_latest_report_id(self, course_id: str) -> "str | None":
        """Return the row ID of the latest scan report for a course (or None).

        Needed by AutoRemedy when reusing a recent report — it has to know
        the report_id so ``remediation_service.run_remediation`` can load
        the report back via ``get_report``. Default implementation walks the
        concrete subclass to find the ID; Postgres overrides with a single
        SQL query.
        """
        return None

    def get_recent_reports(self, course_id: str, limit: int = 5) -> "list[AccessibilityReport]":
        """Get recent scan reports for a course. Default impl returns latest only."""
        latest = self.get_latest_report(course_id)
        return [latest] if latest else []


class RemediationRepository(ABC):
    @abstractmethod
    def save_job(self, job: "RemediationJob") -> None: ...

    @abstractmethod
    def get_job(self, job_id: str) -> "RemediationJob | None": ...

    @abstractmethod
    def get_latest_job(self, course_id: str) -> "RemediationJob | None": ...

    def get_recent_jobs(self, course_id: str, limit: int = 5) -> "list[RemediationJob]":
        """Get recent remediation jobs. Default impl returns latest only."""
        latest = self.get_latest_job(course_id)
        return [latest] if latest else []

    @abstractmethod
    def save_preview(self, preview: "RemediationPreview") -> None: ...

    @abstractmethod
    def get_preview(self, job_id: str, page_id: str) -> "RemediationPreview | None": ...

    @abstractmethod
    def get_previews(self, job_id: str) -> "list[RemediationPreview]": ...


class FileAuditRepository(ABC):
    @abstractmethod
    def save_job(self, job: "FileAuditJob") -> None: ...

    @abstractmethod
    def get_job(self, job_id: str) -> "FileAuditJob | None": ...

    @abstractmethod
    def save_report(self, report_id: str, report: "FileReport") -> None: ...

    @abstractmethod
    def get_report(self, report_id: str) -> "FileReport | None": ...

    @abstractmethod
    def get_latest_report(self, course_id: str) -> "FileReport | None": ...

    def get_latest_report_id(self, course_id: str) -> "str | None":
        """Return the row ID of the latest file audit report for a course.

        Needed by CLU-85 ``/autoremedy/conversion-candidates`` so the
        frontend can cite the audit report its Files view was derived
        from. Default implementation returns None; concrete subclasses
        override with an efficient lookup.
        """
        return None

    def update_entry(
        self,
        report_id: str,
        file_id: int,
        *,
        skip_reason: "str | None" = None,
        remediation_status: "str | None" = None,
    ) -> bool:
        """Patch the skip_reason / remediation_status on a single audit entry.

        Read-modify-write against the stored report — AutoRemedy phase 4
        processes files sequentially so there is no concurrent writer.
        Returns True when the entry was found and updated, False when
        the report or entry does not exist (treated as best-effort).

        Concrete subclasses MAY override with an atomic JSONB update.
        """
        report = self.get_report(report_id)
        if not report:
            return False
        for entry in report.entries:
            if entry.file_id == file_id:
                if skip_reason is not None:
                    entry.skip_reason = skip_reason
                if remediation_status is not None:
                    entry.remediation_status = remediation_status
                self.save_report(report_id, report)
                return True
        return False


class VersionRepository(ABC):
    @abstractmethod
    def save_version(self, version: "ContentVersion") -> None: ...

    @abstractmethod
    def get_version(self, version_id: str) -> "ContentVersion | None": ...

    @abstractmethod
    def get_versions(self, course_id: str, page_id: str) -> "list[ContentVersion]": ...


class AutoRemedyRepository(ABC):
    @abstractmethod
    def save_job(self, job: "AutoRemedyJob") -> None: ...

    @abstractmethod
    def get_job(self, job_id: str) -> "AutoRemedyJob | None": ...

    @abstractmethod
    def get_latest_job(self, course_id: str) -> "AutoRemedyJob | None": ...

    def get_recent_jobs(self, course_id: str, limit: int = 5) -> "list[AutoRemedyJob]":
        """Get recent autoremedy jobs. Default impl returns latest only."""
        latest = self.get_latest_job(course_id)
        return [latest] if latest else []


class PDFFixRepository(ABC):
    @abstractmethod
    def save_job(self, job: "PDFFixJob") -> None: ...

    @abstractmethod
    def get_job(self, job_id: str) -> "PDFFixJob | None": ...

    @abstractmethod
    def get_jobs_for_course(self, course_id: str) -> "list[PDFFixJob]": ...


class ConversionRepository(ABC):
    @abstractmethod
    def save_job(self, job: "ConversionJob") -> None: ...

    @abstractmethod
    def get_job(self, job_id: str) -> "ConversionJob | None": ...


class OCRRepository(ABC):
    @abstractmethod
    def save_job(self, job: "OCRJob") -> None: ...

    @abstractmethod
    def get_job(self, job_id: str) -> "OCRJob | None": ...


class CaptionRepository(ABC):
    @abstractmethod
    def save_job(self, job: "TranscriptionJob") -> None: ...

    @abstractmethod
    def get_job(self, job_id: str) -> "TranscriptionJob | None": ...

    @abstractmethod
    def get_jobs_for_course(self, course_id: str) -> "list[TranscriptionJob]": ...

    @abstractmethod
    def get_job_by_hash(self, vtt_hash: str) -> "TranscriptionJob | None": ...

    @abstractmethod
    def save_video(self, video: "CourseVideoRecord") -> None: ...

    @abstractmethod
    def get_videos_for_course(self, course_id: str) -> "list[CourseVideoRecord]": ...


class ACRRepository(ABC):
    """Repository for Accessibility Conformance Reports."""

    @abstractmethod
    def save_job(self, job: "ACRJob") -> None: ...

    @abstractmethod
    def get_job(self, job_id: str) -> "ACRJob | None": ...

    @abstractmethod
    def save_acr(self, acr: "CourseACR") -> None: ...

    @abstractmethod
    def get_acr(self, acr_id: str) -> "CourseACR | None": ...

    @abstractmethod
    def list_acrs_for_course(self, course_id: str) -> "list[CourseACR]": ...

    @abstractmethod
    def get_latest_acr(self, course_id: str) -> "CourseACR | None": ...

    @abstractmethod
    def delete_acr(self, acr_id: str) -> None: ...


class ExclusionRepository(ABC):
    """Storage for durable per-course AutoRemedy exclusions (CLU-85)."""

    @abstractmethod
    def add(self, exclusion: CourseExclusion) -> None:
        """Idempotent insert keyed on (course_id, item_identifier)."""

    @abstractmethod
    def remove(self, course_id: str, item_identifier: str) -> None:
        """No-op if the exclusion doesn't exist."""

    @abstractmethod
    def list_for_course(self, course_id: str) -> list[CourseExclusion]:
        """Return exclusions for a course ordered newest-first."""

    @abstractmethod
    def get_identifiers_for_course(self, course_id: str) -> set[str]:
        """Fast lookup used by AutoRemedyService at run start."""


# ---------------------------------------------------------------------------
# Postgres implementations (psycopg sync — JSONB model storage)
# ---------------------------------------------------------------------------


def _pg_conn():
    """Get a psycopg connection context manager."""
    from lti_app.db.postgres import get_conn
    return get_conn()


def _load_jsonb(row_value):
    """Parse a JSONB column value into a dict."""
    return row_value if isinstance(row_value, dict) else json.loads(row_value)


def _dump_jsonb(model_obj) -> str:
    """Serialize a Pydantic model to a JSON string for JSONB storage."""
    return json.dumps(model_obj.model_dump(mode="json"), default=str)


# Cache of (table_name -> [(column_name, postgres_data_type)]) for mirror columns
# that should be kept in sync with the data jsonb. Looked up once per table.
_MIRROR_COLUMN_CACHE: dict[str, list[tuple[str, str]]] = {}


def _get_mirror_columns(conn, table: str) -> list[tuple[str, str]]:
    """Return [(col_name, col_type)] for columns that should mirror data jsonb keys.

    Excludes the primary key (id), the course_id index, and the data column itself.
    Result is cached at module level — Postgres schemas don't change at runtime.
    """
    if table in _MIRROR_COLUMN_CACHE:
        return _MIRROR_COLUMN_CACHE[table]
    rows = conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = %s "
        "AND column_name NOT IN ('id', 'course_id', 'data') "
        "ORDER BY ordinal_position",
        [table],
    ).fetchall()
    cols = [(r[0], r[1]) for r in rows]
    _MIRROR_COLUMN_CACHE[table] = cols
    return cols


def _build_mirror_assignments(cols: list[tuple[str, str]]) -> str:
    """Build a SET clause that pulls each mirror column from the data jsonb.

    Column names come from information_schema (Postgres metadata, not user input)
    so f-string interpolation is safe. Type-aware casts so timestamps, integers,
    and floats don't end up as text.
    """
    parts = []
    for name, dtype in cols:
        if dtype in ("integer", "bigint", "smallint"):
            parts.append(f"{name} = NULLIF(data->>'{name}', '')::int")
        elif dtype in ("double precision", "real", "numeric"):
            parts.append(f"{name} = NULLIF(data->>'{name}', '')::float8")
        elif dtype.startswith("timestamp"):
            parts.append(f"{name} = NULLIF(data->>'{name}', '')::timestamptz")
        elif dtype == "date":
            parts.append(f"{name} = NULLIF(data->>'{name}', '')::date")
        elif dtype == "jsonb":
            parts.append(f"{name} = data->'{name}'")
        elif dtype == "boolean":
            parts.append(f"{name} = NULLIF(data->>'{name}', '')::boolean")
        else:
            # text, varchar, char, etc.
            parts.append(f"{name} = data->>'{name}'")
    return ", ".join(parts)


class PostgresSessionRepository(SessionRepository):
    def get_session(self, session_id: str) -> dict | None:
        with _pg_conn() as conn:
            row = conn.execute(
                "SELECT data FROM sessions WHERE id = %s", [session_id]
            ).fetchone()
        if not row:
            return None
        data = _load_jsonb(row[0])
        expires_at = data.get("expires_at")
        if expires_at:
            try:
                exp = datetime.fromisoformat(str(expires_at))
                if exp.astimezone(UTC) < datetime.now(UTC):
                    self.delete_session(session_id)
                    return None
            except (ValueError, TypeError):
                pass
        return data

    def save_session(self, session_id: str, data: dict) -> None:
        j = json.dumps(data, default=str)
        with _pg_conn() as conn:
            conn.execute(
                "INSERT INTO sessions (id, data) VALUES (%s, %s::jsonb) "
                "ON CONFLICT (id) DO UPDATE SET data = %s::jsonb",
                [session_id, j, j],
            )

    def delete_session(self, session_id: str) -> None:
        with _pg_conn() as conn:
            conn.execute("DELETE FROM sessions WHERE id = %s", [session_id])


class PostgresRegistrationRepository(RegistrationRepository):
    def get_by_issuer_client(self, iss: str, client_id: str) -> dict | None:
        with _pg_conn() as conn:
            row = conn.execute(
                "SELECT id, data FROM registrations WHERE iss = %s AND client_id = %s LIMIT 1",
                [iss, client_id],
            ).fetchone()
        if not row:
            return None
        data = _load_jsonb(row[1])
        data["id"] = row[0]
        return data

    def get_by_issuer(self, iss: str) -> dict | None:
        with _pg_conn() as conn:
            row = conn.execute(
                "SELECT id, data FROM registrations WHERE iss = %s LIMIT 1",
                [iss],
            ).fetchone()
        if not row:
            return None
        data = _load_jsonb(row[1])
        data["id"] = row[0]
        return data

    def get_all(self) -> list[dict]:
        with _pg_conn() as conn:
            rows = conn.execute("SELECT id, data FROM registrations").fetchall()
        results = []
        for row in rows:
            data = _load_jsonb(row[1])
            data["id"] = row[0]
            results.append(data)
        return results

    def save_registration(self, reg_id: str, data: dict) -> None:
        iss = data.get("iss", "")
        client_id = data.get("client_id", "")
        j = json.dumps(data, default=str)
        with _pg_conn() as conn:
            conn.execute(
                "INSERT INTO registrations (id, iss, client_id, data) VALUES (%s, %s, %s, %s::jsonb) "
                "ON CONFLICT (id) DO UPDATE SET iss = %s, client_id = %s, data = %s::jsonb",
                [reg_id, iss, client_id, j, iss, client_id, j],
            )

    def delete_registration(self, reg_id: str) -> None:
        with _pg_conn() as conn:
            conn.execute("DELETE FROM registrations WHERE id = %s", [reg_id])


class PostgresSettingsRepository(SettingsRepository):
    def get_settings(self, deployment_id: str) -> dict | None:
        with _pg_conn() as conn:
            row = conn.execute(
                "SELECT data FROM tool_settings WHERE deployment_id = %s",
                [deployment_id],
            ).fetchone()
        if not row:
            return None
        return _load_jsonb(row[0])

    def save_settings(self, deployment_id: str, data: dict) -> None:
        j = json.dumps(data, default=str)
        with _pg_conn() as conn:
            conn.execute(
                "INSERT INTO tool_settings (deployment_id, data) VALUES (%s, %s::jsonb) "
                "ON CONFLICT (deployment_id) DO UPDATE SET data = tool_settings.data || %s::jsonb",
                [deployment_id, j, j],
            )


class _PostgresJobRepoMixin:
    """Common save/get pattern for tables storing Pydantic models as JSONB.

    Job tables have a `data jsonb` column (the source of truth) plus a set of
    structured mirror columns (`status`, `phase`, `created_at`, etc.) used as
    indexed convenience fields. The read path goes through the data jsonb only;
    the mirror columns exist to make ad-hoc admin SQL and future indexing
    cheap. We sync mirrors from the JSONB on every save so the two
    representations cannot drift (CLU-51).
    """

    _table: str

    def _save_model(self, model_obj) -> None:
        data = model_obj.model_dump(mode="json")
        obj_id = data["id"]
        course_id = data.get("course_id", "")
        j = json.dumps(data, default=str)
        with _pg_conn() as conn:
            conn.execute(
                f"INSERT INTO {self._table} (id, course_id, data) VALUES (%s, %s, %s::jsonb) "
                f"ON CONFLICT (id) DO UPDATE SET course_id = %s, data = %s::jsonb",
                [obj_id, course_id, j, course_id, j],
            )
            # Sync structured mirror columns from the JSONB. Without this,
            # columns frozen at INSERT time silently drift from `data`, making
            # admin SQL cleanup against columns a no-op (CLU-51).
            cols = _get_mirror_columns(conn, self._table)
            if cols:
                assigns = _build_mirror_assignments(cols)
                conn.execute(
                    f"UPDATE {self._table} SET {assigns} WHERE id = %s",
                    [obj_id],
                )

    def _get_model(self, obj_id: str, model_cls):
        with _pg_conn() as conn:
            row = conn.execute(
                f"SELECT data FROM {self._table} WHERE id = %s", [obj_id]
            ).fetchone()
        if not row:
            return None
        return model_cls.model_validate(_load_jsonb(row[0]))

    def _get_latest(self, course_id: str, model_cls, order_col: str = "created_at"):
        with _pg_conn() as conn:
            row = conn.execute(
                f"SELECT data FROM {self._table} WHERE course_id = %s "
                f"ORDER BY (data->>'{order_col}') DESC LIMIT 1",
                [course_id],
            ).fetchone()
        if not row:
            return None
        return model_cls.model_validate(_load_jsonb(row[0]))

    def _get_by_course(self, course_id: str, model_cls) -> list:
        with _pg_conn() as conn:
            rows = conn.execute(
                f"SELECT data FROM {self._table} WHERE course_id = %s",
                [course_id],
            ).fetchall()
        return [model_cls.model_validate(_load_jsonb(r[0])) for r in rows]

    def _get_recent(self, course_id: str, model_cls, limit: int = 5, order_col: str = "created_at") -> list:
        with _pg_conn() as conn:
            rows = conn.execute(
                f"SELECT data FROM {self._table} WHERE course_id = %s "
                f"ORDER BY (data->>'{order_col}') DESC LIMIT %s",
                [course_id, limit],
            ).fetchall()
        return [model_cls.model_validate(_load_jsonb(r[0])) for r in rows]


class PostgresScanRepository(_PostgresJobRepoMixin, ScanRepository):
    _table = "scan_jobs"

    def save_job(self, job: "ScanJob") -> None:
        self._save_model(job)

    def get_job(self, job_id: str) -> "ScanJob | None":
        from lti_app.models import ScanJob
        return self._get_model(job_id, ScanJob)

    def save_report(self, report_id: str, report: "AccessibilityReport") -> None:
        data = report.model_dump(mode="json")
        course_id = data.get("course_id", "")
        j = json.dumps(data, default=str)
        with _pg_conn() as conn:
            conn.execute(
                "INSERT INTO scan_reports (id, course_id, data) VALUES (%s, %s, %s::jsonb) "
                "ON CONFLICT (id) DO UPDATE SET course_id = %s, data = %s::jsonb",
                [report_id, course_id, j, course_id, j],
            )

    def get_report(self, report_id: str) -> "AccessibilityReport | None":
        with _pg_conn() as conn:
            row = conn.execute(
                "SELECT data FROM scan_reports WHERE id = %s", [report_id]
            ).fetchone()
        if not row:
            return None
        from lti_app.models import AccessibilityReport
        return AccessibilityReport.model_validate(_load_jsonb(row[0]))

    def get_latest_report(self, course_id: str) -> "AccessibilityReport | None":
        with _pg_conn() as conn:
            row = conn.execute(
                "SELECT data FROM scan_reports WHERE course_id = %s "
                "ORDER BY (data->>'analyzed_at') DESC LIMIT 1",
                [course_id],
            ).fetchone()
        if not row:
            return None
        from lti_app.models import AccessibilityReport
        return AccessibilityReport.model_validate(_load_jsonb(row[0]))

    def get_latest_report_id(self, course_id: str) -> "str | None":
        with _pg_conn() as conn:
            row = conn.execute(
                "SELECT id FROM scan_reports WHERE course_id = %s "
                "ORDER BY (data->>'analyzed_at') DESC LIMIT 1",
                [course_id],
            ).fetchone()
        return row[0] if row else None

    def get_recent_reports(self, course_id: str, limit: int = 5) -> "list[AccessibilityReport]":
        with _pg_conn() as conn:
            rows = conn.execute(
                "SELECT data FROM scan_reports WHERE course_id = %s "
                "ORDER BY (data->>'analyzed_at') DESC LIMIT %s",
                [course_id, limit],
            ).fetchall()
        from lti_app.models import AccessibilityReport
        return [AccessibilityReport.model_validate(_load_jsonb(r[0])) for r in rows]


class PostgresRemediationRepository(_PostgresJobRepoMixin, RemediationRepository):
    _table = "remediation_jobs"

    def save_job(self, job: "RemediationJob") -> None:
        self._save_model(job)

    def get_job(self, job_id: str) -> "RemediationJob | None":
        from lti_app.models import RemediationJob
        return self._get_model(job_id, RemediationJob)

    def get_latest_job(self, course_id: str) -> "RemediationJob | None":
        from lti_app.models import RemediationJob
        return self._get_latest(course_id, RemediationJob)

    def get_recent_jobs(self, course_id: str, limit: int = 5) -> "list[RemediationJob]":
        from lti_app.models import RemediationJob
        return self._get_recent(course_id, RemediationJob, limit)

    def save_preview(self, preview: "RemediationPreview") -> None:
        doc_id = f"{preview.job_id}_{preview.page_id}"
        j = json.dumps(preview.model_dump(mode="json"), default=str)
        with _pg_conn() as conn:
            conn.execute(
                "INSERT INTO remediation_previews (id, job_id, data) VALUES (%s, %s, %s::jsonb) "
                "ON CONFLICT (id) DO UPDATE SET data = %s::jsonb",
                [doc_id, preview.job_id, j, j],
            )

    def get_preview(self, job_id: str, page_id: str) -> "RemediationPreview | None":
        doc_id = f"{job_id}_{page_id}"
        with _pg_conn() as conn:
            row = conn.execute(
                "SELECT data FROM remediation_previews WHERE id = %s", [doc_id]
            ).fetchone()
        if not row:
            return None
        from lti_app.models import RemediationPreview
        return RemediationPreview.model_validate(_load_jsonb(row[0]))

    def get_previews(self, job_id: str) -> "list[RemediationPreview]":
        with _pg_conn() as conn:
            rows = conn.execute(
                "SELECT data FROM remediation_previews WHERE job_id = %s", [job_id]
            ).fetchall()
        from lti_app.models import RemediationPreview
        return [RemediationPreview.model_validate(_load_jsonb(r[0])) for r in rows]


class PostgresFileAuditRepository(_PostgresJobRepoMixin, FileAuditRepository):
    _table = "file_audit_jobs"

    def save_job(self, job: "FileAuditJob") -> None:
        self._save_model(job)

    def get_job(self, job_id: str) -> "FileAuditJob | None":
        from lti_app.models import FileAuditJob
        return self._get_model(job_id, FileAuditJob)

    def save_report(self, report_id: str, report: "FileReport") -> None:
        data = report.model_dump(mode="json")
        course_id = data.get("course_id", "")
        j = json.dumps(data, default=str)
        with _pg_conn() as conn:
            conn.execute(
                "INSERT INTO file_audit_reports (id, course_id, data) VALUES (%s, %s, %s::jsonb) "
                "ON CONFLICT (id) DO UPDATE SET course_id = %s, data = %s::jsonb",
                [report_id, course_id, j, course_id, j],
            )

    def get_report(self, report_id: str) -> "FileReport | None":
        with _pg_conn() as conn:
            row = conn.execute(
                "SELECT data FROM file_audit_reports WHERE id = %s", [report_id]
            ).fetchone()
        if not row:
            return None
        from lti_app.models import FileReport
        return FileReport.model_validate(_load_jsonb(row[0]))

    def get_latest_report(self, course_id: str) -> "FileReport | None":
        with _pg_conn() as conn:
            row = conn.execute(
                "SELECT data FROM file_audit_reports WHERE course_id = %s "
                "ORDER BY (data->>'audited_at') DESC LIMIT 1",
                [course_id],
            ).fetchone()
        if not row:
            return None
        from lti_app.models import FileReport
        return FileReport.model_validate(_load_jsonb(row[0]))

    def get_latest_report_id(self, course_id: str) -> "str | None":
        with _pg_conn() as conn:
            row = conn.execute(
                "SELECT id FROM file_audit_reports WHERE course_id = %s "
                "ORDER BY (data->>'audited_at') DESC LIMIT 1",
                [course_id],
            ).fetchone()
        return row[0] if row else None


class PostgresVersionRepository(VersionRepository):
    def save_version(self, version: "ContentVersion") -> None:
        data = version.model_dump(mode="json")
        course_id = data.get("course_id", "")
        j = json.dumps(data, default=str)
        with _pg_conn() as conn:
            conn.execute(
                "INSERT INTO content_versions (id, course_id, data) VALUES (%s, %s, %s::jsonb) "
                "ON CONFLICT (id) DO UPDATE SET data = %s::jsonb",
                [version.id, course_id, j, j],
            )

    def get_version(self, version_id: str) -> "ContentVersion | None":
        with _pg_conn() as conn:
            row = conn.execute(
                "SELECT data FROM content_versions WHERE id = %s", [version_id]
            ).fetchone()
        if not row:
            return None
        from lti_app.models import ContentVersion
        return ContentVersion.model_validate(_load_jsonb(row[0]))

    def get_versions(self, course_id: str, page_id: str) -> "list[ContentVersion]":
        with _pg_conn() as conn:
            rows = conn.execute(
                "SELECT data FROM content_versions WHERE course_id = %s AND data->>'page_id' = %s "
                "ORDER BY (data->>'created_at') DESC",
                [course_id, page_id],
            ).fetchall()
        from lti_app.models import ContentVersion
        return [ContentVersion.model_validate(_load_jsonb(r[0])) for r in rows]


class PostgresAutoRemedyRepository(_PostgresJobRepoMixin, AutoRemedyRepository):
    _table = "autoremedy_jobs"

    def save_job(self, job: "AutoRemedyJob") -> None:
        self._save_model(job)

    def get_job(self, job_id: str) -> "AutoRemedyJob | None":
        from lti_app.models import AutoRemedyJob
        return self._get_model(job_id, AutoRemedyJob)

    def get_latest_job(self, course_id: str) -> "AutoRemedyJob | None":
        from lti_app.models import AutoRemedyJob
        return self._get_latest(course_id, AutoRemedyJob)

    def get_recent_jobs(self, course_id: str, limit: int = 5) -> "list[AutoRemedyJob]":
        from lti_app.models import AutoRemedyJob
        return self._get_recent(course_id, AutoRemedyJob, limit)


class PostgresPDFFixRepository(_PostgresJobRepoMixin, PDFFixRepository):
    _table = "pdf_fix_jobs"

    def save_job(self, job: "PDFFixJob") -> None:
        self._save_model(job)

    def get_job(self, job_id: str) -> "PDFFixJob | None":
        from lti_app.models import PDFFixJob
        return self._get_model(job_id, PDFFixJob)

    def get_jobs_for_course(self, course_id: str) -> "list[PDFFixJob]":
        from lti_app.models import PDFFixJob
        return self._get_by_course(course_id, PDFFixJob)


class PostgresConversionRepository(_PostgresJobRepoMixin, ConversionRepository):
    _table = "conversion_jobs"

    def save_job(self, job: "ConversionJob") -> None:
        self._save_model(job)

    def get_job(self, job_id: str) -> "ConversionJob | None":
        from lti_app.models import ConversionJob
        return self._get_model(job_id, ConversionJob)


class PostgresOCRRepository(_PostgresJobRepoMixin, OCRRepository):
    _table = "ocr_jobs"

    def save_job(self, job: "OCRJob") -> None:
        self._save_model(job)

    def get_job(self, job_id: str) -> "OCRJob | None":
        from lti_app.models import OCRJob
        return self._get_model(job_id, OCRJob)


class PostgresCaptionRepository(_PostgresJobRepoMixin, CaptionRepository):
    _table = "transcription_jobs"

    def save_job(self, job: "TranscriptionJob") -> None:
        self._save_model(job)

    def get_job(self, job_id: str) -> "TranscriptionJob | None":
        from lti_app.models import TranscriptionJob
        return self._get_model(job_id, TranscriptionJob)

    def get_jobs_for_course(self, course_id: str) -> "list[TranscriptionJob]":
        from lti_app.models import TranscriptionJob
        return self._get_by_course(course_id, TranscriptionJob)

    def get_job_by_hash(self, vtt_hash: str) -> "TranscriptionJob | None":
        with _pg_conn() as conn:
            row = conn.execute(
                "SELECT data FROM transcription_jobs WHERE data->>'vtt_hash' = %s LIMIT 1",
                [vtt_hash],
            ).fetchone()
        if not row:
            return None
        from lti_app.models import TranscriptionJob
        return TranscriptionJob.model_validate(_load_jsonb(row[0]))

    def save_video(self, video: "CourseVideoRecord") -> None:
        j = json.dumps(video.model_dump(mode="json"), default=str)
        with _pg_conn() as conn:
            conn.execute(
                "INSERT INTO course_videos (id, course_id, data) VALUES (%s, %s, %s::jsonb) "
                "ON CONFLICT (id) DO UPDATE SET data = %s::jsonb",
                [video.id, video.course_id, j, j],
            )

    def get_videos_for_course(self, course_id: str) -> "list[CourseVideoRecord]":
        with _pg_conn() as conn:
            rows = conn.execute(
                "SELECT data FROM course_videos WHERE course_id = %s",
                [course_id],
            ).fetchall()
        from lti_app.models import CourseVideoRecord
        return [CourseVideoRecord.model_validate(_load_jsonb(r[0])) for r in rows]


class PostgresACRRepository(_PostgresJobRepoMixin, ACRRepository):
    """Postgres implementation of ACR repository."""

    _table = "acr_jobs"

    def save_job(self, job: "ACRJob") -> None:
        self._save_model(job)

    def get_job(self, job_id: str) -> "ACRJob | None":
        from lti_app.models import ACRJob
        return self._get_model(job_id, ACRJob)

    def save_acr(self, acr: "CourseACR") -> None:
        data = acr.model_dump(mode="json")
        course_id = data.get("course_id", "")
        j = json.dumps(data, default=str)
        with _pg_conn() as conn:
            conn.execute(
                "INSERT INTO acr_reports (id, course_id, data) VALUES (%s, %s, %s::jsonb) "
                "ON CONFLICT (id) DO UPDATE SET course_id = %s, data = %s::jsonb",
                [acr.id, course_id, j, course_id, j],
            )

    def get_acr(self, acr_id: str) -> "CourseACR | None":
        with _pg_conn() as conn:
            row = conn.execute(
                "SELECT data FROM acr_reports WHERE id = %s", [acr_id]
            ).fetchone()
        if not row:
            return None
        from lti_app.models import CourseACR
        return CourseACR.model_validate(_load_jsonb(row[0]))

    def list_acrs_for_course(self, course_id: str) -> "list[CourseACR]":
        with _pg_conn() as conn:
            rows = conn.execute(
                "SELECT data FROM acr_reports WHERE course_id = %s "
                "ORDER BY (data->>'generated_at') DESC",
                [course_id],
            ).fetchall()
        from lti_app.models import CourseACR
        return [CourseACR.model_validate(_load_jsonb(r[0])) for r in rows]

    def get_latest_acr(self, course_id: str) -> "CourseACR | None":
        with _pg_conn() as conn:
            row = conn.execute(
                "SELECT data FROM acr_reports WHERE course_id = %s "
                "ORDER BY (data->>'generated_at') DESC LIMIT 1",
                [course_id],
            ).fetchone()
        if not row:
            return None
        from lti_app.models import CourseACR
        return CourseACR.model_validate(_load_jsonb(row[0]))

    def delete_acr(self, acr_id: str) -> None:
        with _pg_conn() as conn:
            conn.execute("DELETE FROM acr_reports WHERE id = %s", [acr_id])


class PostgresExclusionRepository(ExclusionRepository):
    """Postgres-backed durable exclusion storage (CLU-85)."""

    def add(self, exclusion: CourseExclusion) -> None:
        with _pg_conn() as conn:
            conn.execute(
                """INSERT INTO course_exclusions
                   (id, course_id, item_identifier, item_type, reason,
                    excluded_at, excluded_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (course_id, item_identifier) DO NOTHING""",
                [
                    exclusion.id,
                    exclusion.course_id,
                    exclusion.item_identifier,
                    exclusion.item_type,
                    exclusion.reason,
                    exclusion.excluded_at,
                    exclusion.excluded_by,
                ],
            )

    def remove(self, course_id: str, item_identifier: str) -> None:
        with _pg_conn() as conn:
            conn.execute(
                "DELETE FROM course_exclusions "
                "WHERE course_id = %s AND item_identifier = %s",
                [course_id, item_identifier],
            )

    def list_for_course(self, course_id: str) -> list[CourseExclusion]:
        with _pg_conn() as conn:
            rows = conn.execute(
                """SELECT id, course_id, item_identifier, item_type, reason,
                          excluded_at, excluded_by
                   FROM course_exclusions WHERE course_id = %s
                   ORDER BY excluded_at DESC""",
                [course_id],
            ).fetchall()
        return [
            CourseExclusion(
                id=r[0],
                course_id=r[1],
                item_identifier=r[2],
                item_type=r[3],
                reason=r[4],
                excluded_at=r[5],
                excluded_by=r[6],
            )
            for r in rows
        ]

    def get_identifiers_for_course(self, course_id: str) -> set[str]:
        with _pg_conn() as conn:
            rows = conn.execute(
                "SELECT item_identifier FROM course_exclusions WHERE course_id = %s",
                [course_id],
            ).fetchall()
        return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# In-memory implementations (AUTH_BYPASS_FOR_LOCAL=true)
# ---------------------------------------------------------------------------

_in_memory_store: dict[str, dict[str, dict]] = defaultdict(dict)


class InMemorySessionRepository(SessionRepository):
    def get_session(self, session_id: str) -> dict | None:
        data = _in_memory_store["lti_sessions"].get(session_id)
        if data is None:
            return None
        expires_at = data.get("expires_at")
        if expires_at and isinstance(expires_at, datetime):
            if expires_at.astimezone(UTC) < datetime.now(UTC):
                del _in_memory_store["lti_sessions"][session_id]
                return None
        return data

    def save_session(self, session_id: str, data: dict) -> None:
        _in_memory_store["lti_sessions"][session_id] = data

    def delete_session(self, session_id: str) -> None:
        _in_memory_store["lti_sessions"].pop(session_id, None)


class InMemoryRegistrationRepository(RegistrationRepository):
    def get_by_issuer_client(self, iss: str, client_id: str) -> dict | None:
        for data in _in_memory_store["platform_registrations"].values():
            if data.get("iss") == iss and data.get("client_id") == client_id:
                return data
        return None

    def get_by_issuer(self, iss: str) -> dict | None:
        for data in _in_memory_store["platform_registrations"].values():
            if data.get("iss") == iss:
                return data
        return None

    def get_all(self) -> list[dict]:
        return list(_in_memory_store["platform_registrations"].values())

    def save_registration(self, reg_id: str, data: dict) -> None:
        data["id"] = reg_id
        _in_memory_store["platform_registrations"][reg_id] = data

    def delete_registration(self, reg_id: str) -> None:
        _in_memory_store["platform_registrations"].pop(reg_id, None)


class InMemorySettingsRepository(SettingsRepository):
    def get_settings(self, deployment_id: str) -> dict | None:
        return _in_memory_store["tool_settings"].get(deployment_id)

    def save_settings(self, deployment_id: str, data: dict) -> None:
        existing = _in_memory_store["tool_settings"].get(deployment_id, {})
        existing.update(data)
        _in_memory_store["tool_settings"][deployment_id] = existing


class InMemoryScanRepository(ScanRepository):
    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._reports: dict[str, dict] = {}

    def save_job(self, job: "ScanJob") -> None:
        self._jobs[job.id] = job.model_dump(mode="json")

    def get_job(self, job_id: str) -> "ScanJob | None":
        data = self._jobs.get(job_id)
        if data is None:
            return None
        from lti_app.models import ScanJob
        return ScanJob.model_validate(data)

    def save_report(self, report_id: str, report: "AccessibilityReport") -> None:
        self._reports[report_id] = report.model_dump(mode="json")

    def get_report(self, report_id: str) -> "AccessibilityReport | None":
        data = self._reports.get(report_id)
        if data is None:
            return None
        from lti_app.models import AccessibilityReport
        return AccessibilityReport.model_validate(data)

    def get_latest_report(self, course_id: str) -> "AccessibilityReport | None":
        from lti_app.models import AccessibilityReport
        matching = [
            AccessibilityReport.model_validate(d)
            for d in self._reports.values()
            if d.get("course_id") == course_id
        ]
        if not matching:
            return None
        return max(matching, key=lambda r: r.analyzed_at)

    def get_latest_report_id(self, course_id: str) -> "str | None":
        matching = [
            (rid, d)
            for rid, d in self._reports.items()
            if d.get("course_id") == course_id
        ]
        if not matching:
            return None
        return max(matching, key=lambda pair: pair[1].get("analyzed_at", ""))[0]


class InMemoryRemediationRepository(RemediationRepository):
    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._previews: dict[str, dict] = {}

    def save_job(self, job: "RemediationJob") -> None:
        self._jobs[job.id] = job.model_dump(mode="json")

    def get_job(self, job_id: str) -> "RemediationJob | None":
        data = self._jobs.get(job_id)
        if data is None:
            return None
        from lti_app.models import RemediationJob
        return RemediationJob.model_validate(data)

    def get_latest_job(self, course_id: str) -> "RemediationJob | None":
        from lti_app.models import RemediationJob
        matching = [
            RemediationJob.model_validate(d)
            for d in self._jobs.values()
            if d.get("course_id") == course_id
        ]
        if not matching:
            return None
        return max(matching, key=lambda j: j.created_at)

    def save_preview(self, preview: "RemediationPreview") -> None:
        doc_id = f"{preview.job_id}_{preview.page_id}"
        self._previews[doc_id] = preview.model_dump(mode="json")

    def get_preview(self, job_id: str, page_id: str) -> "RemediationPreview | None":
        doc_id = f"{job_id}_{page_id}"
        data = self._previews.get(doc_id)
        if data is None:
            return None
        from lti_app.models import RemediationPreview
        return RemediationPreview.model_validate(data)

    def get_previews(self, job_id: str) -> "list[RemediationPreview]":
        from lti_app.models import RemediationPreview
        return [
            RemediationPreview.model_validate(d)
            for d in self._previews.values()
            if d.get("job_id") == job_id
        ]


class InMemoryFileAuditRepository(FileAuditRepository):
    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._reports: dict[str, dict] = {}

    def save_job(self, job: "FileAuditJob") -> None:
        self._jobs[job.id] = job.model_dump(mode="json")

    def get_job(self, job_id: str) -> "FileAuditJob | None":
        data = self._jobs.get(job_id)
        if data is None:
            return None
        from lti_app.models import FileAuditJob
        return FileAuditJob.model_validate(data)

    def save_report(self, report_id: str, report: "FileReport") -> None:
        self._reports[report_id] = report.model_dump(mode="json")

    def get_report(self, report_id: str) -> "FileReport | None":
        data = self._reports.get(report_id)
        if data is None:
            return None
        from lti_app.models import FileReport
        return FileReport.model_validate(data)

    def get_latest_report(self, course_id: str) -> "FileReport | None":
        from lti_app.models import FileReport
        matching = [
            FileReport.model_validate(d)
            for d in self._reports.values()
            if d.get("course_id") == course_id
        ]
        if not matching:
            return None
        return max(matching, key=lambda r: r.audited_at)

    def get_latest_report_id(self, course_id: str) -> "str | None":
        matching = [
            (rid, d)
            for rid, d in self._reports.items()
            if d.get("course_id") == course_id
        ]
        if not matching:
            return None
        return max(matching, key=lambda pair: pair[1].get("audited_at", ""))[0]


class InMemoryVersionRepository(VersionRepository):
    def __init__(self):
        self._versions: dict[str, dict] = {}

    def save_version(self, version: "ContentVersion") -> None:
        self._versions[version.id] = version.model_dump(mode="json")

    def get_version(self, version_id: str) -> "ContentVersion | None":
        data = self._versions.get(version_id)
        if data is None:
            return None
        from lti_app.models import ContentVersion
        return ContentVersion.model_validate(data)

    def get_versions(self, course_id: str, page_id: str) -> "list[ContentVersion]":
        from lti_app.models import ContentVersion
        matching = [
            ContentVersion.model_validate(d)
            for d in self._versions.values()
            if d.get("course_id") == course_id and d.get("page_id") == page_id
        ]
        return sorted(matching, key=lambda v: v.created_at, reverse=True)


class InMemoryAutoRemedyRepository(AutoRemedyRepository):
    def __init__(self):
        self._jobs: dict[str, dict] = {}

    def save_job(self, job: "AutoRemedyJob") -> None:
        self._jobs[job.id] = job.model_dump(mode="json")

    def get_job(self, job_id: str) -> "AutoRemedyJob | None":
        data = self._jobs.get(job_id)
        if data is None:
            return None
        from lti_app.models import AutoRemedyJob
        return AutoRemedyJob.model_validate(data)

    def get_latest_job(self, course_id: str) -> "AutoRemedyJob | None":
        from lti_app.models import AutoRemedyJob
        matching = [
            AutoRemedyJob.model_validate(d)
            for d in self._jobs.values()
            if d.get("course_id") == course_id
        ]
        if not matching:
            return None
        return max(matching, key=lambda j: j.created_at)


class InMemoryPDFFixRepository(PDFFixRepository):
    def __init__(self):
        self._jobs: dict[str, dict] = {}

    def save_job(self, job: "PDFFixJob") -> None:
        self._jobs[job.id] = job.model_dump(mode="json")

    def get_job(self, job_id: str) -> "PDFFixJob | None":
        data = self._jobs.get(job_id)
        if data is None:
            return None
        from lti_app.models import PDFFixJob
        return PDFFixJob.model_validate(data)

    def get_jobs_for_course(self, course_id: str) -> "list[PDFFixJob]":
        from lti_app.models import PDFFixJob
        return [
            PDFFixJob.model_validate(d)
            for d in self._jobs.values()
            if d.get("course_id") == course_id
        ]


class InMemoryConversionRepository(ConversionRepository):
    def __init__(self):
        self._jobs: dict[str, dict] = {}

    def save_job(self, job: "ConversionJob") -> None:
        self._jobs[job.id] = job.model_dump(mode="json")

    def get_job(self, job_id: str) -> "ConversionJob | None":
        data = self._jobs.get(job_id)
        if data is None:
            return None
        from lti_app.models import ConversionJob
        return ConversionJob.model_validate(data)


class InMemoryOCRRepository(OCRRepository):
    def __init__(self):
        self._jobs: dict[str, dict] = {}

    def save_job(self, job: "OCRJob") -> None:
        self._jobs[job.id] = job.model_dump(mode="json")

    def get_job(self, job_id: str) -> "OCRJob | None":
        data = self._jobs.get(job_id)
        if data is None:
            return None
        from lti_app.models import OCRJob
        return OCRJob.model_validate(data)


class InMemoryCaptionRepository(CaptionRepository):
    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._videos: dict[str, dict] = {}

    def save_job(self, job: "TranscriptionJob") -> None:
        self._jobs[job.id] = job.model_dump(mode="json")

    def get_job(self, job_id: str) -> "TranscriptionJob | None":
        data = self._jobs.get(job_id)
        if data is None:
            return None
        from lti_app.models import TranscriptionJob
        return TranscriptionJob.model_validate(data)

    def get_jobs_for_course(self, course_id: str) -> "list[TranscriptionJob]":
        from lti_app.models import TranscriptionJob
        return [
            TranscriptionJob.model_validate(d)
            for d in self._jobs.values()
            if d.get("course_id") == course_id
        ]

    def get_job_by_hash(self, vtt_hash: str) -> "TranscriptionJob | None":
        from lti_app.models import TranscriptionJob
        for d in self._jobs.values():
            if d.get("vtt_hash") == vtt_hash:
                return TranscriptionJob.model_validate(d)
        return None

    def save_video(self, video: "CourseVideoRecord") -> None:
        self._videos[video.id] = video.model_dump(mode="json")

    def get_videos_for_course(self, course_id: str) -> "list[CourseVideoRecord]":
        from lti_app.models import CourseVideoRecord
        return [
            CourseVideoRecord.model_validate(d)
            for d in self._videos.values()
            if d.get("course_id") == course_id
        ]


class InMemoryACRRepository(ACRRepository):
    """In-memory implementation of ACR repository."""

    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._acrs: dict[str, dict] = {}

    def save_job(self, job: "ACRJob") -> None:
        self._jobs[job.id] = job.model_dump(mode="json")

    def get_job(self, job_id: str) -> "ACRJob | None":
        data = self._jobs.get(job_id)
        if data is None:
            return None
        from lti_app.models import ACRJob
        return ACRJob.model_validate(data)

    def save_acr(self, acr: "CourseACR") -> None:
        self._acrs[acr.id] = acr.model_dump(mode="json")

    def get_acr(self, acr_id: str) -> "CourseACR | None":
        data = self._acrs.get(acr_id)
        if data is None:
            return None
        from lti_app.models import CourseACR
        return CourseACR.model_validate(data)

    def list_acrs_for_course(self, course_id: str) -> "list[CourseACR]":
        from lti_app.models import CourseACR
        matching = [
            CourseACR.model_validate(d)
            for d in self._acrs.values()
            if d.get("course_id") == course_id
        ]
        return sorted(matching, key=lambda a: a.generated_at, reverse=True)

    def get_latest_acr(self, course_id: str) -> "CourseACR | None":
        acrs = self.list_acrs_for_course(course_id)
        return acrs[0] if acrs else None

    def delete_acr(self, acr_id: str) -> None:
        self._acrs.pop(acr_id, None)


class InMemoryExclusionRepository(ExclusionRepository):
    """In-memory exclusion storage for unit tests (CLU-85)."""

    def __init__(self):
        self._items: dict[tuple[str, str], CourseExclusion] = {}

    def add(self, exclusion: CourseExclusion) -> None:
        key = (exclusion.course_id, exclusion.item_identifier)
        # Idempotent: first insert wins, mirroring Postgres ON CONFLICT DO NOTHING
        if key not in self._items:
            self._items[key] = exclusion

    def remove(self, course_id: str, item_identifier: str) -> None:
        self._items.pop((course_id, item_identifier), None)

    def list_for_course(self, course_id: str) -> list[CourseExclusion]:
        matching = [
            ex for (cid, _), ex in self._items.items() if cid == course_id
        ]
        matching.sort(key=lambda ex: ex.excluded_at, reverse=True)
        return matching

    def get_identifiers_for_course(self, course_id: str) -> set[str]:
        return {
            identifier
            for (cid, identifier) in self._items.keys()
            if cid == course_id
        }


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------

_session_repo: SessionRepository | None = None
_registration_repo: RegistrationRepository | None = None
_settings_repo: SettingsRepository | None = None
_scan_repo: ScanRepository | None = None


def get_session_repository() -> SessionRepository:
    global _session_repo
    if _session_repo is not None:
        return _session_repo
    _session_repo = PostgresSessionRepository()
    return _session_repo


def get_registration_repository() -> RegistrationRepository:
    global _registration_repo
    if _registration_repo is not None:
        return _registration_repo
    _registration_repo = PostgresRegistrationRepository()
    return _registration_repo


def get_settings_repository() -> SettingsRepository:
    global _settings_repo
    if _settings_repo is not None:
        return _settings_repo
    _settings_repo = PostgresSettingsRepository()
    return _settings_repo


def get_scan_repository() -> ScanRepository:
    global _scan_repo
    if _scan_repo is not None:
        return _scan_repo
    _scan_repo = PostgresScanRepository()
    return _scan_repo


_remediation_repo: RemediationRepository | None = None


def get_remediation_repository() -> RemediationRepository:
    global _remediation_repo
    if _remediation_repo is not None:
        return _remediation_repo
    _remediation_repo = PostgresRemediationRepository()
    return _remediation_repo


_file_audit_repo: FileAuditRepository | None = None


def get_file_audit_repository() -> FileAuditRepository:
    global _file_audit_repo
    if _file_audit_repo is not None:
        return _file_audit_repo
    _file_audit_repo = PostgresFileAuditRepository()
    return _file_audit_repo


_version_repo: VersionRepository | None = None


def get_version_repository() -> VersionRepository:
    global _version_repo
    if _version_repo is not None:
        return _version_repo
    _version_repo = PostgresVersionRepository()
    return _version_repo


_autoremedy_repo: AutoRemedyRepository | None = None


def get_autoremedy_repository() -> AutoRemedyRepository:
    global _autoremedy_repo
    if _autoremedy_repo is not None:
        return _autoremedy_repo
    _autoremedy_repo = PostgresAutoRemedyRepository()
    return _autoremedy_repo


_pdf_fix_repo: PDFFixRepository | None = None


def get_pdf_fix_repository() -> PDFFixRepository:
    global _pdf_fix_repo
    if _pdf_fix_repo is not None:
        return _pdf_fix_repo
    _pdf_fix_repo = PostgresPDFFixRepository()
    return _pdf_fix_repo


_conversion_repo: ConversionRepository | None = None


def get_conversion_repository() -> ConversionRepository:
    global _conversion_repo
    if _conversion_repo is not None:
        return _conversion_repo
    _conversion_repo = PostgresConversionRepository()
    return _conversion_repo


_ocr_repo: OCRRepository | None = None


def get_ocr_repository() -> OCRRepository:
    global _ocr_repo
    if _ocr_repo is not None:
        return _ocr_repo
    _ocr_repo = PostgresOCRRepository()
    return _ocr_repo


_caption_repo: CaptionRepository | None = None


def get_caption_repository() -> CaptionRepository:
    global _caption_repo
    if _caption_repo is not None:
        return _caption_repo
    _caption_repo = PostgresCaptionRepository()
    return _caption_repo


_acr_repo: ACRRepository | None = None


def get_acr_repository() -> ACRRepository:
    global _acr_repo
    if _acr_repo is not None:
        return _acr_repo
    _acr_repo = PostgresACRRepository()
    return _acr_repo


_exclusion_repo: ExclusionRepository | None = None


def get_exclusion_repository() -> ExclusionRepository:
    """FastAPI dependency for the exclusion repository."""
    global _exclusion_repo
    if _exclusion_repo is not None:
        return _exclusion_repo
    _exclusion_repo = PostgresExclusionRepository()
    return _exclusion_repo
