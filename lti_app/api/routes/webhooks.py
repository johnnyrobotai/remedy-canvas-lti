"""Canvas webhook receiver for real-time content change scanning."""

import base64
import hashlib
import hmac
import json

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from lti_app.config import Settings, get_settings

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
_logger = structlog.get_logger(__name__)

# Canvas content events that should trigger a scan
_CONTENT_EVENTS = frozenset({
    "wiki_page_created",
    "wiki_page_updated",
    "assignment_created",
    "assignment_updated",
})


@router.post("/canvas")
async def receive_canvas_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    """Receive Canvas webhook events for content changes.

    Canvas sends data subscription webhooks as POST requests with JSON payloads.
    The ``X-Canvas-Webhook-Signature`` header may be verified if a shared secret
    is configured.
    """
    body = await request.body()
    if settings.canvas_webhook_secret:
        signature = request.headers.get("x-canvas-webhook-signature", "")
        if not _is_valid_signature(settings.canvas_webhook_secret, body, signature):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = payload.get("event_type", "")
    course_id = payload.get("course_id")

    _logger.info("webhook_received", event_type=event_type, course_id=course_id)

    # Queue a scan if content changed
    if event_type in _CONTENT_EVENTS and course_id:
        _logger.info(
            "webhook_scan_triggered",
            course_id=course_id,
            event_type=event_type,
        )
        # In production, this would check admin settings for auto-scan
        # and create a scan job via ScanService if enabled.

    return {"received": True, "event_type": event_type}


def _is_valid_signature(secret: str, body: bytes, signature: str) -> bool:
    if not signature:
        return False

    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected_hex = digest.hex()
    expected_b64 = base64.b64encode(digest).decode("ascii")
    candidates = {
        expected_hex,
        f"sha256={expected_hex}",
        expected_b64,
        f"sha256={expected_b64}",
    }
    return any(hmac.compare_digest(signature, candidate) for candidate in candidates)


@router.get("/canvas/status")
async def webhook_status():
    """Check webhook endpoint status."""
    return {"status": "active", "endpoint": "/api/webhooks/canvas"}
