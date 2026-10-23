"""When was it said? Dates kept apart, and uncertainty kept as uncertainty.

Five different dates matter to a fact-check, and none may stand in for another:

- the video's publication (upload) date: an upper bound only. Archival footage, compilations,
  rebroadcasts and re-uploads are published long after the words were said;
- the statement (recording) date: known only when the video itself shows or says it, or the
  user gives it. It may be partial ("1998", "1998-05");
- the date of the event the claim describes: part of the claim's own wording;
- each evidence source's publication date, when the page or the search provider states it;
- today.

`temporal_context` reconciles the first two (dropping impossible or conflicting values and
saying so) and renders the text both the analysis (search planning) and evaluation prompts get.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

PARTIAL_DATE_RE = r"^\d{4}(-\d{2}(-\d{2})?)?$"
_PARTIAL = re.compile(PARTIAL_DATE_RE)
_ISO_PREFIX = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})(?:$|[T\s])")
# Clock skew and time zones: a date up to a day ahead of the server's is not "in the future".
_TOLERANCE = timedelta(days=1)


def valid_partial_date(value: str) -> bool:
    """YYYY, YYYY-MM or YYYY-MM-DD naming a real calendar date."""
    if not _PARTIAL.match(value):
        return False
    y, m, d = [*value.split("-"), "01", "01"][:3]
    try:
        date(int(y), int(m), int(d))
    except ValueError:
        return False
    return True


def earliest(value: str) -> date:
    """First day of a partial date (validated)."""
    y, m, d = [*value.split("-"), "01", "01"][:3]
    return date(int(y), int(m), int(d))


def iso_day(value: object, today: date | None = None) -> str | None:
    """YYYY-MM-DD from an ISO date or date-time string, if real and not in the future."""
    if not isinstance(value, str) or not (m := _ISO_PREFIX.match(value)):
        return None
    try:
        day = date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None
    if day > (today or date.today()) + _TOLERANCE or day.year < 1900:
        return None
    return day.isoformat()


# Publication dates in page markup, most specific first. Attribute order varies, so each
# pattern allows the content before or after the name.
_META_NAMES = (
    "article:published_time", "og:published_time", "datepublished", "publish-date",
    "publication_date", "pubdate", "dc.date", "date",
)  # fmt: skip
_JSONLD_RE = re.compile(r'"datePublished"\s*:\s*"([^"]{8,40})"', re.I)
_TIME_RE = re.compile(r"<time[^>]*\bdatetime\s*=\s*[\"']([^\"']{8,40})[\"']", re.I)


def _meta_content(raw: str, name: str) -> str | None:
    n = re.escape(name)
    for pattern in (
        rf"<meta[^>]*(?:property|name|itemprop)\s*=\s*[\"']{n}[\"'][^>]*content\s*=\s*[\"']([^\"']+)",
        rf"<meta[^>]*content\s*=\s*[\"']([^\"']+)[\"'][^>]*(?:property|name|itemprop)\s*=\s*[\"']{n}[\"']",
    ):
        if m := re.search(pattern, raw, re.I):
            return m.group(1)
    return None


def page_published_date(raw_html: str, today: date | None = None) -> str | None:
    """The page's own publication date from its metadata; None if absent or implausible."""
    head = raw_html[:200_000]
    candidates = [_meta_content(head, n) for n in _META_NAMES]
    candidates += [m.group(1) for m in (_JSONLD_RE.search(head), _TIME_RE.search(head)) if m]
    for value in candidates:
        if day := iso_day(value, today):
            return day
    return None


@dataclass(frozen=True)
class TemporalContext:
    said_on: str | None = None  # partial date, when known
    said_by: str | None = None  # upper bound: the video's publication date
    notes: tuple[str, ...] = ()  # values that were dropped, and why

    def cache_part(self) -> str:
        return f"{self.said_on or ''}<={self.said_by or ''}"

    def prompt_text(self, *, evaluating: bool = True) -> str:
        """What the model is told. Search planning gets the facts; the evaluator also the rules."""
        if self.said_on:
            bound = f" The video was published on {self.said_by}." if self.said_by else ""
            rule = (
                " Judge the claim as of that time: later events cannot make it false, but later "
                "reporting about that time still counts."
            )
            return (
                f"\nWhen it was said: {self.said_on} (from the video itself, or the user).{bound}"
                + (rule if evaluating else "")
            )
        if self.said_by:
            rule = (
                " If the verdict depends on when it was said and the claim or context does not "
                "establish it, say so in limitations and prefer mixed or insufficient_evidence "
                "over judging it against the wrong period."
            )
            return (
                f"\nWhen it was said: unknown. The video was published on {self.said_by}, so it "
                "was said on or before that date, possibly years earlier (archival footage, a "
                "compilation, a rebroadcast or a re-upload). Do not assume it was said on the "
                "publication date." + (rule if evaluating else "")
            )
        return ""


def temporal_context(
    statement_date: str | None, video_published_date: str | None, today: date | None = None
) -> TemporalContext:
    today = today or date.today()
    notes: list[str] = []
    said_on, said_by = statement_date, video_published_date
    if said_by and earliest(said_by) > today + _TOLERANCE:
        notes.append(f"The video's publication date ({said_by}) is in the future; ignored.")
        said_by = None
    if said_on and earliest(said_on) > today + _TOLERANCE:
        notes.append(f"The statement date ({said_on}) is in the future; ignored.")
        said_on = None
    if said_on and said_by and earliest(said_on) > earliest(said_by):
        notes.append(
            f"The statement date ({said_on}) is after the video was published ({said_by}); "
            "the dates conflict, so when it was said is treated as unknown."
        )
        said_on = None
    return TemporalContext(said_on, said_by, tuple(notes))
