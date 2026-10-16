"""Step F: citation integrity.

Every source the user sees originates from search-grounding metadata returned by
the provider; the model cannot introduce a URL. This module:
- builds numbered evidence sources from grounding chunks + attributed spans,
- validates URLs before display (scheme, host, no credentials, no private hosts),
- optionally resolves Google's grounding redirect links to the publisher URL
  (only that one redirect host is ever contacted, redirects are not followed),
- merges duplicate URLs and counts independent domains.

A reachable URL does not prove relevance; relevance is assessed separately by
the evaluator and enforced by verdict guardrails.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

import httpx

from app.services.llm import GroundedResult

log = logging.getLogger(__name__)

GROUNDING_REDIRECT_HOST = "vertexaisearch.cloud.google.com"
_MAX_EXCERPTS_PER_SOURCE = 3
_MAX_EXCERPT_CHARS = 400
_MAX_SOURCES = 10

# Where an excerpt's text comes from. Shown to the evaluator, which must weigh
# a model summary or a search snippet differently from verbatim page text.
ExcerptOrigin = Literal["grounding_summary", "page_text", "search_snippet"]
_ORIGIN_LABEL: dict[str, str] = {
    "grounding_summary": "model summary attributed by search grounding",
    "page_text": "verbatim text from the page",
    "search_snippet": "search-engine snippet (page not read)",
}


@dataclass
class EvidenceSource:
    id: str
    url: str
    title: str | None
    domain: str | None
    excerpts: list[str] = field(default_factory=list)
    origin: ExcerptOrigin = "grounding_summary"


def is_safe_display_url(url: str) -> bool:
    """True if the URL is safe to render as a clickable link."""
    if not url or len(url) > 2048:
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in {"http", "https"} or parts.username or parts.password:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith((".local", ".localhost")):
        return False
    if "." not in host and ":" not in host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Browsers and resolvers read "127.1", "0177.0.0.1" and "0x7f.1" as IPv4 addresses.
        try:
            ip = ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return True  # a host name
    return ip.is_global


def normalize_domain(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().lower()
    host = urlsplit(value).hostname if "://" in value else value.split("/", 1)[0]
    if not host or "." not in host or " " in host:
        return None
    return host.removeprefix("www.")


def is_grounding_redirect(url: str) -> bool:
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and parts.hostname == GROUNDING_REDIRECT_HOST
        and parts.path.startswith("/grounding-api-redirect/")
    )


def build_sources(grounded: GroundedResult) -> list[EvidenceSource]:
    """Number grounding chunks that have attributed text and a safe URL."""
    excerpts: dict[int, list[str]] = {}
    for span in grounded.supports:
        text = " ".join(span.text.split())
        if len(text) > _MAX_EXCERPT_CHARS:
            text = text[: _MAX_EXCERPT_CHARS - 1].rstrip() + "…"
        for idx in span.source_indices:
            bucket = excerpts.setdefault(idx, [])
            if text not in bucket and len(bucket) < _MAX_EXCERPTS_PER_SOURCE:
                bucket.append(text)

    sources: list[EvidenceSource] = []
    for chunk in grounded.sources:
        if chunk.index not in excerpts or not is_safe_display_url(chunk.uri):
            continue  # nothing attributable to it, or unsafe: not citable
        domain = normalize_domain(chunk.domain) or normalize_domain(chunk.title)
        if domain is None and not is_grounding_redirect(chunk.uri):
            domain = normalize_domain(chunk.uri)
        sources.append(
            EvidenceSource(
                id="",
                url=chunk.uri,
                title=(chunk.title or "").strip() or None,
                domain=domain,
                excerpts=excerpts[chunk.index],
            )
        )
        if len(sources) >= _MAX_SOURCES:
            break
    return sources


def dedupe_and_number(sources: list[EvidenceSource]) -> list[EvidenceSource]:
    """Merge sources with the same URL, then assign ids S1..Sn."""
    merged: dict[str, EvidenceSource] = {}
    for src in sources:
        key = src.url.split("#", 1)[0].rstrip("/")
        if key in merged:
            target = merged[key]
            for ex in src.excerpts:
                if ex not in target.excerpts and len(target.excerpts) < _MAX_EXCERPTS_PER_SOURCE:
                    target.excerpts.append(ex)
        else:
            merged[key] = src
    out = list(merged.values())
    for i, src in enumerate(out, start=1):
        src.id = f"S{i}"
    return out


async def resolve_redirects(sources: list[EvidenceSource], client: httpx.AsyncClient) -> None:
    """Replace grounding redirect URLs with the publisher URL when safely resolvable.

    Only the fixed Google redirect host is contacted, and the Location header is
    read without following it. On any failure the redirect URL is kept (it still
    takes the user to the source).
    """

    async def _one(src: EvidenceSource) -> None:
        if not is_grounding_redirect(src.url):
            return
        try:
            resp = await client.get(src.url, follow_redirects=False)
        except httpx.HTTPError as exc:
            log.info("redirect_resolve_failed", extra={"error": type(exc).__name__})
            return
        location = resp.headers.get("location")
        if resp.is_redirect and location and is_safe_display_url(location):
            src.url = location
            src.domain = src.domain or normalize_domain(location)

    await asyncio.gather(*(_one(s) for s in sources))


def format_sources_for_prompt(
    sources: list[EvidenceSource], same_origin: dict[str, list[str]] | None = None
) -> str:
    """Numbered source block for the evaluator.

    `same_origin` maps a source id to other ids that share its site or republish
    the same report, so the evaluator does not count them as independent.
    """
    from app.services.prompts import untrusted_line

    blocks = []
    for src in sources:
        # Titles and domains come from the web, like the excerpts: wrapped as data, on one line.
        title = untrusted_line(src.title or "Untitled")
        domain = untrusted_line(src.domain or "unknown domain")
        header = (
            f"[{src.id}] title: <untrusted>{title}</untrusted>; domain: {domain}; "
            f"excerpts: {_ORIGIN_LABEL[src.origin]}"
        )
        if same_origin and same_origin.get(src.id):
            header += f"; same site or same original report as {', '.join(same_origin[src.id])}"
        lines = [header]
        lines += [f"  - <untrusted>{untrusted_line(ex)}</untrusted>" for ex in src.excerpts]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
