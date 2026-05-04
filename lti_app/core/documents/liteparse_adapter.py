"""LiteParse adapter — spatial document parsing with bounding boxes."""

import json

from pydantic import BaseModel
from liteparse import LiteParse

# ---------------------------------------------------------------------------
# CLU-83 monkey-patch: upstream liteparse (tested through v1.4.6) drops
# stderr on the JSONDecodeError path in ``_get_parse_result()``.  The
# non-zero-exit branch passes ``stderr=stderr.decode()``, but the JSON
# branch does ``raise ParseError(f"...: {e}")`` without it.  That means
# when the Node CLI exits 0 but emits empty/invalid stdout (e.g. a .docx
# that LibreOffice silently failed to convert), the ParseError exception
# has no stderr attribute — losing critical diagnostic information.
#
# This patch replaces the static method at import time.  It is safe to
# remove once upstream ships a release that includes the fix.
# Track upstream: https://github.com/run-llama/liteparse
# ---------------------------------------------------------------------------
try:
    from liteparse.parser import _parse_json_result  # type: ignore
    from liteparse.types import ParseError as _UpstreamParseError  # type: ignore

    @staticmethod  # type: ignore[misc]
    def _patched_get_parse_result(
        returncode: int,
        stdout: bytes,
        stderr: bytes,
    ):  # -> ParseResult (avoid circular import)
        if returncode != 0:
            raise _UpstreamParseError(
                f"Parsing failed with exit code {returncode}",
                stderr=stderr.decode("utf-8"),
            )
        try:
            json_data = json.loads(stdout.decode("utf-8"))
            return _parse_json_result(json_data)
        except json.JSONDecodeError as e:
            raise _UpstreamParseError(
                f"Failed to parse CLI output: {e}",
                stderr=stderr.decode("utf-8"),  # CLU-83 fix
            )

    LiteParse._get_parse_result = _patched_get_parse_result  # type: ignore[assignment]
except ImportError:
    # liteparse not installed or internal structure changed — the adapter's
    # getattr(exc, "stderr", "") fallback still handles the unpatched case.
    pass

# Import ParseError so we can catch it narrowly and surface its ``stderr``
# attribute to the downstream error handler.
try:
    from liteparse.types import ParseError as LiteParseError  # type: ignore
except ImportError:
    try:
        from liteparse import ParseError as LiteParseError  # type: ignore
    except ImportError:
        # Defensive fallback — tests monkey-patch this symbol, and production
        # paths always succeed with one of the two imports above.
        LiteParseError = Exception  # type: ignore


class TextItem(BaseModel):
    """A text element with spatial position and (optionally) font metadata."""

    text: str
    x: float
    y: float
    width: float
    height: float
    page_num: int
    font_name: str | None = None
    font_size: float | None = None


class PageLayout(BaseModel):
    """Spatial layout of a single page."""

    page_num: int
    width: float
    height: float
    text: str
    text_items: list[TextItem] = []


class SpatialParseResult(BaseModel):
    """Full document parse result with spatial data."""

    text: str
    pages: list[PageLayout] = []
    source_format: str = ""
    total_text_items: int = 0


_FMT_MAP = {
    "pdf": "pdf",
    "docx": "docx",
    "pptx": "pptx",
    "xlsx": "xlsx",
    "png": "image",
    "jpg": "image",
    "jpeg": "image",
}


class LiteParseAdapter:
    """Wraps LiteParse for spatial document parsing.

    The real LiteParse API accepts ``ocr_enabled`` and ``dpi`` as per-call
    keyword arguments on ``parse()``, not as constructor parameters.  We store
    them as instance defaults so callers can treat the adapter like a
    configured parser without changing call sites.
    """

    def __init__(self, ocr_enabled: bool = True, dpi: int = 150):
        self._lp = LiteParse()
        self._ocr_enabled = ocr_enabled
        self._dpi = dpi

    def parse_file(self, file_path: str) -> SpatialParseResult:
        """Parse a document file and return spatial result."""
        try:
            result = self._lp.parse(
                file_path,
                ocr_enabled=self._ocr_enabled,
                dpi=self._dpi,
            )
        except LiteParseError as exc:
            stderr_detail = (getattr(exc, "stderr", "") or "").strip()
            if stderr_detail:
                raise RuntimeError(
                    f"LiteParse failed for '{file_path}': {exc}\n"
                    f"CLI stderr: {stderr_detail[:2000]}"
                ) from exc
            raise RuntimeError(
                f"LiteParse failed for '{file_path}': {exc} "
                f"(no stderr captured — likely upstream JSON decode path; "
                f"see CLU-83 for diagnosis notes)"
            ) from exc
        return self._convert_result(result, file_path)

    def parse_bytes(self, data: bytes, filename: str) -> SpatialParseResult:
        """Parse document bytes and return spatial result.

        LiteParse accepts raw bytes as the first positional argument to
        ``parse()``; the filename is used for format detection only.
        """
        try:
            result = self._lp.parse(
                data,
                ocr_enabled=self._ocr_enabled,
                dpi=self._dpi,
            )
        except LiteParseError as exc:
            stderr_detail = (getattr(exc, "stderr", "") or "").strip()
            if stderr_detail:
                raise RuntimeError(
                    f"LiteParse failed for '{filename}': {exc}\n"
                    f"CLI stderr: {stderr_detail[:2000]}"
                ) from exc
            raise RuntimeError(
                f"LiteParse failed for '{filename}': {exc} "
                f"(no stderr captured — likely upstream JSON decode path; "
                f"see CLU-83 for diagnosis notes)"
            ) from exc
        return self._convert_result(result, filename)

    def _convert_result(self, result, source: str) -> SpatialParseResult:
        """Convert LiteParse result to our model."""
        pages: list[PageLayout] = []
        total_items = 0

        for page in (getattr(result, "pages", None) or []):
            page_num = getattr(page, "pageNum", 0)
            items: list[TextItem] = []
            for item in (getattr(page, "textItems", None) or []):
                # LiteParse provides font metadata as fontName/fontSize on each
                # text item; pass them through so DocumentToHTMLService can
                # build a font-size → heading-level map. Both are optional —
                # PDFs typically expose them, OCR'd images may not.
                font_size_raw = getattr(item, "fontSize", None)
                items.append(TextItem(
                    text=getattr(item, "text", ""),
                    x=getattr(item, "x", 0.0),
                    y=getattr(item, "y", 0.0),
                    width=getattr(item, "width", 0.0),
                    height=getattr(item, "height", 0.0),
                    page_num=page_num,
                    font_name=getattr(item, "fontName", None),
                    font_size=float(font_size_raw) if font_size_raw is not None else None,
                ))
            total_items += len(items)
            pages.append(PageLayout(
                page_num=page_num,
                width=getattr(page, "width", 0.0),
                height=getattr(page, "height", 0.0),
                text=getattr(page, "text", "") or "",
                text_items=items,
            ))

        ext = source.rsplit(".", 1)[-1].lower() if "." in source else ""

        return SpatialParseResult(
            text=getattr(result, "text", "") or "",
            pages=pages,
            source_format=_FMT_MAP.get(ext, ext),
            total_text_items=total_items,
        )
