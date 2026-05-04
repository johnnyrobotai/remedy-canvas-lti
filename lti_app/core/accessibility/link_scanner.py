"""External document link scanner.

Scans course HTML content to find links to external documents
(PDFs, DOCX, PPTX, XLSX) that may need accessibility remediation.
"""

import re
from urllib.parse import urlparse

from pydantic import BaseModel


DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls"}


class ExternalDocLink(BaseModel):
    """A link to an external document found in course HTML."""

    url: str
    page_id: str
    page_title: str
    filename: str
    extension: str
    is_external: bool = True  # not hosted on Canvas


def scan_for_document_links(
    pages: list,
    canvas_domain: str = "",
) -> list[ExternalDocLink]:
    """Scan course pages for links to external documents.

    Args:
        pages: List of CoursePage models to scan.
        canvas_domain: The Canvas instance domain (e.g. "laccd.instructure.com").
            Links whose netloc contains this domain are marked ``is_external=False``.

    Returns:
        List of ExternalDocLink models found across all pages.
    """
    from bs4 import BeautifulSoup

    links: list[ExternalDocLink] = []

    for page in pages:
        html = getattr(page, "html_content", "") or ""
        if not html.strip():
            continue

        soup = BeautifulSoup(html, "html.parser")

        for a in soup.find_all("a", href=True):
            href = a["href"]
            parsed = urlparse(href)
            path = parsed.path.lower()

            # Extract extension from the path
            ext = ""
            if "." in path:
                ext = "." + path.rsplit(".", 1)[-1]

            if ext not in DOCUMENT_EXTENSIONS:
                continue

            # Determine if the link is external (not on Canvas)
            is_external = bool(parsed.netloc) and (
                not canvas_domain or canvas_domain not in parsed.netloc
            )

            # Extract filename from the path
            filename = path.rsplit("/", 1)[-1] if "/" in path else path

            links.append(
                ExternalDocLink(
                    url=href,
                    page_id=getattr(page, "id", ""),
                    page_title=getattr(page, "title", ""),
                    filename=filename,
                    extension=ext,
                    is_external=is_external,
                )
            )

    return links
