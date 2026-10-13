"""Server-side fetching of public web pages for evidence extraction.

The backend fetches URLs that come from third-party search results, so every
hop is treated as untrusted (SSRF protection):
- only http(s) URLs that pass `is_safe_display_url`,
- the host must resolve exclusively to public IP addresses,
- redirects are followed manually (bounded) and each hop is re-validated,
- bodies are size-capped and only text/HTML is accepted.

Known gap: DNS is resolved here and again by the HTTP client, so a hostile DNS
server could still rebind between the two lookups. A production deployment
should also block private ranges at the network layer (egress proxy/firewall).
"""

from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from app.services.citations import is_safe_display_url

MAX_REDIRECTS = 3
USER_AGENT = "YouVerifyBot/0.1 (+fact-check evidence retrieval)"

_TAG_RE = re.compile(r"<[^>]+>")
_DROP_RE = re.compile(r"<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", re.I | re.S)
_BLOCK_RE = re.compile(r"</?(p|div|li|h[1-6]|br|tr|section|article|blockquote)[^>]*>", re.I)


def html_to_paragraphs(raw: str) -> list[str]:
    """Visible text split at block-level elements; used to cut evidence passages."""
    text = _DROP_RE.sub(" ", raw)
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    paras = (" ".join(p.split()) for p in html.unescape(text).split("\n"))
    return [p for p in paras if p]


@dataclass(frozen=True)
class FetchedPage:
    url: str  # final URL after redirects
    paragraphs: list[str]


class FetchError(Exception):
    """A page could not be fetched safely; `reason` is a short machine code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def _resolves_to_public_ips(host: str) -> bool:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    addresses = {info[4][0] for info in infos}
    return bool(addresses) and all(ipaddress.ip_address(a).is_global for a in addresses)


async def fetch_page(
    url: str,
    client: httpx.AsyncClient,
    *,
    max_bytes: int = 1_500_000,
    check_dns: bool = True,
) -> FetchedPage:
    """Fetch one public page with SSRF guards. Raises FetchError on any problem."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if not is_safe_display_url(current):
            raise FetchError("unsafe_url")
        host = urlsplit(current).hostname or ""
        if check_dns and not await _resolves_to_public_ips(host):
            raise FetchError("non_public_host")
        try:
            async with client.stream(
                "GET", current, follow_redirects=False, headers={"User-Agent": USER_AGENT}
            ) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise FetchError("bad_redirect")
                    current = urljoin(current, location)
                    continue
                if resp.status_code >= 400:
                    raise FetchError(f"http_{resp.status_code}")
                ctype = resp.headers.get("content-type", "").lower()
                if "html" not in ctype and "text/plain" not in ctype:
                    raise FetchError("non_text")
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body += chunk
                    if len(body) > max_bytes:
                        break  # keep the first max_bytes; long pages are truncated
                text = bytes(body[:max_bytes]).decode(resp.encoding or "utf-8", errors="replace")
        except httpx.HTTPError as exc:
            raise FetchError(type(exc).__name__) from exc
        if "html" in ctype:
            return FetchedPage(url=current, paragraphs=html_to_paragraphs(text))
        return FetchedPage(url=current, paragraphs=[p for p in text.split("\n\n") if p.strip()])
    raise FetchError("too_many_redirects")
