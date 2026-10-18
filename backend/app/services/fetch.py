"""Server-side fetching of public web pages for evidence extraction.

The backend fetches URLs that come from third-party search results, so every
hop is treated as untrusted (SSRF protection):
- only http(s) URLs that pass `is_safe_display_url`;
- the host is resolved once, every address must be public, and the
  connection goes to that exact address (DNS pinning). TLS is still verified
  against the hostname (SNI), so a DNS answer that changes between check and
  connect (rebinding) cannot redirect the request to an internal address;
- redirects are followed manually (bounded) and each hop is re-validated;
- only text/HTML is accepted; the *compressed* body is size-capped and
  decompressed with an output limit (no decompression bombs);
- the whole fetch runs under the caller's timeout.

Deployment: also block private ranges at the network egress. If an HTTP proxy
is configured for outbound traffic, the proxy resolves names itself and must
enforce the same policy.
"""

from __future__ import annotations

import asyncio
import codecs
import html
import ipaddress
import re
import socket
import zlib
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.services.citations import is_safe_display_url

MAX_REDIRECTS = 3
USER_AGENT = "YouVerifyBot/0.1 (+fact-check evidence retrieval)"
_META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset=["']?\s*([A-Za-z0-9_\-]+)""", re.I)

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


async def _public_addresses(host: str) -> list[str] | None:
    """All addresses of `host` if every one is public, else None (fail closed)."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return None
    addresses = list(dict.fromkeys(str(info[4][0]) for info in infos))
    if not addresses or not all(ipaddress.ip_address(a).is_global for a in addresses):
        return None
    return addresses


async def _resolves_to_public_ips(host: str) -> bool:
    return await _public_addresses(host) is not None


def _pinned_request(url: str, address: str) -> tuple[str, dict[str, str], dict[str, str]]:
    """URL aimed at `address`, plus the Host header and TLS SNI for the original name."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    ip_host = f"[{address}]" if ":" in address else address
    netloc = f"{ip_host}:{parts.port}" if parts.port else ip_host
    host_header = f"{host}:{parts.port}" if parts.port else host
    extensions = {"sni_hostname": host} if parts.scheme == "https" else {}
    return urlunsplit(parts._replace(netloc=netloc)), {"Host": host_header}, extensions


def _decode_body(raw: bytes, encoding: str, max_bytes: int) -> bytes:
    if encoding in ("", "identity"):
        return raw[:max_bytes]
    if encoding in ("gzip", "x-gzip", "deflate"):
        for wbits in (47, -15):  # 47: auto-detect gzip/zlib header; -15: raw deflate
            try:
                return zlib.decompressobj(wbits).decompress(raw, max_bytes)
            except zlib.error:
                continue
        raise FetchError("bad_encoding")
    raise FetchError("unsupported_encoding")


def _charset(header_charset: str | None, body: bytes) -> str:
    for candidate in (header_charset, _sniff_meta_charset(body)):
        if candidate:
            try:
                return codecs.lookup(candidate).name
            except LookupError:
                continue
    return "utf-8"


def _sniff_meta_charset(body: bytes) -> str | None:
    match = _META_CHARSET_RE.search(body[:4096])
    return match.group(1).decode("ascii") if match else None


async def fetch_page(
    url: str,
    client: httpx.AsyncClient,
    *,
    max_bytes: int = 1_500_000,
    check_dns: bool = True,
) -> FetchedPage:
    """Fetch one public page with SSRF guards. Raises FetchError on any problem.

    `check_dns=False` skips resolution and pinning (tests with a mock transport only).
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if not is_safe_display_url(current):
            raise FetchError("unsafe_url")
        target = current
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        extensions: dict[str, str] = {}
        if check_dns:
            addresses = await _public_addresses(urlsplit(current).hostname or "")
            if addresses is None:
                raise FetchError("non_public_host")
            target, host_header, extensions = _pinned_request(current, addresses[0])
            headers.update(host_header)
        try:
            async with client.stream(
                "GET", target, follow_redirects=False, headers=headers, extensions=extensions
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
                if resp.is_stream_consumed:
                    # Already buffered and decoded by the transport (in-memory test transports).
                    body = resp.content[:max_bytes]
                else:
                    raw = bytearray()
                    async for chunk in resp.aiter_raw():
                        raw += chunk
                        if len(raw) > max_bytes:
                            break  # keep the first max_bytes; long pages are truncated
                    encoding = resp.headers.get("content-encoding", "").strip().lower()
                    body = _decode_body(bytes(raw[:max_bytes]), encoding, max_bytes)
                text = body.decode(_charset(resp.charset_encoding, body), errors="replace")
        except httpx.HTTPError as exc:
            raise FetchError(type(exc).__name__) from exc
        if "html" in ctype:
            return FetchedPage(url=current, paragraphs=html_to_paragraphs(text))
        return FetchedPage(url=current, paragraphs=[p for p in text.split("\n\n") if p.strip()])
    raise FetchError("too_many_redirects")
