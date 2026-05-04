"""Blueprint course support -- sync accessibility fixes to associated courses."""

import structlog
from pydantic import BaseModel

_logger = structlog.get_logger(__name__)


class BlueprintSyncResult(BaseModel):
    blueprint_course_id: str
    associated_courses: list[str] = []
    synced: int = 0
    errors: list[str] = []


class BlueprintService:
    """Manage blueprint course accessibility syncing."""

    async def get_blueprint_info(self, session, course_id: int) -> dict:
        """Check if a course is a blueprint and get associated courses."""
        from lti_app.canvas.client import CanvasClient

        client = CanvasClient(
            base_url=session.canvas_base_url,
            access_token=session.canvas_access_token,
            refresh_token=getattr(session, "canvas_refresh_token", ""),
        )
        try:
            await client.get(
                f"/api/v1/courses/{course_id}/blueprint_templates/default"
            )
            associations = await client.get(
                f"/api/v1/courses/{course_id}/blueprint_templates/default/associated_courses"
            )
            return {
                "is_blueprint": True,
                "associated_courses": [
                    str(a.get("id"))
                    for a in (associations if isinstance(associations, list) else [])
                ],
            }
        except Exception:
            return {"is_blueprint": False, "associated_courses": []}
        finally:
            await client.close()

    async def trigger_sync(self, session, course_id: int) -> BlueprintSyncResult:
        """Trigger a blueprint sync to push changes to associated courses."""
        from lti_app.canvas.client import CanvasClient

        client = CanvasClient(
            base_url=session.canvas_base_url,
            access_token=session.canvas_access_token,
            refresh_token=getattr(session, "canvas_refresh_token", ""),
        )
        try:
            info = await self.get_blueprint_info(session, course_id)
            await client.post(
                f"/api/v1/courses/{course_id}/blueprint_templates/default/migrations",
                body={"comment": "Remedy Canvas LTI accessibility remediation sync"},
            )
            _logger.info("blueprint_sync_triggered", course_id=course_id)
            return BlueprintSyncResult(
                blueprint_course_id=str(course_id),
                associated_courses=info.get("associated_courses", []),
                synced=1,
            )
        except Exception as e:
            _logger.error("blueprint_sync_failed", course_id=course_id, error=str(e))
            return BlueprintSyncResult(
                blueprint_course_id=str(course_id),
                errors=[str(e)],
            )
        finally:
            await client.close()
