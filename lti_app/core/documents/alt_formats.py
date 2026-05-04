"""Alternative format generation -- HTML, plain text, ePub.

Generates accessible alternative formats from HTML content so that
students can download course pages in their preferred format.
"""

import html as html_module
import io
import uuid
import zipfile

from pydantic import BaseModel


class AlternativeFormat(BaseModel):
    """A generated alternative format of a page."""

    format_type: str  # "html", "text", "epub"
    content: bytes
    filename: str
    content_type: str


class AltFormatGenerator:
    """Generate alternative accessible formats from HTML content."""

    SUPPORTED_FORMATS = ("html", "text", "epub")

    def generate(
        self,
        format_type: str,
        html: str,
        title: str = "",
    ) -> AlternativeFormat:
        """Generate an alternative format from HTML.

        Args:
            format_type: One of "html", "text", "epub".
            html: Source HTML content.
            title: Document title.

        Returns:
            AlternativeFormat with the generated content.

        Raises:
            ValueError: If format_type is not supported.
        """
        if format_type == "html":
            return self.generate_html(html, title)
        elif format_type == "text":
            return self.generate_text(html, title)
        elif format_type == "epub":
            return self.generate_epub(html, title)
        else:
            raise ValueError(
                f"Unsupported format: {format_type}. "
                f"Supported: {', '.join(self.SUPPORTED_FORMATS)}"
            )

    def generate_text(self, html: str, title: str = "") -> AlternativeFormat:
        """Convert HTML to plain text.

        Uses BeautifulSoup to extract readable text from HTML, with
        double-newline separators between elements for readability.
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator="\n\n", strip=True)

        if title:
            text = f"{title}\n{'=' * len(title)}\n\n{text}"

        safe_title = _safe_filename(title or "document")
        filename = f"{safe_title}.txt"

        return AlternativeFormat(
            format_type="text",
            content=text.encode("utf-8"),
            filename=filename,
            content_type="text/plain; charset=utf-8",
        )

    def generate_html(self, html: str, title: str = "") -> AlternativeFormat:
        """Wrap HTML content in a standalone accessible HTML document.

        Produces a self-contained HTML5 page with sensible default
        styles, proper ``lang`` attribute, responsive viewport, and
        accessible table styling.
        """
        title_escaped = html_module.escape(title or "Document")

        standalone = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title_escaped}</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 800px; margin: 2rem auto; padding: 0 1rem; line-height: 1.6; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ccc; padding: 0.5rem; text-align: left; }}
th {{ background: #f5f5f5; }}
img {{ max-width: 100%; height: auto; }}
</style>
</head>
<body>
{html}
</body>
</html>"""

        safe_title = _safe_filename(title or "document")
        filename = f"{safe_title}.html"

        return AlternativeFormat(
            format_type="html",
            content=standalone.encode("utf-8"),
            filename=filename,
            content_type="text/html; charset=utf-8",
        )

    def generate_epub(self, html: str, title: str = "") -> AlternativeFormat:
        """Generate a minimal EPUB 3 from HTML content.

        Produces a valid EPUB archive with:
        - mimetype (uncompressed, as required by EPUB spec)
        - META-INF/container.xml
        - OEBPS/content.opf (package document)
        - OEBPS/content.xhtml (the actual content)
        - OEBPS/nav.xhtml (navigation document)
        """
        book_id = str(uuid.uuid4())
        title_escaped = html_module.escape(title or "Document")

        container_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            "<rootfiles>"
            '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles>"
            "</container>"
        )

        content_opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="3.0">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="BookId">{book_id}</dc:identifier>
<dc:title>{title_escaped}</dc:title>
<dc:language>en</dc:language>
</metadata>
<manifest>
<item id="content" href="content.xhtml" media-type="application/xhtml+xml"/>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
</manifest>
<spine><itemref idref="content"/></spine>
</package>"""

        content_xhtml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
<head><meta charset="utf-8"/><title>{title_escaped}</title></head>
<body>
{html}
</body>
</html>"""

        nav_xhtml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en">
<head><meta charset="utf-8"/><title>Navigation</title></head>
<body>
<nav epub:type="toc"><h1>Contents</h1><ol><li><a href="content.xhtml">{title_escaped}</a></li></ol></nav>
</body>
</html>"""

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # mimetype MUST be first entry and uncompressed per EPUB spec
            zf.writestr(
                "mimetype",
                "application/epub+zip",
                compress_type=zipfile.ZIP_STORED,
            )
            zf.writestr("META-INF/container.xml", container_xml)
            zf.writestr("OEBPS/content.opf", content_opf)
            zf.writestr("OEBPS/content.xhtml", content_xhtml)
            zf.writestr("OEBPS/nav.xhtml", nav_xhtml)

        safe_title = _safe_filename(title or "document")
        filename = f"{safe_title}.epub"

        return AlternativeFormat(
            format_type="epub",
            content=buf.getvalue(),
            filename=filename,
            content_type="application/epub+zip",
        )


def _safe_filename(title: str) -> str:
    """Sanitize a title for use as a filename.

    Replaces spaces with underscores, removes non-alphanumeric characters
    (except underscores and hyphens), and truncates to 100 characters.
    """
    import re

    safe = re.sub(r"[^\w\s-]", "", title)
    safe = re.sub(r"\s+", "_", safe.strip())
    return safe[:100] or "document"
