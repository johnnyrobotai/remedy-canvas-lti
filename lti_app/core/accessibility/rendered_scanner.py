"""Rendered page accessibility scanner using Playwright + axe-core.

Scans fully-rendered Canvas pages in headless Chromium, detecting issues
that the content-body scanner misses (CSS contrast, Canvas UI, forms, etc.).

Uses a shared BrowserPool with max 2 concurrent scans.
"""

import asyncio
from pathlib import Path
from typing import Optional

import httpx
import structlog
from playwright.async_api import async_playwright, Browser, Playwright

from lti_app.core.accessibility.axe_mapper import axe_violation_to_issues
from lti_app.models import AccessibilityIssue

_logger = structlog.get_logger(__name__)

# Vendored axe-core JS — Docker path first, then local dev fallback
AXE_JS_PATH = Path("/app/vendor/axe.min.js")
if not AXE_JS_PATH.exists():
    AXE_JS_PATH = Path(__file__).parent.parent.parent.parent / "vendor" / "axe.min.js"

# Canvas page URL patterns by content type
CONTENT_URLS = {
    "WIKI_PAGE": "/courses/{course_id}/pages/{identifier}",
    "ASSIGNMENT": "/courses/{course_id}/assignments/{identifier}",
    "DISCUSSION": "/courses/{course_id}/discussion_topics/{identifier}",
    "QUIZ": "/courses/{course_id}/quizzes/{identifier}",
    "NEW_QUIZ": "/courses/{course_id}/quizzes/{identifier}",
    "ANNOUNCEMENT": "/courses/{course_id}/discussion_topics/{identifier}",
    "SYLLABUS": "/courses/{course_id}/assignments/syllabus",
}


async def get_canvas_session_url(
    canvas_base_url: str, access_token: str, return_to: str
) -> str:
    """Convert Canvas API token to a one-time browser session URL.

    Canvas provides /login/session_token which takes an API token and returns
    a one-time URL that establishes a browser session when navigated to.
    """
    async with httpx.AsyncClient(base_url=canvas_base_url, timeout=30) as http:
        resp = await http.get(
            "/login/session_token",
            params={"return_to": return_to},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()["session_url"]


class BrowserPool:
    """Shared Chromium instance with concurrent scan limiting.

    One persistent Chromium process shared across all users.
    Each scan gets an isolated BrowserContext (own cookies, own Canvas session).
    Max 2 concurrent scans — additional requests queue.
    """

    _pw: Optional[Playwright] = None
    _browser: Optional[Browser] = None
    _semaphore = asyncio.Semaphore(2)
    _lock = asyncio.Lock()
    _queue_count: int = 0

    @classmethod
    async def get_browser(cls) -> Browser:
        """Get or launch the shared Chromium instance."""
        if cls._browser is None or not cls._browser.is_connected():
            async with cls._lock:
                if cls._browser is None or not cls._browser.is_connected():
                    cls._pw = await async_playwright().start()
                    cls._browser = await cls._pw.chromium.launch(
                        headless=True,
                        args=["--disable-dev-shm-usage", "--no-sandbox"],
                    )
                    _logger.info("browser_pool_started")
        return cls._browser

    @classmethod
    def get_queue_position(cls) -> int:
        """Return current number of scans waiting for a slot."""
        return cls._queue_count

    @classmethod
    async def scan_page(
        cls,
        canvas_base_url: str,
        access_token: str,
        canvas_path: str,
        page_id: str,
    ) -> list[AccessibilityIssue]:
        """Acquire a semaphore slot, render page, run axe-core, return issues."""
        cls._queue_count += 1
        try:
            async with cls._semaphore:
                cls._queue_count -= 1
                browser = await cls.get_browser()
                context = await browser.new_context(
                    viewport={"width": 1440, "height": 1000},
                    bypass_csp=True,
                )
                try:
                    page = await context.new_page()
                    full_url = f"{canvas_base_url}{canvas_path}"

                    # Get one-time session URL from Canvas
                    session_url = await get_canvas_session_url(
                        canvas_base_url, access_token, full_url
                    )

                    # Navigate — Canvas logs in and redirects to target page
                    await page.goto(session_url, wait_until="domcontentloaded")

                    # Wait for Canvas content area (don't use networkidle — Canvas keeps polling)
                    await page.wait_for_selector(
                        "main, #content, .ic-Layout-contentMain",
                        timeout=15000,
                    )
                    await page.wait_for_timeout(750)  # Late JS hydration settle

                    # Inject axe-core
                    if not AXE_JS_PATH.exists():
                        _logger.error("axe_js_not_found", path=str(AXE_JS_PATH))
                        return []
                    await page.add_script_tag(path=str(AXE_JS_PATH))

                    # Run axe with WCAG 2.2 AA tags
                    axe_results = await page.evaluate(
                        """async () => await axe.run(document, {
                            runOnly: {
                                type: "tag",
                                values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]
                            },
                            resultTypes: ["violations"]
                        })"""
                    )

                    # Convert violations to Remedy Canvas LTI issues
                    issues = []
                    for violation in axe_results.get("violations", []):
                        issues.extend(
                            axe_violation_to_issues(violation, page_id, full_url)
                        )

                    _logger.info(
                        "rendered_scan_page_complete",
                        page=canvas_path,
                        issues=len(issues),
                    )
                    return issues

                except Exception as e:
                    _logger.error(
                        "rendered_scan_page_failed",
                        page=canvas_path,
                        error=str(e),
                    )
                    return []
                finally:
                    await context.close()
        except Exception:
            cls._queue_count = max(0, cls._queue_count - 1)
            raise

    @classmethod
    async def shutdown(cls):
        """Clean up browser resources."""
        if cls._browser:
            await cls._browser.close()
            cls._browser = None
        if cls._pw:
            await cls._pw.stop()
            cls._pw = None
            _logger.info("browser_pool_stopped")
