"""OCR provider abstraction and escalation logic. Ported from Project Remedy."""

from __future__ import annotations

import asyncio
import base64
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field

from lti_app.config import get_settings

# ---------------------------------------------------------------------------
# Inline OCR prompt (from project_remedy.vision_prompts)
# ---------------------------------------------------------------------------

_LAYOUT_RULES = """\
Document remediation rules:
- Preserve a meaningful reading sequence. Do not merge unrelated columns, sidebars, callouts, or footer content.
- Keep headings separate from body text and preserve heading hierarchy.
- Keep list structures explicit instead of flattening them into paragraphs.
- Keep tables and directories explicit; do not linearize cell content into prose.
- Keep form prompts, labels, values, and widgets grouped in reading order.
- Treat purely decorative backgrounds, banners, borders, spacers, and watermarks as artifacts, not content.
- If layout intent is ambiguous, say so explicitly instead of guessing.
"""


def _ocr_markdown_prompt(*, page_hint: str = "") -> str:
    hint = f" ({page_hint})" if page_hint else ""
    return (
        f"Extract ALL content from this document{hint} as structured Markdown.\n"
        "Work from the rendered page image and preserve only visible content.\n"
        f"{_LAYOUT_RULES}"
        "Output requirements:\n"
        "- Preserve headings, paragraphs, lists, tables, captions, and form prompts in reading order.\n"
        "- For images, logos, screenshots, charts, or diagrams, output ![brief visual description](IMAGE_PLACEHOLDER).\n"
        "- Do NOT invent URLs, filenames, or missing text.\n"
        "- Keep multi-column pages column-aware.\n"
        "- Keep sidebars and callouts separate from the main article flow.\n"
        "Return compact, faithful Markdown with strict structure and no commentary."
    )


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class OCRBlock(BaseModel):
    text: str
    bbox: list[float] | None = None
    role: str = ""
    confidence: float | None = None


class OCRPageResult(BaseModel):
    provider: str = ""
    page_number: int = 0
    markdown: str
    blocks: list[OCRBlock] = Field(default_factory=list)
    confidence: float | None = None


class OCREscalationSignal(BaseModel):
    layout_class: str
    visual_block_count: int
    structured_text_nodes: int
    image_coverage: float = 0.0
    has_small_text: bool = False
    requires_rebuild: bool = False
    structure_warning: bool = False


class OCRBenchmarkResult(BaseModel):
    provider: str
    sample_count: int
    mean_block_count: float
    mean_markdown_length: float
    mean_token_overlap: float = 0.0
    mean_heading_count: float = 0.0
    mean_table_markers: float = 0.0
    empty_output_count: int = 0
    failures: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# OCRAdapter protocol
# ---------------------------------------------------------------------------


class OCRAdapter(Protocol):
    name: str

    async def extract_page(self, pdf_path: Path, page_number: int) -> OCRPageResult:
        ...


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


class OllamaModelOCRAdapter:
    """OCR adapter backed by a local Ollama multimodal model."""

    def __init__(
        self,
        model_name: str,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 300.0,
    ) -> None:
        settings = get_settings()
        self.model_name = model_name
        self.name = model_name
        self._base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self._api_key = api_key if api_key is not None else settings.ollama_api_key or "ollama"
        self._timeout_seconds = timeout_seconds

    async def extract_page(self, pdf_path: Path, page_number: int) -> OCRPageResult:
        image_b64 = _render_pdf_page_to_base64_png(pdf_path, page_number)
        payload: dict[str, Any] = {
            "model": self.model_name,
            # reasoning_effort=none disables the model's internal monologue.
            # Without this, kimi-k2.6:cloud spends most of its budget thinking
            # about the image before producing OCR output. Same root cause
            # as the document conversion fix in vision_client.py.
            "reasoning_effort": "none",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_b64}",
                            },
                        },
                        {
                            "type": "text",
                            "text": _ocr_markdown_prompt(page_hint=f"Page {page_number}"),
                        },
                    ],
                }
            ],
            "max_tokens": 8192,
            "temperature": 0.1,
        }

        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(self._timeout_seconds, connect=30.0),
        ) as client:
            response = await client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()

        markdown = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        return OCRPageResult(
            provider=self.name,
            page_number=page_number,
            markdown=markdown,
            blocks=_markdown_to_blocks(markdown),
        )


class TesseractOCRAdapter:
    """Baseline OCR adapter using local Tesseract if available."""

    name = "tesseract"

    def __init__(self, *, language: str = "eng") -> None:
        self._language = language

    async def extract_page(self, pdf_path: Path, page_number: int) -> OCRPageResult:
        tesseract = shutil.which("tesseract")
        if tesseract is None:
            raise RuntimeError("tesseract not found")

        image_bytes = _render_pdf_page_to_png_bytes(pdf_path, page_number)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_image:
            temp_path = Path(temp_image.name)
            temp_image.write(image_bytes)

        try:
            proc = await asyncio.create_subprocess_exec(
                tesseract,
                str(temp_path),
                "stdout",
                "-l",
                self._language,
                "--psm",
                "6",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                message = stderr.decode("utf-8", errors="replace").strip() or self.name
                raise RuntimeError(message)
        finally:
            temp_path.unlink(missing_ok=True)

        markdown = stdout.decode("utf-8", errors="replace").strip()
        return OCRPageResult(
            provider=self.name,
            page_number=page_number,
            markdown=markdown,
            blocks=_markdown_to_blocks(markdown),
        )


# ---------------------------------------------------------------------------
# Adapter discovery
# ---------------------------------------------------------------------------


def installed_ollama_models() -> set[str]:
    """Return model names reported by `ollama list`."""
    ollama = shutil.which("ollama")
    if ollama is None:
        return set()
    proc = subprocess.run(
        [ollama, "list"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return set()

    models: set[str] = set()
    for line in proc.stdout.splitlines()[1:]:
        parts = line.split()
        if parts:
            models.add(parts[0].strip())
    return models


def available_specialized_ocr_adapters() -> list[OCRAdapter]:
    """Return available OCR adapters: Ollama (if configured) + Tesseract fallback."""
    adapters: list[OCRAdapter] = []
    settings = get_settings()

    # Add Ollama vision model if configured
    if settings.ollama_base_url:
        models = installed_ollama_models()
        vision_model = settings.ollama_model
        if vision_model in models or settings.ollama_api_key:
            # Include if a cloud key is set (remote Ollama) or model is locally installed
            adapters.append(OllamaModelOCRAdapter(vision_model))

    # Tesseract as fallback
    if shutil.which("tesseract"):
        adapters.append(TesseractOCRAdapter())

    return adapters


# ---------------------------------------------------------------------------
# Escalation logic
# ---------------------------------------------------------------------------


def should_escalate_specialized_ocr(signal: OCREscalationSignal) -> bool:
    """Return True when the page layout is complex enough to warrant specialized OCR."""
    complex_layout = signal.layout_class in {
        "brochure_sidebar",
        "schedule_grid",
        "mixed_graphic_flyer",
        "map_infographic",
        "unknown_complex",
        "hero_cover",
        "report_cover",
    }
    if signal.requires_rebuild or signal.structure_warning:
        return True
    if signal.layout_class in {"single_column", "form_checklist", "table_directory"}:
        return False
    if signal.visual_block_count >= 8 and signal.structured_text_nodes <= 2:
        return True
    if complex_layout and signal.visual_block_count >= 6 and signal.structured_text_nodes <= 3:
        return True
    if signal.image_coverage >= 0.35 and signal.has_small_text:
        return True
    return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _render_pdf_page_to_png_bytes(pdf_path: Path, page_number: int, *, dpi: int = 200) -> bytes:
    import fitz  # type: ignore[import-untyped]

    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_number - 1]
        pix = page.get_pixmap(dpi=dpi)
        return pix.tobytes("png")
    finally:
        doc.close()


def _render_pdf_page_to_base64_png(pdf_path: Path, page_number: int, *, dpi: int = 200) -> str:
    return base64.b64encode(_render_pdf_page_to_png_bytes(pdf_path, page_number, dpi=dpi)).decode()


def _markdown_to_blocks(markdown: str) -> list[OCRBlock]:
    blocks: list[OCRBlock] = []
    for chunk in re.split(r"\n\s*\n", markdown.strip()):
        text = chunk.strip()
        if not text:
            continue
        role = "heading" if text.startswith("#") else "paragraph"
        if "|" in text and "\n" in text:
            role = "table"
        elif re.match(r"^[-*]\s+", text):
            role = "list"
        blocks.append(OCRBlock(text=text, role=role))
    return blocks
