"""DocHub standalone mode -- upload and remediate documents without Canvas."""

import os
import tempfile

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from ulid import ULID

from lti_app.auth.dependencies import get_current_session
from lti_app.config import Settings, get_settings

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/dochub", tags=["dochub"])


class DocHubUploadResult(BaseModel):
    id: str
    filename: str
    file_type: str
    size: int
    action: str  # "checked", "fixed", "converted"
    # PDF check results (if PDF)
    checks_before: dict | None = None
    checks_after: dict | None = None
    fixes_applied: list[str] = []
    # Conversion results (if document)
    converted_html: str = ""


async def require_dochub_access(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> None:
    """Require an LTI session unless standalone DocHub is explicitly enabled."""
    if not settings.dochub_requires_auth:
        return
    from lti_app.db.repositories import get_session_repository

    await get_current_session(request, settings, get_session_repository())


@router.post(
    "/upload",
    response_model=DocHubUploadResult,
    dependencies=[Depends(require_dochub_access)],
)
async def upload_and_process(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
):
    """Upload a document for standalone accessibility processing."""
    data = await file.read()
    if len(data) > settings.dochub_max_upload_bytes:
        raise HTTPException(status_code=413, detail="Uploaded file is too large")

    filename = file.filename or "upload"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    result = DocHubUploadResult(
        id=str(ULID()),
        filename=filename,
        file_type=ext,
        size=len(data),
        action="checked",
    )

    if ext == "pdf":
        await _process_pdf(data, result)
    elif ext in ("docx", "pptx", "xlsx"):
        await _process_document(data, ext, filename, result)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: .{ext}. Supported: pdf, docx, pptx, xlsx",
        )

    return result


async def _process_pdf(data: bytes, result: DocHubUploadResult) -> None:
    """Check and fix a PDF document."""
    from lti_app.core.pdf.checker import PDFAccessibilityChecker
    from lti_app.core.pdf.fixer import fix_all

    tmp_in = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp_in.write(data)
    tmp_in.close()
    tmp_out = tempfile.mktemp(suffix=".pdf")

    try:
        checker = PDFAccessibilityChecker(tmp_in.name)
        before = checker.run_all(file_id=result.id, filename=result.filename)
        result.checks_before = {
            "passed": before.passed,
            "failed": before.failed,
            "total": before.total_checks,
            "pass_rate": before.pass_rate,
        }

        fix_report = fix_all(tmp_in.name, tmp_out)
        result.fixes_applied = fix_report.fixes_applied
        result.action = "fixed"

        if os.path.exists(tmp_out):
            checker2 = PDFAccessibilityChecker(tmp_out)
            after = checker2.run_all(file_id=result.id, filename=result.filename)
            result.checks_after = {
                "passed": after.passed,
                "failed": after.failed,
                "total": after.total_checks,
                "pass_rate": after.pass_rate,
            }
    except Exception as exc:
        _logger.error("dochub_pdf_error", filename=result.filename, error=str(exc))
        raise HTTPException(status_code=500, detail=f"PDF processing failed: {exc}") from exc
    finally:
        if os.path.exists(tmp_in.name):
            os.unlink(tmp_in.name)
        if os.path.exists(tmp_out):
            os.unlink(tmp_out)


async def _process_document(
    data: bytes, ext: str, filename: str, result: DocHubUploadResult
) -> None:
    """Extract and convert a document (DOCX/PPTX/XLSX) to accessible HTML."""
    from lti_app.core.documents.liteparse_adapter import LiteParseAdapter
    from lti_app.core.documents.llm_converter import DocumentToHTMLService

    try:
        adapter = LiteParseAdapter()
        spatial = adapter.parse_bytes(data, filename)

        service = DocumentToHTMLService()
        title = filename.rsplit(".", 1)[0] if "." in filename else filename
        result.converted_html = await service.convert(spatial, title=title)
        result.action = "converted"
    except Exception as exc:
        _logger.error("dochub_convert_error", filename=filename, error=str(exc))
        raise HTTPException(
            status_code=500, detail=f"Document conversion failed: {exc}"
        ) from exc


@router.get("/health")
async def dochub_health(settings: Settings = Depends(get_settings)):
    """Check DocHub standalone endpoint status."""
    return {
        "status": "ok",
        "mode": "standalone",
        "auth_required": settings.dochub_requires_auth,
    }
