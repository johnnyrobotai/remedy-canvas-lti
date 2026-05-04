"""Service for creating remediated IMSCC exports.

Patches Canvas's IMSCC export with live (remediated) page content.
The standard Canvas export contains the original HTML from before
Remedy Canvas LTI fixed accessibility issues via the Canvas API. This service
downloads the IMSCC, replaces each wiki page's <body> with the current
live content, patches the syllabus, and re-zips the result.

CLU-71 fix: The `GET /api/v1/courses/{id}/pages/{slug}` endpoint returns
HTML with absolute Canvas file URLs (`https://host/courses/X/files/Y/preview`),
but a spec-compliant IMSCC must use `$IMS-CC-FILEBASE$/...` placeholders
that point into the bundled `web_resources/` tree. We rewrite live body
HTML before dropping it into the IMSCC.
"""

import asyncio
import re
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import quote, unquote

import httpx
import structlog
from bs4 import BeautifulSoup

from lti_app.canvas.client import CanvasClient
from lti_app.lti.models import LTISession

_logger = structlog.get_logger(__name__)

# Concurrency limit for page-body fetches (avoids hammering Canvas API)
_FETCH_CONCURRENCY = 10

# Matches Canvas file URLs in any of these shapes (with optional host prefix):
#
#   /courses/{id}/files/{fid}
#   /courses/{id}/files/{fid}/preview
#   /courses/{id}/files/{fid}/download
#   /courses/{id}/files/{fid}/download/{filename}
#   /files/{fid}                           (bare — no /courses/ prefix)
#   /api/v1/courses/{id}/files/{fid}       (data-api-endpoint shape)
#
# The host prefix is stripped when present. Query strings (`?wrap=1`,
# `?download_frd=1`, `?verifier=...`) are allowed at the end. The match
# captures the numeric file_id in the `file_id` group.
_CANVAS_FILE_URL_RE = re.compile(
    r"""
    (?:https?://[^/\s"'>]+)?       # optional host
    (?:/api/v1)?                   # optional /api/v1 prefix
    (?:/courses/\d+)?              # optional /courses/{id} prefix
    /files/(?P<file_id>\d+)        # /files/{fid}  (capture)
    (?:/(?:preview|download)       # optional /preview or /download
        (?:/[^?\s"'>]*)?           #   optional filename tail after /download
    )?
    (?:\?[^"'>\s]*)?               # optional ?query=string
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Matches `url(...)` references in inline CSS `style` attributes.
# Captures the inner URL string (strips optional quotes).
_CSS_URL_RE = re.compile(r"""url\(\s*["']?([^)"']+)["']?\s*\)""")


# CLU-81: Matches Canvas wiki page URLs in these shapes:
#
#   /courses/{id}/pages/{slug}
#   /courses/{id}/pages/{slug}?query=...
#   /courses/{id}/pages/{slug}#hash
#   https://host/courses/{id}/pages/{slug}
#
# The match captures `course_id`, `slug`, and `tail` (optional
# query/hash). The `/courses/{id}/` prefix is REQUIRED whenever a
# host is present — this ensures that external URLs merely
# containing `/pages/` in their path (e.g.,
# `https://example.com/help/pages/index`) do NOT match. The only
# way to match without a host is a path-relative URL that itself
# starts with `/courses/{id}/pages/`.
#
# Anchored with ^ and $ — attribute values are matched as full
# strings, not substring-searched.
_CANVAS_PAGE_URL_RE = re.compile(
    r"""
    ^                                   # anchored at start
    (?:https?://[^/\s"'>]+)?            # optional host
    /courses/(?P<course_id>\d+)         # REQUIRED /courses/{id} prefix
    /pages/(?P<slug>[^?#"\s'>]+)        # /pages/{slug}
    (?P<tail>[?#][^"'>\s]*)?            # optional query/hash tail
    $                                   # anchored at end
    """,
    re.IGNORECASE | re.VERBOSE,
)


# Matches Canvas by-path file references:
#
#   /courses/{id}/file_contents/<folder>/<filename>
#
# This shape is emitted by the Canvas RCE "files repository" browser and
# references a file by its logical folder path instead of numeric id.
# The first path segment is usually the root folder name (e.g.,
# `course%20files/`), which must be stripped to produce a valid IMSCC
# filebase path.
#
# Captures the full relative path (without query string) in `rel_path`.
_CANVAS_BY_PATH_RE = re.compile(
    r"""
    (?:https?://[^/\s"'>]+)?       # optional host
    /courses/\d+/file_contents/    # /courses/{id}/file_contents/
    (?P<rel_path>[^?\s"'>]+)       # the relative path (captured)
    (?:\?[^"'>\s]*)?               # optional query string
    """,
    re.IGNORECASE | re.VERBOSE,
)


# Matches Canvas canvadoc_session URLs:
#
#   /api/v1/canvadoc_session?blob=<json>&hmac=<hmac>
#
# These are session-bound document preview URLs with HMAC-signed blob
# payloads containing `attachment_id`. They are inherently ephemeral
# and cross-instance broken — the attachment_id is from the ORIGINAL
# Canvas instance, not the one we're exporting from. We detect them to
# count as unresolved file references (so users see the warning) but
# cannot rewrite them to IMSCC placeholders.
_CANVADOC_SESSION_RE = re.compile(
    r"""
    (?:https?://[^/\s"'>]+)?       # optional host
    /api/v1/canvadoc_session       # the canvadoc session endpoint
    (?:\?[^"'>\s]*)?               # optional ?blob=...&hmac=...
    """,
    re.IGNORECASE | re.VERBOSE,
)


class RemediatedExportService:
    """Creates IMSCC exports patched with live (remediated) page content."""

    async def create_export(self, session: LTISession, course_id: int) -> dict:
        """Create a remediated IMSCC export.

        Full pipeline:
        1. Trigger Canvas IMSCC export
        2. Poll until complete
        3. Download the IMSCC file
        4. Unzip to temp directory
        5. Patch wiki_content/*.html with live page bodies
        6. Patch course_settings/syllabus.html with live syllabus
        7. Re-zip as remediated.imscc

        Returns dict with status, patched_pages count, and download_path.
        """
        client = CanvasClient(
            base_url=session.canvas_base_url,
            access_token=session.canvas_access_token,
            refresh_token=getattr(session, "canvas_refresh_token", ""),
        )

        try:
            # Step 0: Fetch course name for the download filename (CLU-78).
            # Sourcing the name from Canvas at export time — NOT from the
            # LTI session at download time — avoids a cross-tab cookie
            # shadowing bug where a concurrent LTI launch in another tab
            # would overwrite the cookie and the download endpoint would
            # label course A's bytes with course B's name.
            course_name = ""
            try:
                course_info = await client.get(f"/api/v1/courses/{course_id}")
                course_name = (course_info.get("name") or "").strip()
            except Exception as exc:
                _logger.warning(
                    "remediated_export_course_name_fetch_failed",
                    course_id=course_id,
                    error=str(exc),
                )

            # Step 1: Trigger Canvas export
            _logger.info("remediated_export_starting", course_id=course_id)
            export = await client.post(
                f"/api/v1/courses/{course_id}/content_exports",
                body={"export_type": "common_cartridge", "skip_notifications": True},
            )
            export_id = export["id"]

            # Step 2: Poll until complete
            export_data = await self._poll_export(client, course_id, export_id)

            # Step 3: Download IMSCC
            tmp_dir = Path(tempfile.mkdtemp(prefix="clu-export-"))
            imscc_path = await self._download_imscc(
                export_data, session.canvas_access_token, tmp_dir
            )

            # Step 4: Unzip
            extract_dir = tmp_dir / "extracted"
            with zipfile.ZipFile(imscc_path, "r") as zf:
                zf.extractall(extract_dir)

            # Step 4a: Build the file_id map + path map from Canvas
            # folders + files. Used in the patch steps below to rewrite
            # absolute Canvas file URLs back to IMSCC filebase
            # placeholders (CLU-71 core + CLU-71 expansion for by-path).
            file_map, path_map, files_skipped = await self._build_file_id_map(
                client, course_id, extract_dir
            )

            # Step 5: Patch wiki pages (returns patched count + unresolved refs)
            patched_count, wiki_unresolved = await self._patch_wiki_pages(
                client, course_id, extract_dir, file_map, path_map
            )

            # Step 6: Patch syllabus (returns patched count + unresolved refs)
            syllabus_count, syllabus_unresolved = await self._patch_syllabus(
                client, course_id, extract_dir, file_map, path_map
            )
            patched_count += syllabus_count
            unresolved_file_refs = wiki_unresolved + syllabus_unresolved

            # Step 7: Re-zip
            patched_path = tmp_dir / "remediated.imscc"
            self._create_zip(extract_dir, patched_path)

            _logger.info(
                "remediated_export_complete",
                course_id=course_id,
                patched_pages=patched_count,
                unresolved_file_refs=unresolved_file_refs,
                files_skipped_from_map=files_skipped,
                path=str(patched_path),
            )

            return {
                "status": "complete",
                "course_name": course_name,
                "patched_pages": patched_count,
                "unresolved_file_refs": unresolved_file_refs,
                "download_path": str(patched_path),
            }

        finally:
            await client.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _poll_export(
        self, client: CanvasClient, course_id: int, export_id: int
    ) -> dict:
        """Poll Canvas until the export is ready or has failed."""
        while True:
            status = await client.get(
                f"/api/v1/courses/{course_id}/content_exports/{export_id}"
            )
            state = status.get("workflow_state", "")
            if state == "exported":
                return status
            if state == "failed":
                raise RuntimeError(f"Canvas export failed: {status}")
            await asyncio.sleep(2)

    async def _download_imscc(
        self, export_data: dict, access_token: str, tmp_dir: Path
    ) -> Path:
        """Download the IMSCC zip from Canvas to a temp file."""
        attachment = export_data.get("attachment", {})
        download_url = attachment.get("url", "")
        if not download_url:
            raise RuntimeError("No download URL in Canvas export response")

        imscc_path = tmp_dir / "original.imscc"
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as http:
            resp = await http.get(
                download_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            imscc_path.write_bytes(resp.content)

        _logger.info(
            "remediated_export_downloaded",
            path=str(imscc_path),
            size=imscc_path.stat().st_size,
        )
        return imscc_path

    async def _patch_wiki_pages(
        self,
        client: CanvasClient,
        course_id: int,
        extract_dir: Path,
        file_map: dict[int, str],
        path_map: dict[str, str] | None = None,
    ) -> tuple[int, int]:
        """Replace <body> content in wiki_content/*.html with live page bodies.

        Returns `(patched_count, unresolved_file_refs_total)`. Each live
        body is run through `_relativize_canvas_file_urls` before being
        written into the IMSCC so that absolute Canvas file URLs become
        `$IMS-CC-FILEBASE$/...` placeholders (CLU-71).
        """
        wiki_dir = extract_dir / "wiki_content"
        if not wiki_dir.exists():
            return 0, 0

        html_files = list(wiki_dir.glob("*.html"))
        if not html_files:
            return 0, 0

        _logger.info("remediated_export_patching_pages", count=len(html_files))

        # Build a lookup of slug -> html_file
        # IMSCC filenames may be URL-encoded (e.g., "my%20page.html")
        slug_to_file: dict[str, Path] = {}
        for f in html_files:
            # Decode URL-encoded characters in the filename
            slug = unquote(f.stem)
            slug_to_file[slug] = f

        # Fetch all page slugs from Canvas (paginated) to know which pages exist
        canvas_slugs: set[str] = set()
        async for page in client.get_paginated(
            f"/api/v1/courses/{course_id}/pages",
        ):
            slug = page.get("url", "")
            if slug:
                canvas_slugs.add(slug)

        # Fetch bodies concurrently and patch files
        semaphore = asyncio.Semaphore(_FETCH_CONCURRENCY)

        async def fetch_and_patch(slug: str, html_file: Path) -> tuple[bool, int]:
            """Fetch live body + patch file. Returns (patched, unresolved_count)."""
            if slug not in canvas_slugs:
                return False, 0

            async with semaphore:
                try:
                    full_page = await client.get(
                        f"/api/v1/courses/{course_id}/pages/{slug}"
                    )
                    live_body = full_page.get("body", "")
                    if not live_body:
                        return False, 0

                    # CLU-71: rewrite absolute Canvas file URLs to
                    # $IMS-CC-FILEBASE$ placeholders before dropping
                    # the body into the IMSCC. Handles /files/{id},
                    # /file_contents/<path>, and canvadoc_session
                    # shapes (last two added in the expansion after
                    # Codex NO-GO on initial validation).
                    relativized_body, unresolved = (
                        self._relativize_canvas_file_urls(
                            live_body,
                            file_map,
                            path_map=path_map,
                            current_course_id=course_id,
                        )
                    )

                    _replace_body_content(html_file, relativized_body)
                    return True, unresolved
                except Exception as exc:
                    _logger.warning(
                        "remediated_export_patch_page_failed",
                        slug=slug,
                        error=str(exc),
                    )
                    return False, 0

        results = await asyncio.gather(
            *[fetch_and_patch(slug, f) for slug, f in slug_to_file.items()]
        )
        patched_count = sum(1 for patched, _ in results if patched)
        unresolved_total = sum(u for _, u in results)
        return patched_count, unresolved_total

    async def _patch_syllabus(
        self,
        client: CanvasClient,
        course_id: int,
        extract_dir: Path,
        file_map: dict[int, str],
        path_map: dict[str, str] | None = None,
    ) -> tuple[int, int]:
        """Patch course_settings/syllabus.html with the live syllabus body.

        Returns `(patched_count, unresolved_file_refs)`. Same CLU-71
        treatment as wiki pages — relativize Canvas file URLs before
        writing.
        """
        syllabus_file = extract_dir / "course_settings" / "syllabus.html"
        if not syllabus_file.exists():
            return 0, 0

        try:
            course_data = await client.get(
                f"/api/v1/courses/{course_id}",
                params={"include[]": "syllabus_body"},
            )
            syllabus_body = course_data.get("syllabus_body", "")
            if not syllabus_body:
                return 0, 0

            # CLU-71: relativize Canvas file URLs in the syllabus body
            # CLU-81: + relativize Canvas page URLs (same-course guard)
            relativized_body, unresolved = self._relativize_canvas_file_urls(
                syllabus_body,
                file_map,
                path_map=path_map,
                current_course_id=course_id,
            )
            _replace_body_content(syllabus_file, relativized_body)
            return 1, unresolved
        except Exception as exc:
            _logger.warning(
                "remediated_export_syllabus_patch_failed",
                error=str(exc),
            )
            return 0, 0

    # ------------------------------------------------------------------
    # CLU-71 — Canvas file URL → $IMS-CC-FILEBASE$ relativization
    # ------------------------------------------------------------------

    async def _build_file_id_map(
        self,
        client: CanvasClient,
        course_id: int,
        extract_dir: Path,
    ) -> tuple[dict[int, str], dict[str, str], int]:
        """Build the file_id map and the by-path map for IMSCC rewriting.

        Paginates Canvas folders + files, derives each file's IMSCC
        filebase path relative to the root folder (using the root
        folder's dynamic name — never hardcoded), URL-encodes per path
        segment with `urllib.parse.quote`, and verifies the resulting
        path exists on disk in `extract_dir/web_resources/` before
        registering the mapping (`verify-first`).

        Probes both `display_name` and `filename` before declaring a
        miss — Canvas can diverge these on rename/duplicate flows.

        Returns `(file_map, path_map, skipped_count)`:

        - `file_map`: `{file_id: "$IMS-CC-FILEBASE$/path"}` for
          Canvas `/files/{id}` URL rewriting.
        - `path_map`: `{"decoded/rel/path": "$IMS-CC-FILEBASE$/path"}`
          for Canvas `/file_contents/<folder>/<name>` by-path URL
          rewriting. Keys are the decoded relative path EXCLUDING the
          root folder prefix (so they match what the by-path handler
          produces after stripping the root segment).
        - `skipped_count`: files where neither `display_name` nor
          `filename` resolved on disk. Skipped files are omitted from
          both maps; any HTML references to them will fall through as
          `unresolved_file_refs` and surface to the user via the
          export status API.
        """
        # Step 1: fetch all folders, identify the root folder dynamically
        folders_by_id: dict[int, dict] = {}
        root_folder_name: str | None = None
        async for folder in client.get_paginated(
            f"/api/v1/courses/{course_id}/folders"
        ):
            folders_by_id[folder["id"]] = folder
            # The root folder has no parent_folder_id
            if folder.get("parent_folder_id") is None:
                root_folder_name = folder.get("name") or folder.get("full_name")

        if root_folder_name is None:
            _logger.warning(
                "remediated_export_no_root_folder",
                course_id=course_id,
                folder_count=len(folders_by_id),
            )
            return {}, {}, 0

        # Step 2: fetch all files and build both maps
        file_map: dict[int, str] = {}
        path_map: dict[str, str] = {}
        skipped = 0
        web_resources = extract_dir / "web_resources"

        async for file_obj in client.get_paginated(
            f"/api/v1/courses/{course_id}/files"
        ):
            file_id = file_obj.get("id")
            if file_id is None:
                continue
            display_name = file_obj.get("display_name", "") or ""
            filename = file_obj.get("filename", "") or ""
            folder_id = file_obj.get("folder_id")

            # Resolve the folder path segments relative to the root folder.
            # Root-folder files produce an EMPTY list → the path collapses
            # to `$IMS-CC-FILEBASE$/<filename>` with no folder segment.
            folder_path_parts: list[str] = []
            if folder_id is not None and folder_id in folders_by_id:
                folder = folders_by_id[folder_id]
                full_name = folder.get("full_name", "") or ""
                if full_name == root_folder_name:
                    folder_path_parts = []
                elif full_name.startswith(root_folder_name + "/"):
                    sub_path = full_name[len(root_folder_name) + 1:]
                    folder_path_parts = (
                        sub_path.split("/") if sub_path else []
                    )
                else:
                    # Unexpected shape — log and treat as root
                    _logger.warning(
                        "remediated_export_unexpected_folder_full_name",
                        file_id=file_id,
                        full_name=full_name,
                        root=root_folder_name,
                    )
                    folder_path_parts = []
            # folder_id=None → root (empty folder_path_parts)

            # Verify-first: probe BOTH display_name and filename on disk
            # before declaring the file resolvable. Canvas can diverge
            # these on rename/duplicate flows.
            resolved_name: str | None = None
            for candidate in (display_name, filename):
                if not candidate:
                    continue
                disk_path = web_resources
                for part in folder_path_parts:
                    disk_path = disk_path / part
                disk_path = disk_path / candidate
                if disk_path.is_file():
                    resolved_name = candidate
                    break

            if resolved_name is None:
                _logger.info(
                    "remediated_export_file_not_in_web_resources",
                    course_id=course_id,
                    file_id=file_id,
                    display_name=display_name,
                    filename=filename,
                    folder_path="/".join(folder_path_parts),
                )
                skipped += 1
                continue

            # URL-encode per segment with `quote` (NOT `quote_plus`).
            # `quote_plus` turns spaces into `+` which is invalid in
            # IMSCC filebase paths. `quote` with safe="" encodes every
            # non-alphanumeric except the unreserved set.
            encoded_parts = [quote(p, safe="") for p in folder_path_parts]
            encoded_name = quote(resolved_name, safe="")
            path_segments = encoded_parts + [encoded_name]
            filebase_path = "$IMS-CC-FILEBASE$/" + "/".join(path_segments)
            file_map[file_id] = filebase_path

            # Also register in the by-path map, keyed by the decoded
            # relative path (without the root folder prefix). This is
            # what Canvas's /file_contents/<path> URLs reference after
            # the root segment is stripped.
            decoded_path = "/".join(folder_path_parts + [resolved_name])
            path_map[decoded_path] = filebase_path

        _logger.info(
            "remediated_export_file_map_built",
            course_id=course_id,
            resolved=len(file_map),
            by_path_resolved=len(path_map),
            skipped=skipped,
            root_folder=root_folder_name,
        )
        return file_map, path_map, skipped

    def _rewrite_page_url(
        self, val: str, current_course_id: int | None
    ) -> str:
        """Rewrite same-course absolute or relative Canvas page URLs to
        `$WIKI_REFERENCE$/pages/{slug}` form. Leaves cross-course and
        non-Canvas URLs untouched.

        CLU-81: without this, absolute projectclu.com page URLs (left
        behind by CLU-13's file-convert link sweep) get exported
        verbatim into the IMSCC and break when the cartridge is
        reimported to a different Canvas instance.
        """
        match = _CANVAS_PAGE_URL_RE.match(val)
        if not match:
            return val
        matched_course_id_str = match.group("course_id")
        if (
            matched_course_id_str is not None
            and current_course_id is not None
            and int(matched_course_id_str) != current_course_id
        ):
            # Cross-course reference — leave alone. These are external
            # links the author deliberately pointed at another course
            # and Canvas will not resolve `$WIKI_REFERENCE$` to them.
            return val
        slug = match.group("slug")
        tail = match.group("tail") or ""
        return f"$WIKI_REFERENCE$/pages/{slug}{tail}"

    def _relativize_canvas_file_urls(
        self,
        live_body_html: str,
        file_map: dict[int, str],
        path_map: dict[str, str] | None = None,
        current_course_id: int | None = None,
    ) -> tuple[str, int]:
        """Rewrite absolute Canvas file URLs to `$IMS-CC-FILEBASE$` placeholders.

        Walks every element whose attribute can reference a Canvas file:
            - `<img src>`, `<img data-api-endpoint>`
            - `<a href>`, `<a data-api-endpoint>`
            - `<source src>`, `<audio src>`, `<video src>`, `<video poster>`
            - `<object data>`
            - `<iframe src>`
            - inline CSS `url(...)` in `style` attributes

        Recognizes THREE URL shapes (checked in priority order):

        1. `/files/{id}` (with optional `/preview`, `/download`, etc.)
           → looked up in `file_map` → rewritten or counted as unresolved
        2. `/file_contents/<folder>/<filename>` (Canvas by-path shape)
           → decoded, prefix-stripped, looked up in `path_map` →
           rewritten or counted as unresolved
        3. `/api/v1/canvadoc_session?blob=...&hmac=...` (session-bound
           document previews) → unfixable (HMAC-signed, cross-instance
           attachment_id), counted as unresolved but left in place

        Also strips `data-api-endpoint` and `data-api-returntype` after
        processing — these are meaningless in an IMSCC and get
        re-resolved by the importing Canvas instance anyway.

        Returns `(rewritten_html, unresolved_count)`. The unresolved
        total surfaces to the user via `unresolved_file_refs` in the
        export status API so they can find + manually fix affected
        pages.
        """
        if not live_body_html:
            return live_body_html, 0

        soup = BeautifulSoup(live_body_html, "html.parser")
        unresolved = 0
        path_map = path_map or {}

        def _resolve_by_path(rel_path_encoded: str) -> str | None:
            """Given a URL-encoded relative path from /file_contents/,
            decode it, strip the root folder prefix if present, and
            return the matching `$IMS-CC-FILEBASE$/...` placeholder
            from `path_map`, or None if not found.

            We don't know the root folder name at rewrite time (it's
            only in `_build_file_id_map`), so we try three strategies:
              1. Strip any common root folder name prefix
                 (`course files/`, `group files/`, `user files/`)
              2. Use the decoded path as-is (in case the caller
                 already stripped the prefix)
              3. Try every known root prefix from `path_map` keys
            """
            if not path_map:
                return None
            from urllib.parse import unquote
            decoded = unquote(rel_path_encoded)

            # Candidates: decoded as-is, plus common prefix-stripped
            # variants. Canvas typically uses "course files/" as the
            # root folder name, but the dynamic detection in
            # _build_file_id_map also handles "group files/" and
            # "user files/" contexts.
            candidates = [decoded]
            for prefix in ("course files/", "group files/", "user files/"):
                if decoded.startswith(prefix):
                    candidates.append(decoded[len(prefix):])

            for candidate in candidates:
                if candidate in path_map:
                    return path_map[candidate]
            return None

        def rewrite_attr(tag, attr_name: str) -> None:
            """Rewrite a single attribute if it holds a Canvas file URL.

            Checks the three URL shapes in priority order. Each shape
            either succeeds (rewrites the attribute) or falls through
            (counts as unresolved, leaves URL alone).
            """
            nonlocal unresolved
            val = tag.get(attr_name, "")
            if not val:
                return

            # Shape 1: /files/{id} — numeric file_id lookup
            match = _CANVAS_FILE_URL_RE.search(val)
            if match:
                try:
                    file_id = int(match.group("file_id"))
                except (TypeError, ValueError):
                    return
                if file_id in file_map:
                    tag[attr_name] = file_map[file_id]
                else:
                    unresolved += 1
                    _logger.debug(
                        "remediated_export_unresolved_file_ref",
                        shape="files_id",
                        file_id=file_id,
                        url=val[:120],
                    )
                return

            # Shape 2: /file_contents/<path> — Canvas by-path reference
            match = _CANVAS_BY_PATH_RE.search(val)
            if match:
                rel_path = match.group("rel_path")
                resolved = _resolve_by_path(rel_path)
                if resolved is not None:
                    tag[attr_name] = resolved
                else:
                    unresolved += 1
                    _logger.debug(
                        "remediated_export_unresolved_file_ref",
                        shape="by_path",
                        rel_path=rel_path[:120],
                    )
                return

            # Shape 3: canvadoc_session — ephemeral, unfixable
            match = _CANVADOC_SESSION_RE.search(val)
            if match:
                unresolved += 1
                _logger.debug(
                    "remediated_export_unresolved_file_ref",
                    shape="canvadoc_session",
                    url=val[:120],
                )
                # Leave URL alone (can't rewrite session-bound URLs)
                return

            # Not a Canvas file URL (external, mailto, pages link, etc.)

        # Image / media elements that can carry file URLs
        for tag in soup.find_all(["img", "source", "audio", "video"]):
            if tag.has_attr("src"):
                rewrite_attr(tag, "src")
            if tag.has_attr("poster"):
                rewrite_attr(tag, "poster")

        # Anchor tags — most commonly Canvas file_link pointing to PDFs/DOCXs
        for tag in soup.find_all("a"):
            if tag.has_attr("href"):
                rewrite_attr(tag, "href")

        # Object embeds (PDFs, etc.)
        for tag in soup.find_all("object"):
            if tag.has_attr("data"):
                rewrite_attr(tag, "data")

        # Iframe embeds (Canvas sometimes uses these for PDF previews)
        for tag in soup.find_all("iframe"):
            if tag.has_attr("src"):
                rewrite_attr(tag, "src")

        # Inline CSS `url(...)` in style attributes — handles all three
        # URL shapes (files/id, file_contents by-path, canvadoc_session).
        for tag in soup.find_all(style=True):
            style = tag.get("style", "") or ""
            if "url(" not in style.lower():
                continue

            def css_sub(m: re.Match[str]) -> str:
                nonlocal unresolved
                inner_url = m.group(1)

                # Shape 1: /files/{id}
                file_match = _CANVAS_FILE_URL_RE.search(inner_url)
                if file_match:
                    try:
                        fid = int(file_match.group("file_id"))
                    except (TypeError, ValueError):
                        return m.group(0)
                    if fid in file_map:
                        return f'url("{file_map[fid]}")'
                    unresolved += 1
                    return m.group(0)

                # Shape 2: /file_contents/<path>
                path_match = _CANVAS_BY_PATH_RE.search(inner_url)
                if path_match:
                    resolved = _resolve_by_path(path_match.group("rel_path"))
                    if resolved is not None:
                        return f'url("{resolved}")'
                    unresolved += 1
                    return m.group(0)

                # Shape 3: canvadoc_session
                if _CANVADOC_SESSION_RE.search(inner_url):
                    unresolved += 1
                    return m.group(0)

                return m.group(0)

            new_style = _CSS_URL_RE.sub(css_sub, style)
            if new_style != style:
                tag["style"] = new_style

        # CLU-81: second pass — rewrite absolute/relative Canvas wiki
        # page URLs (`/courses/{id}/pages/{slug}`) to the IMSCC
        # `$WIKI_REFERENCE$/pages/{slug}` placeholder so the cartridge
        # is portable across Canvas instances. This handles the output
        # of CLU-13's file-convert link sweep, which stores absolute
        # projectclu.com URLs when replacing converted file links.
        #
        # Pages that matched the file URL regex in the first pass
        # won't match _CANVAS_PAGE_URL_RE (different path segment),
        # so the two passes do not conflict.
        def rewrite_page_attr(tag, attr_name: str) -> None:
            val = tag.get(attr_name, "")
            if not val:
                return
            new_val = self._rewrite_page_url(val, current_course_id)
            if new_val != val:
                tag[attr_name] = new_val

        for tag in soup.find_all(["img", "source", "audio", "video"]):
            if tag.has_attr("src"):
                rewrite_page_attr(tag, "src")
            if tag.has_attr("poster"):
                rewrite_page_attr(tag, "poster")
        for tag in soup.find_all("a"):
            if tag.has_attr("href"):
                rewrite_page_attr(tag, "href")
        for tag in soup.find_all("object"):
            if tag.has_attr("data"):
                rewrite_page_attr(tag, "data")
        for tag in soup.find_all("iframe"):
            if tag.has_attr("src"):
                rewrite_page_attr(tag, "src")

        # Inline CSS `url(...)` — handle page URLs in background-image
        # and similar. Rare for page URLs to appear in CSS, but cover it
        # for consistency with the file URL pass above.
        for tag in soup.find_all(style=True):
            style = tag.get("style", "") or ""
            if "url(" not in style.lower():
                continue

            def page_css_sub(m: re.Match[str]) -> str:
                inner_url = m.group(1)
                new_url = self._rewrite_page_url(
                    inner_url, current_course_id
                )
                if new_url != inner_url:
                    return f'url("{new_url}")'
                return m.group(0)

            new_style = _CSS_URL_RE.sub(page_css_sub, style)
            if new_style != style:
                tag["style"] = new_style

        # Strip data-api-* attributes (meaningless in an IMSCC; Canvas
        # re-resolves them on import).
        for attr_name in ("data-api-endpoint", "data-api-returntype"):
            for tag in list(soup.find_all(attrs={attr_name: True})):
                del tag[attr_name]

        return str(soup), unresolved

    @staticmethod
    def _create_zip(source_dir: Path, output_path: Path) -> None:
        """Zip the extracted directory back into an IMSCC file."""
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in sorted(source_dir.rglob("*")):
                if file_path.is_file():
                    arcname = file_path.relative_to(source_dir)
                    zf.write(file_path, arcname)


def _replace_body_content(html_file: Path, new_body_html: str) -> None:
    """Replace the inner content of <body> in an IMSCC HTML file.

    Preserves <head> metadata (identifier, title, editing_roles, etc.).
    """
    original_html = html_file.read_text(encoding="utf-8")
    soup = BeautifulSoup(original_html, "html.parser")
    body_tag = soup.find("body")
    if not body_tag:
        return

    body_tag.clear()
    new_content = BeautifulSoup(new_body_html, "html.parser")
    for child in list(new_content.children):
        body_tag.append(child)

    html_file.write_text(str(soup), encoding="utf-8")
