"""Scanned PDF OCR orchestrator."""

import os
import tempfile
from datetime import UTC, datetime
import structlog
from ulid import ULID
import fitz  # PyMuPDF

from lti_app.canvas.client import CanvasClient
from lti_app.canvas.file_manager import FileManager
from lti_app.core.pdf.ocr import (
    OllamaModelOCRAdapter,
    TesseractOCRAdapter,
)
from lti_app.config import get_settings
from lti_app.db.repositories import OCRRepository
from lti_app.models import OCRJob, ScanStatus

_logger = structlog.get_logger(__name__)


class OCRService:
    def __init__(self, repo: OCRRepository):
        self._repo = repo

    def create_job(self, session_id, course_id, file_id, filename):
        job = OCRJob(
            id=str(ULID()), course_id=course_id, session_id=session_id,
            file_id=file_id, filename=filename,
            created_at=datetime.now(UTC),
        )
        self._repo.save_job(job)
        return job

    async def run_ocr(self, job_id, session, course_id):
        job = self._repo.get_job(job_id)
        if not job:
            return

        try:
            job.status = ScanStatus.RUNNING
            self._repo.save_job(job)

            client = CanvasClient(
                base_url=session.canvas_base_url,
                access_token=session.canvas_access_token,
                refresh_token=getattr(session, "canvas_refresh_token", ""),
            )

            try:
                fm = FileManager(client)
                data, _ = await fm.download_file(job.file_id, job.filename)

                tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
                tmp.write(data)
                tmp.close()

                try:
                    # Count pages
                    doc = fitz.open(tmp.name)
                    job.pages_total = len(doc)
                    doc.close()
                    self._repo.save_job(job)

                    # Try Ollama OCR first, fall back to Tesseract
                    adapter = None
                    try:
                        settings = get_settings()
                        adapter = OllamaModelOCRAdapter(settings.ollama_model)
                    except Exception:
                        try:
                            adapter = TesseractOCRAdapter()
                        except Exception:
                            raise RuntimeError("No OCR provider available (tried Ollama, Tesseract)")

                    # Extract each page
                    from pathlib import Path
                    pages_md = []
                    for page_num in range(job.pages_total):
                        try:
                            result = await adapter.extract_page(Path(tmp.name), page_num)
                            pages_md.append(result.markdown)
                        except Exception as e:
                            pages_md.append(f"<!-- OCR failed for page {page_num + 1}: {e} -->")
                            _logger.warning("ocr_page_failed", page=page_num, error=str(e))

                        job.pages_processed = page_num + 1
                        job.progress = job.pages_processed / max(job.pages_total, 1)
                        self._repo.save_job(job)

                    job.extracted_markdown = "\n\n---\n\n".join(pages_md)
                    job.status = ScanStatus.COMPLETED
                    job.completed_at = datetime.now(UTC)
                    self._repo.save_job(job)

                finally:
                    os.unlink(tmp.name)

            finally:
                await client.close()

        except Exception as e:
            _logger.error("ocr_failed", job_id=job_id, error=str(e))
            job.status = ScanStatus.FAILED
            job.error = str(e)
            job.completed_at = datetime.now(UTC)
            self._repo.save_job(job)
