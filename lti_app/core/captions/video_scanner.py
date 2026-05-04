"""Scan Canvas course pages for YouTube and Canvas Studio video embeds."""

import re
from dataclasses import dataclass, field
from typing import Optional

import structlog

_logger = structlog.get_logger(__name__)

# YouTube URL patterns in iframes
_YOUTUBE_IFRAME_RE = re.compile(
    r"youtube(?:-nocookie)?\.com/embed/([a-zA-Z0-9_-]{11})",
    re.IGNORECASE,
)

# YouTube watch URL pattern (sometimes in <a> tags or text)
_YOUTUBE_WATCH_RE = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})",
    re.IGNORECASE,
)

# Canvas Studio embed patterns
# Matches: https://{subdomain}.instructuremedia.com/embed/{uuid}
_STUDIO_EMBED_RE = re.compile(
    r"(https?://([a-zA-Z0-9-]+)\.instructuremedia\.com)/embed/([a-f0-9-]+)",
    re.IGNORECASE,
)

# Also matches older Arc branding: arc.instructure.com
_ARC_EMBED_RE = re.compile(
    r"(https?://arc\.instructure\.com)/embed/([a-f0-9-]+)",
    re.IGNORECASE,
)


@dataclass
class DetectedVideo:
    """A video found in a Canvas course page (YouTube or Canvas Studio)."""
    video_id: str
    video_url: str
    page_id: str
    page_title: str
    content_type: str
    thumbnail_url: str = ""
    source_type: str = "youtube"  # "youtube" or "studio"

    @property
    def canonical_url(self) -> str:
        if self.source_type == "youtube":
            return f"https://www.youtube.com/watch?v={self.video_id}"
        return self.video_url

    @property
    def embed_url(self) -> str:
        if self.source_type == "youtube":
            return f"https://www.youtube.com/embed/{self.video_id}"
        return self.video_url


# Keep backward-compatible alias
YouTubeVideo = DetectedVideo


class VideoScanner:
    """Scan course pages for YouTube and Canvas Studio video embeds."""

    def scan_pages(self, pages: list) -> list[DetectedVideo]:
        """Scan a list of CoursePage objects for videos.

        Args:
            pages: List of CoursePage model instances (must have id, title, html_content, content_type)

        Returns:
            Deduplicated list of DetectedVideo records (YouTube + Studio)
        """
        seen_keys: set[str] = set()
        videos: list[DetectedVideo] = []

        for page in pages:
            html = getattr(page, "html_content", "") or ""
            page_id = str(getattr(page, "id", ""))
            page_title = getattr(page, "title", "")
            content_type = str(getattr(page, "content_type", "wiki_page"))

            # YouTube videos
            for vid_id in self.extract_youtube_ids(html):
                key = f"youtube:{vid_id}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                videos.append(DetectedVideo(
                    video_id=vid_id,
                    video_url=f"https://www.youtube.com/watch?v={vid_id}",
                    page_id=page_id,
                    page_title=page_title,
                    content_type=content_type,
                    thumbnail_url=f"https://img.youtube.com/vi/{vid_id}/mqdefault.jpg",
                    source_type="youtube",
                ))

            # Canvas Studio videos
            for studio_info in self._extract_studio_embeds(html):
                key = f"studio:{studio_info['uuid']}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                videos.append(DetectedVideo(
                    video_id=studio_info["uuid"],
                    video_url=studio_info["embed_url"],
                    page_id=page_id,
                    page_title=page_title,
                    content_type=content_type,
                    thumbnail_url="",  # Studio doesn't expose public thumbnails
                    source_type="studio",
                ))

        yt_count = sum(1 for v in videos if v.source_type == "youtube")
        studio_count = sum(1 for v in videos if v.source_type == "studio")
        _logger.info("video_scan_complete", pages=len(pages),
                     youtube=yt_count, studio=studio_count, total=len(videos))
        return videos

    def extract_youtube_ids(self, html: str) -> list[str]:
        """Extract unique YouTube video IDs from HTML content."""
        ids: list[str] = []
        seen: set[str] = set()

        for match in _YOUTUBE_IFRAME_RE.finditer(html):
            vid_id = match.group(1)
            if vid_id not in seen:
                seen.add(vid_id)
                ids.append(vid_id)

        for match in _YOUTUBE_WATCH_RE.finditer(html):
            vid_id = match.group(1)
            if vid_id not in seen:
                seen.add(vid_id)
                ids.append(vid_id)

        return ids

    def _extract_studio_embeds(self, html: str) -> list[dict]:
        """Extract Canvas Studio embed info from HTML content."""
        results: list[dict] = []
        seen: set[str] = set()

        for match in _STUDIO_EMBED_RE.finditer(html):
            base_url, subdomain, uuid = match.group(1), match.group(2), match.group(3)
            if uuid not in seen:
                seen.add(uuid)
                results.append({
                    "uuid": uuid,
                    "embed_url": f"{base_url}/embed/{uuid}",
                    "subdomain": subdomain,
                })

        for match in _ARC_EMBED_RE.finditer(html):
            base_url, uuid = match.group(1), match.group(2)
            if uuid not in seen:
                seen.add(uuid)
                results.append({
                    "uuid": uuid,
                    "embed_url": f"{base_url}/embed/{uuid}",
                    "subdomain": "arc",
                })

        return results
