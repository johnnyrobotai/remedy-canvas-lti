"""File audit and PDF fix API endpoints."""

import asyncio
import os
import tempfile

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lti_app.auth.dependencies import InstructorSession, verify_session_course_id
from lti_app.db.repositories import (
    ConversionRepository,
    FileAuditRepository,
    OCRRepository,
    PDFFixRepository,
    get_conversion_repository,
    get_file_audit_repository,
    get_ocr_repository,
    get_pdf_fix_repository,
)
from lti_app.models import CheckReportRef, ScanStatus
from lti_app.services.conversion_service import ConversionService
from lti_app.services.file_audit_service import FileAuditService
from lti_app.services.ocr_service import OCRService
from lti_app.services.pdf_fix_service import PDFFixService

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/courses", tags=["files"])


def _get_file_audit_service(
    file_audit_repo: FileAuditRepository = Depends(get_file_audit_repository),
) -> FileAuditService:
    return FileAuditService(file_audit_repo)


def _ensure_resource_course(resource, course_id: int) -> None:
    if str(getattr(resource, "course_id", "")) != str(course_id):
        raise HTTPException(status_code=404, detail="Resource not found")


@router.post("/{course_id}/files/scan")
async def start_file_audit(
    course_id: int,
    session: InstructorSession,
    service: FileAuditService = Depends(_get_file_audit_service),
):
    """Start a background file audit job."""
    verify_session_course_id(session, course_id)
    job = service.create_job(session.session_id, str(course_id))
    asyncio.create_task(service.run_audit(job.id, session, course_id))
    _logger.info("file_audit_started", job_id=job.id, course_id=course_id)
    return job


@router.get("/{course_id}/files/scan/{job_id}")
async def get_file_audit_status(
    course_id: int,
    job_id: str,
    session: InstructorSession,
    service: FileAuditService = Depends(_get_file_audit_service),
):
    """Poll file audit job status."""
    verify_session_course_id(session, course_id)
    job = service._repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="File audit job not found")
    _ensure_resource_course(job, course_id)
    return job


@router.get("/{course_id}/files/report")
async def get_file_report(
    course_id: int,
    session: InstructorSession,
    service: FileAuditService = Depends(_get_file_audit_service),
):
    """Get the latest file audit report for a course."""
    verify_session_course_id(session, course_id)
    report = service._repo.get_latest_report(str(course_id))
    if not report:
        raise HTTPException(status_code=404, detail="No file report found")
    return report


@router.get("/{course_id}/files/report/{file_id}")
async def get_file_check_report(
    course_id: int,
    file_id: int,
    session: InstructorSession,
    service: FileAuditService = Depends(_get_file_audit_service),
):
    """Get audit details for a single file."""
    verify_session_course_id(session, course_id)
    report = service._repo.get_latest_report(str(course_id))
    if not report:
        raise HTTPException(status_code=404, detail="No file report found")
    entry = next((e for e in report.entries if e.file_id == file_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="File not found in report")
    return entry


# ---------------------------------------------------------------------------
# PDF Fix endpoints
# ---------------------------------------------------------------------------


def _get_pdf_fix_service(
    pdf_fix_repo: PDFFixRepository = Depends(get_pdf_fix_repository),
    file_audit_repo: FileAuditRepository = Depends(get_file_audit_repository),
) -> PDFFixService:
    return PDFFixService(pdf_fix_repo, file_audit_repo)


@router.post("/{course_id}/files/{file_id}/fix")
async def start_pdf_fix(
    course_id: int,
    file_id: int,
    session: InstructorSession,
    service: FileAuditService = Depends(_get_file_audit_service),
    fix_service: PDFFixService = Depends(_get_pdf_fix_service),
):
    """Start fixing a failed PDF."""
    verify_session_course_id(session, course_id)
    # Get the check report from the latest file audit
    report = service._repo.get_latest_report(str(course_id))
    if not report:
        raise HTTPException(status_code=404, detail="No file report found")

    entry = next((e for e in report.entries if e.file_id == file_id), None)
    if not entry or not entry.is_pdf:
        raise HTTPException(status_code=404, detail="PDF not found in report")

    checks_before = entry.check_report or CheckReportRef()
    job = fix_service.create_job(session.session_id, str(course_id), file_id, entry.filename, checks_before)
    asyncio.create_task(fix_service.run_fix(job.id, session, course_id))
    return job


@router.get("/{course_id}/files/fix/{job_id}")
async def get_pdf_fix_status(
    course_id: int,
    job_id: str,
    session: InstructorSession,
    fix_service: PDFFixService = Depends(_get_pdf_fix_service),
):
    """Poll PDF fix job status."""
    verify_session_course_id(session, course_id)
    job = fix_service._repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Fix job not found")
    _ensure_resource_course(job, course_id)
    return job


class UploadRequest(BaseModel):
    mode: str  # "replace" | "alongside"


@router.post("/{course_id}/files/fix/{job_id}/upload")
async def upload_fixed_pdf(
    course_id: int,
    job_id: str,
    body: UploadRequest,
    session: InstructorSession,
    fix_service: PDFFixService = Depends(_get_pdf_fix_service),
):
    """Upload the fixed PDF to Canvas."""
    verify_session_course_id(session, course_id)
    if body.mode not in ("replace", "alongside"):
        raise HTTPException(status_code=400, detail="mode must be 'replace' or 'alongside'")
    job = fix_service._repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Fix job not found")
    _ensure_resource_course(job, course_id)
    try:
        result = await fix_service.upload_fixed(job_id, session, course_id, body.mode)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=410, detail=str(e))


# ---------------------------------------------------------------------------
# Document conversion endpoints
# ---------------------------------------------------------------------------


def _get_conversion_service(
    repo: ConversionRepository = Depends(get_conversion_repository),
) -> ConversionService:
    return ConversionService(repo)


def _get_ocr_service(
    repo: OCRRepository = Depends(get_ocr_repository),
) -> OCRService:
    return OCRService(repo)


@router.post("/{course_id}/files/{file_id}/convert")
async def start_conversion(
    course_id: int,
    file_id: int,
    session: InstructorSession,
    service: FileAuditService = Depends(_get_file_audit_service),
    conv_service: ConversionService = Depends(_get_conversion_service),
):
    """Convert a document (DOCX/PPTX/XLSX) to a Canvas wiki page.

    CLU-13: After the conversion completes, sweep all course pages and
    rewrite any links to the original file so they point at the new
    converted page. AutoRemedy phase 4 has always done this in its
    own loop, but the standalone "Convert this file" button used to
    skip the link sweep entirely — leaving stale references to the
    original file across the course.
    """
    verify_session_course_id(session, course_id)
    report = service._repo.get_latest_report(str(course_id))
    if not report:
        raise HTTPException(status_code=404, detail="No file report found")
    entry = next((e for e in report.entries if e.file_id == file_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="File not found in report")
    job = conv_service.create_job(
        session.session_id, str(course_id), file_id, entry.filename, entry.content_type,
    )

    async def _run_and_replace_links() -> None:
        try:
            await conv_service.run_conversion(job.id, session, course_id)
            completed = conv_service._repo.get_job(job.id)
            if not completed or completed.status != ScanStatus.COMPLETED:
                return
            new_page_url = completed.canvas_page_url or ""
            if not new_page_url:
                return
            from urllib.parse import urlparse
            parsed = urlparse(new_page_url)
            path_only = parsed.path or new_page_url
            await conv_service.replace_file_links_across_course(
                session=session,
                course_id=course_id,
                original_file_id=file_id,
                new_page_url=path_only,
            )
        except Exception:
            _logger.exception(
                "standalone_convert_link_sweep_failed",
                file_id=file_id,
                course_id=course_id,
            )

    asyncio.create_task(_run_and_replace_links())
    return job


@router.get("/{course_id}/files/convert/{job_id}")
async def get_conversion_status(
    course_id: int,
    job_id: str,
    session: InstructorSession,
    conv_service: ConversionService = Depends(_get_conversion_service),
):
    """Poll document conversion job status."""
    verify_session_course_id(session, course_id)
    job = conv_service._repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Conversion job not found")
    _ensure_resource_course(job, course_id)
    return job


@router.post("/{course_id}/files/{file_id}/ocr")
async def start_ocr(
    course_id: int,
    file_id: int,
    session: InstructorSession,
    service: FileAuditService = Depends(_get_file_audit_service),
    ocr_service: OCRService = Depends(_get_ocr_service),
):
    """Start OCR extraction for a scanned PDF."""
    verify_session_course_id(session, course_id)
    report = service._repo.get_latest_report(str(course_id))
    if not report:
        raise HTTPException(status_code=404, detail="No file report found")
    entry = next((e for e in report.entries if e.file_id == file_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="File not found in report")
    job = ocr_service.create_job(
        session.session_id, str(course_id), file_id, entry.filename,
    )
    asyncio.create_task(ocr_service.run_ocr(job.id, session, course_id))
    return job


@router.get("/{course_id}/files/ocr/{job_id}")
async def get_ocr_status(
    course_id: int,
    job_id: str,
    session: InstructorSession,
    ocr_service: OCRService = Depends(_get_ocr_service),
):
    """Poll OCR job status."""
    verify_session_course_id(session, course_id)
    job = ocr_service._repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="OCR job not found")
    _ensure_resource_course(job, course_id)
    return job


# ---------------------------------------------------------------------------
# Spatial parse endpoint
# ---------------------------------------------------------------------------


@router.get("/{course_id}/files/{file_id}/spatial")
async def get_spatial_parse(
    course_id: int,
    file_id: int,
    session: InstructorSession,
    service: FileAuditService = Depends(_get_file_audit_service),
):
    """Get spatial layout with bounding boxes for a file.

    Downloads the file from Canvas, parses it with LiteParse, and returns
    per-page text items with (x, y, width, height) coordinates in PDF points.
    Useful for overlaying accessibility issue locations on page screenshots.
    """
    verify_session_course_id(session, course_id)
    report = service._repo.get_latest_report(str(course_id))
    if not report:
        raise HTTPException(status_code=404, detail="No file report found")

    entry = next((e for e in report.entries if e.file_id == file_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="File not found in report")

    try:
        result = await service.get_spatial_layout(
            file_id=file_id,
            filename=entry.filename,
            session=session,
            course_id=course_id,
        )
    except Exception as exc:
        _logger.error("spatial_parse_error", file_id=file_id, error=str(exc))
        raise HTTPException(status_code=500, detail=f"Spatial parse failed: {exc}") from exc

    return result


# ---------------------------------------------------------------------------
# PDF structure editor endpoints
# ---------------------------------------------------------------------------


class StructureNodeResponse(BaseModel):
    """A single node in the PDF tag tree for the structure editor."""

    index: int
    tag: str
    depth: int
    page: int  # 0-based
    text: str
    alt_text: str
    lang: str
    children_count: int
    has_content: bool


class StructureResponse(BaseModel):
    """Full PDF structure tree response."""

    file_id: int
    filename: str
    page_count: int
    has_structure_tree: bool
    nodes: list[StructureNodeResponse]
    issues: list[dict] = []


class StructureEditItem(BaseModel):
    """A single edit to apply to the PDF tag tree."""

    node_index: int
    new_tag: str  # e.g. "H1", "H2", "P", "Artifact"


class StructureEditRequest(BaseModel):
    """Request body for applying structure edits."""

    edits: list[StructureEditItem]


class StructureEditResponse(BaseModel):
    """Response after applying structure edits."""

    edits_applied: int
    errors: list[str] = []


@router.get("/{course_id}/files/{file_id}/structure")
async def get_pdf_structure(
    course_id: int,
    file_id: int,
    session: InstructorSession,
    service: FileAuditService = Depends(_get_file_audit_service),
) -> StructureResponse:
    """Get PDF tag tree structure for the structure editor.

    Downloads the PDF from Canvas, runs the tag tree reader, and returns
    the structured result with nodes and validation issues.
    """
    verify_session_course_id(session, course_id)
    from lti_app.canvas.client import CanvasClient
    from lti_app.canvas.file_manager import FileManager
    from lti_app.core.pdf.tag_tree_reader import _extract_tag_tree, validate_tag_tree

    # Look up filename from report if available
    report = service._repo.get_latest_report(str(course_id))
    if not report:
        raise HTTPException(status_code=404, detail="No file report found")
    entry = next((e for e in report.entries if e.file_id == file_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="File not found in report")
    filename = entry.filename

    # Download from Canvas
    client = CanvasClient(
        base_url=session.canvas_base_url,
        access_token=session.canvas_access_token,
        refresh_token=getattr(session, "canvas_refresh_token", ""),
    )
    try:
        fm = FileManager(client)
        data, resolved_filename = await fm.download_file(file_id, filename)
    finally:
        await client.close()

    # Write to temp file and extract structure
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    try:
        tmp.write(data)
        tmp.close()

        from pathlib import Path

        pdf_path = Path(tmp.name)
        internal = _extract_tag_tree(pdf_path)
        validation = validate_tag_tree(pdf_path)

        nodes = [
            StructureNodeResponse(
                index=i,
                tag=node.tag,
                depth=node.depth,
                page=node.page,
                text=node.text[:500] if node.text else "",
                alt_text=node.alt_text,
                lang=node.lang,
                children_count=node.children_count,
                has_content=node.has_content,
            )
            for i, node in enumerate(internal.nodes)
        ]

        issues = [
            {
                "description": issue.description,
                "severity": issue.severity.value,
                "location": issue.location,
            }
            for issue in validation.issues
        ]

        return StructureResponse(
            file_id=file_id,
            filename=resolved_filename,
            page_count=internal.page_count,
            has_structure_tree=internal.has_structure_tree,
            nodes=nodes,
            issues=issues,
        )
    except Exception as exc:
        _logger.error("structure_read_error", file_id=file_id, error=str(exc))
        raise HTTPException(
            status_code=500, detail=f"Failed to read PDF structure: {exc}"
        ) from exc
    finally:
        os.unlink(tmp.name)


@router.post("/{course_id}/files/{file_id}/structure")
async def update_pdf_structure(
    course_id: int,
    file_id: int,
    body: StructureEditRequest,
    session: InstructorSession,
    service: FileAuditService = Depends(_get_file_audit_service),
) -> StructureEditResponse:
    """Apply structure edits to a PDF (re-tag, edit heading levels, mark artifacts).

    Downloads the PDF, applies tag modifications, re-uploads to Canvas.
    """
    verify_session_course_id(session, course_id)
    import pikepdf
    from lti_app.canvas.client import CanvasClient
    from lti_app.canvas.file_manager import FileManager
    from lti_app.core.pdf.checker import _get_struct_type, walk_structure_tree

    # Look up filename
    report = service._repo.get_latest_report(str(course_id))
    if not report:
        raise HTTPException(status_code=404, detail="No file report found")
    entry = next((e for e in report.entries if e.file_id == file_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="File not found in report")
    filename = entry.filename

    # Download from Canvas
    client = CanvasClient(
        base_url=session.canvas_base_url,
        access_token=session.canvas_access_token,
        refresh_token=getattr(session, "canvas_refresh_token", ""),
    )
    try:
        fm = FileManager(client)
        data, resolved_filename = await fm.download_file(file_id, filename)
    finally:
        await client.close()

    # Apply edits
    tmp_in = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp_out = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    errors: list[str] = []
    edits_applied = 0

    try:
        tmp_in.write(data)
        tmp_in.close()
        tmp_out.close()

        # Build edit map: node_index -> new_tag
        edit_map: dict[int, str] = {e.node_index: e.new_tag for e in body.edits}

        with pikepdf.open(tmp_in.name, allow_overwriting_input=True) as pdf:
            struct_root = pdf.Root.get("/StructTreeRoot")
            if struct_root is None:
                raise HTTPException(
                    status_code=400,
                    detail="PDF has no structure tree — cannot edit tags",
                )

            # Walk nodes and apply edits by index
            idx = 0
            for node, depth, parent in walk_structure_tree(pdf):
                tag = _get_struct_type(node)
                if not tag or tag == "StructTreeRoot":
                    continue

                if idx in edit_map:
                    new_tag = edit_map[idx]
                    try:
                        if new_tag == "Artifact":
                            # Mark as artifact by removing /S and setting role
                            node["/S"] = pikepdf.Name("/Artifact")
                        else:
                            node["/S"] = pikepdf.Name(f"/{new_tag}")
                        edits_applied += 1
                    except Exception as exc:
                        errors.append(
                            f"Failed to change node {idx} to {new_tag}: {exc}"
                        )
                idx += 1

            pdf.save(tmp_out.name)

        # Re-upload to Canvas
        with open(tmp_out.name, "rb") as f:
            edited_data = f.read()

        client2 = CanvasClient(
            base_url=session.canvas_base_url,
            access_token=session.canvas_access_token,
            refresh_token=getattr(session, "canvas_refresh_token", ""),
        )
        try:
            fm2 = FileManager(client2)
            folders = await fm2.get_course_folders(course_id)
            root_folder = next(
                (f for f in folders if f.get("parent_folder_id") is None),
                folders[0] if folders else None,
            )
            if not root_folder:
                raise HTTPException(
                    status_code=500,
                    detail="Could not find course root folder for upload",
                )
            await fm2.upload_file(
                course_id,
                root_folder["id"],
                resolved_filename,
                edited_data,
                "application/pdf",
            )
        finally:
            await client2.close()

        return StructureEditResponse(
            edits_applied=edits_applied,
            errors=errors,
        )

    except HTTPException:
        raise
    except Exception as exc:
        _logger.error("structure_edit_error", file_id=file_id, error=str(exc))
        raise HTTPException(
            status_code=500, detail=f"Failed to edit PDF structure: {exc}"
        ) from exc
    finally:
        if os.path.exists(tmp_in.name):
            os.unlink(tmp_in.name)
        if os.path.exists(tmp_out.name):
            os.unlink(tmp_out.name)


# ---------------------------------------------------------------------------
# External document link scanning
# ---------------------------------------------------------------------------


@router.get("/{course_id}/files/external-links")
async def scan_external_links(
    course_id: int,
    session: InstructorSession,
):
    """Scan course HTML for links to external documents (PDF, DOCX, PPTX, XLSX).

    Downloads all course page content, scans for anchor tags pointing to
    document files, and returns the list of discovered links with metadata.
    """
    verify_session_course_id(session, course_id)
    from urllib.parse import urlparse

    from lti_app.canvas.client import CanvasClient
    from lti_app.canvas.content_fetcher import ContentFetcher
    from lti_app.core.accessibility.link_scanner import scan_for_document_links

    client = CanvasClient(
        base_url=session.canvas_base_url,
        access_token=session.canvas_access_token,
        refresh_token=getattr(session, "canvas_refresh_token", ""),
    )
    try:
        fetcher = ContentFetcher(client)
        pages = await fetcher.fetch_all(course_id)

        # Derive canvas domain from session base URL
        parsed = urlparse(session.canvas_base_url)
        canvas_domain = parsed.netloc or ""

        links = scan_for_document_links(pages, canvas_domain=canvas_domain)

        _logger.info(
            "external_links_scanned",
            course_id=course_id,
            pages_scanned=len(pages),
            links_found=len(links),
        )

        return {
            "course_id": str(course_id),
            "pages_scanned": len(pages),
            "links": [link.model_dump() for link in links],
            "total": len(links),
            "external_count": sum(1 for link in links if link.is_external),
            "internal_count": sum(1 for link in links if not link.is_external),
        }
    finally:
        await client.close()


class ExternalLinkProcessRequest(BaseModel):
    url: str
    filename: str


@router.post("/{course_id}/files/external-links/process")
async def process_external_link(
    course_id: int,
    body: ExternalLinkProcessRequest,
    session: InstructorSession,
):
    """Download an external document and upload it to Canvas.

    The document is placed in an 'External Documents' folder in the course.
    """
    verify_session_course_id(session, course_id)
    from lti_app.services.external_link_service import ExternalLinkService

    svc = ExternalLinkService()
    job = await svc.process_link(body.url, body.filename, session, course_id)

    if job.status.value == "failed":
        raise HTTPException(status_code=502, detail=job.error or "Processing failed")

    return job


# ---------------------------------------------------------------------------
# Alternative format generation
# ---------------------------------------------------------------------------


@router.get("/{course_id}/pages/{page_id}/alt-formats")
async def list_alt_formats(
    course_id: int,
    page_id: str,
    session: InstructorSession,
):
    """List available alternative formats for a page."""
    verify_session_course_id(session, course_id)
    from lti_app.core.documents.alt_formats import AltFormatGenerator

    return {"formats": list(AltFormatGenerator.SUPPORTED_FORMATS)}


@router.get("/{course_id}/pages/{page_id}/alt-formats/{format_type}")
async def get_alt_format(
    course_id: int,
    page_id: str,
    format_type: str,
    session: InstructorSession,
):
    """Download a page in an alternative format (html, text, epub).

    Fetches the page HTML from Canvas and converts it to the requested
    format. Returns the file as a download attachment.
    """
    verify_session_course_id(session, course_id)
    from fastapi.responses import Response

    from lti_app.canvas.client import CanvasClient
    from lti_app.core.documents.alt_formats import AltFormatGenerator

    generator = AltFormatGenerator()

    if format_type not in generator.SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format: {format_type}. Supported: {', '.join(generator.SUPPORTED_FORMATS)}",
        )

    # Fetch page content from Canvas
    client = CanvasClient(
        base_url=session.canvas_base_url,
        access_token=session.canvas_access_token,
        refresh_token=getattr(session, "canvas_refresh_token", ""),
    )
    try:
        page_data = await client.get(
            f"/api/v1/courses/{course_id}/pages/{page_id}"
        )
    except Exception as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Could not fetch page {page_id}: {exc}",
        ) from exc
    finally:
        await client.close()

    html = page_data.get("body") or ""
    if not html.strip():
        raise HTTPException(status_code=404, detail="Page has no HTML content")

    title = page_data.get("title", "")

    alt_format = generator.generate(format_type, html, title=title)

    return Response(
        content=alt_format.content,
        media_type=alt_format.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{alt_format.filename}"',
        },
    )
