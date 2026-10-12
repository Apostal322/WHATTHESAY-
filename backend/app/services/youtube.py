"""YouTube reference validation.

The backend never fetches or forwards a user-supplied URL. It extracts and
validates an 11-character video ID and rebuilds a canonical watch URL, which is
then passed to Gemini (Google fetches it, not us). This removes SSRF surface.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from app.core.errors import InvalidInputError

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_WATCH_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
_SHORT_HOSTS = {"youtu.be"}


def canonical_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def parse_video_id(youtube_url: str | None = None, video_id: str | None = None) -> str:
    """Return a validated video ID from either an explicit ID or a YouTube URL."""
    if video_id:
        if not VIDEO_ID_RE.fullmatch(video_id):
            raise InvalidInputError("video_id must be an 11-character YouTube video ID.")
        if youtube_url:
            from_url = parse_video_id(youtube_url=youtube_url)
            if from_url != video_id:
                raise InvalidInputError("video_id does not match youtube_url.")
        return video_id

    if not youtube_url:
        raise InvalidInputError("Provide youtube_url or video_id.")

    parts = urlsplit(youtube_url.strip())
    if parts.scheme not in {"http", "https"} or parts.username or parts.password or parts.port:
        raise InvalidInputError("Only plain http(s) YouTube URLs are accepted.")
    host = (parts.hostname or "").lower()

    candidate: str | None = None
    if host in _WATCH_HOSTS:
        if parts.path.startswith("/shorts/"):
            raise InvalidInputError("YouTube Shorts are not supported yet; open a standard video.")
        if parts.path.startswith("/live/"):
            raise InvalidInputError("Live streams are not supported yet.")
        if parts.path != "/watch":
            raise InvalidInputError("Only youtube.com/watch URLs are supported.")
        values = parse_qs(parts.query).get("v", [])
        candidate = values[0] if values else None
    elif host in _SHORT_HOSTS:
        candidate = parts.path.lstrip("/").split("/", 1)[0]
    else:
        raise InvalidInputError("URL host is not an allowed YouTube host.")

    if not candidate or not VIDEO_ID_RE.fullmatch(candidate):
        raise InvalidInputError("Could not find a valid YouTube video ID in the URL.")
    return candidate


def format_mmss(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
