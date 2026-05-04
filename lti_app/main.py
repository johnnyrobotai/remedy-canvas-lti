"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from lti_app.config import get_settings

_logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize and teardown resources."""
    settings = get_settings()

    settings.validate_for_startup()

    # Initialize Postgres when DATABASE_URL is available
    if settings.database_url:
        from lti_app.db.postgres import initialize_postgres

        initialize_postgres(settings.database_url)
        _sweep_orphaned_jobs()

    if settings.auth_bypass_for_local:
        _logger.info("auth_bypass_active", mode="local_development")

    # Initialize ToolConf singleton with cached registrations
    from lti_app.api.routes.lti import initialize_tool_conf
    from lti_app.db.repositories import InMemoryRegistrationRepository, get_registration_repository

    if settings.database_url:
        reg_repo = get_registration_repository()
    else:
        reg_repo = InMemoryRegistrationRepository()
        _logger.warning("registration_repo_in_memory", reason="DATABASE_URL not set")

    # Auto-seed Canvas registration if LOCAL_CANVAS_* vars are set
    if settings.local_canvas_base_url and settings.local_canvas_client_id:
        _seed_local_registration(reg_repo, settings)

    initialize_tool_conf(reg_repo)

    yield

    _logger.info("app_shutdown")


def _sweep_orphaned_jobs() -> None:
    """Mark jobs left in 'running'/'pending' by a previous process as failed.

    Async background tasks (scans, remediation, autoremedy) don't survive
    container restarts. Without this sweep they sit forever in the running
    state and the UI shows a perpetual "Fixing..." spinner (CLU-57).

    Updates both the data jsonb and the structured mirror columns so
    cleanup is consistent (relies on CLU-51 sync semantics).
    """
    import json
    from datetime import UTC, datetime as _dt
    from lti_app.db.repositories import _pg_conn

    job_tables = [
        "autoremedy_jobs",
        "remediation_jobs",
        "scan_reports",
        "scan_jobs",
        "file_audit_jobs",
        "pdf_fix_jobs",
        "conversion_jobs",
        "ocr_jobs",
        "caption_jobs",
        "acr_jobs",
    ]
    iso_now = _dt.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    sweep_msg = f"Job orphaned by app restart at {iso_now}"
    patch = json.dumps({
        "status": "failed",
        "error": sweep_msg,
        "completed_at": iso_now,
    })

    swept_total = 0
    with _pg_conn() as conn:
        for table in job_tables:
            try:
                # Skip tables that don't exist in this deployment
                exists = conn.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
                    [table],
                ).fetchone()
                if not exists:
                    continue
                rows = conn.execute(
                    f"UPDATE {table} SET data = data || %s::jsonb "
                    f"WHERE data->>'status' IN ('pending','running') RETURNING id",
                    [patch],
                ).fetchall()
                if rows:
                    # Sync the structured mirror columns from the now-updated jsonb
                    # so admin SQL against the columns sees the failed state too.
                    from lti_app.db.repositories import (
                        _get_mirror_columns,
                        _build_mirror_assignments,
                    )
                    cols = _get_mirror_columns(conn, table)
                    if cols:
                        assigns = _build_mirror_assignments(cols)
                        ids = [r[0] for r in rows]
                        conn.execute(
                            f"UPDATE {table} SET {assigns} WHERE id = ANY(%s)",
                            [ids],
                        )
                    swept_total += len(rows)
                    _logger.info(
                        "orphaned_jobs_swept",
                        table=table,
                        count=len(rows),
                    )
            except Exception as e:
                _logger.warning("orphan_sweep_failed", table=table, error=str(e))

    if swept_total:
        _logger.info("orphaned_jobs_swept_total", count=swept_total)


def _seed_local_registration(reg_repo, settings) -> None:
    """Seed a local Canvas registration if none exists.

    Reads LTI_TOOL_PRIVATE_KEY_PEM and LOCAL_CANVAS_* from settings.
    """
    from lti_app.lti.models import PlatformRegistration
    from lti_app.lti.registration import get_all_registrations, save_registration

    if get_all_registrations(reg_repo):
        _logger.info("seed_skipped", reason="registrations already exist")
        return

    private_pem = settings.lti_private_key
    if not private_pem:
        _logger.warning("seed_skipped", reason="LTI_TOOL_PRIVATE_KEY_PEM not set")
        return

    # Derive public key from private key
    try:
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
            load_pem_private_key,
        )

        pk = load_pem_private_key(private_pem.encode(), password=None)
        public_pem = pk.public_key().public_bytes(
            Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
        ).decode()
    except Exception:
        public_pem = ""

    canvas_base = settings.local_canvas_base_url
    client_id = settings.local_canvas_client_id
    deployment_id = settings.local_canvas_deployment_id

    reg = PlatformRegistration(
        id="local-canvas-dev",
        iss="https://canvas.instructure.com",
        client_id=client_id,
        deployment_id=deployment_id,
        canvas_base_url=canvas_base,
        auth_login_url=f"{canvas_base}/api/lti/authorize_redirect",
        auth_token_url=f"{canvas_base}/login/oauth2/token",
        jwks_url=f"{canvas_base}/api/lti/security/jwks",
        tool_private_key_pem=private_pem,
        tool_public_key_pem=public_pem,
    )

    save_registration(reg_repo, reg)
    _logger.info(
        "seed_local_registration",
        iss=reg.iss,
        client_id=reg.client_id,
        deployment_id=reg.deployment_id,
        canvas_base_url=reg.canvas_base_url,
    )


app = FastAPI(
    title="Remedy Canvas LTI Accessibility Tool",
    description="LTI 1.3 Canvas accessibility scanning and remediation tool",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
settings = get_settings()
if settings.allowed_hosts_list != ["*"]:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.allowed_hosts_list,
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Add baseline browser security headers without blocking Canvas iframes."""
    response = await call_next(request)
    if settings.security_headers_enabled:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
    if request.url.path.startswith("/assets/"):
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
    return response


# Routes
from lti_app.api.routes.lti import router as lti_router  # noqa: E402
from lti_app.api.routes.oauth2 import router as oauth2_router  # noqa: E402
from lti_app.api.routes.courses import router as courses_router  # noqa: E402
from lti_app.api.routes.exclusions import router as exclusions_router  # noqa: E402
from lti_app.api.routes.exports import router as exports_router  # noqa: E402
from lti_app.api.routes.remediation import router as remediation_router  # noqa: E402
from lti_app.api.routes.files import router as files_router  # noqa: E402
from lti_app.api.routes.autoremedy import router as autoremedy_router  # noqa: E402
from lti_app.api.routes.accessibility import router as accessibility_router  # noqa: E402
# Admin panel removed — WCAG 2.1 AA compliance is not configurable
# from lti_app.api.routes.admin import router as admin_router  # noqa: E402
from lti_app.api.routes.registrations import router as registrations_router  # noqa: E402
from lti_app.api.routes.dochub import router as dochub_router  # noqa: E402
from lti_app.api.routes.webhooks import router as webhooks_router  # noqa: E402
from lti_app.api.routes.acr import router as acr_router  # noqa: E402
# TODO: re-enable when Ollama captioning is stable on LACCD Canvas
# from lti_app.api.routes.captions import router as captions_router  # noqa: E402

app.include_router(lti_router)
app.include_router(oauth2_router)
app.include_router(courses_router)
app.include_router(exclusions_router)
app.include_router(exports_router)
app.include_router(remediation_router)
app.include_router(files_router)
app.include_router(autoremedy_router)
app.include_router(accessibility_router)
# app.include_router(admin_router)
app.include_router(registrations_router)
app.include_router(dochub_router)
app.include_router(webhooks_router)
app.include_router(acr_router)
# app.include_router(captions_router)  # TODO: re-enable when Ollama captioning is stable


# Health check
@app.get("/health")
async def health():
    return {"status": "ok"}


# Serve frontend static files (production: built React app)
_frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/assets", StaticFiles(directory=_frontend_dist / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve the React SPA for all non-API routes."""
        from fastapi.responses import FileResponse

        if full_path.startswith(("api/", "lti/", "health")):
            raise HTTPException(status_code=404, detail="Not found")

        index = _frontend_dist / "index.html"
        if index.exists():
            return FileResponse(index)

        raise HTTPException(status_code=404, detail="Frontend not built")
