"""SSRF guards and limits of the server-side page fetcher."""

import asyncio
import socket
from typing import Any

import httpx
import pytest

from app.services import fetch
from app.services.fetch import FetchError, fetch_page, html_to_paragraphs

PAGE = "<html><head><style>x{}</style></head><body><nav>Menu</nav><p>First para.</p><div>Second <b>para</b>.</div><script>evil()</script></body></html>"


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_html_to_paragraphs_drops_chrome_and_scripts() -> None:
    assert html_to_paragraphs(PAGE) == ["First para.", "Second para ."]


async def test_follows_safe_redirect_and_reports_final_url() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "short.example":
            return httpx.Response(301, headers={"location": "https://www.example.org/article"})
        return httpx.Response(200, html=PAGE)

    async with _client(handler) as http:
        page = await fetch_page("https://short.example/a", http, check_dns=False)
    assert page.url == "https://www.example.org/article"
    assert "First para." in page.paragraphs


@pytest.mark.parametrize(
    "location",
    ["http://169.254.169.254/latest/meta-data", "http://localhost:8000/v1", "file:///etc/passwd"],
)
async def test_redirect_to_internal_target_is_blocked(location: str) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "example.org", "internal target must never be requested"
        return httpx.Response(302, headers={"location": location})

    async with _client(handler) as http:
        with pytest.raises(FetchError) as info:
            await fetch_page("https://example.org/a", http, check_dns=False)
    assert info.value.reason == "unsafe_url"


async def test_redirect_loop_is_bounded() -> None:
    async with _client(lambda req: httpx.Response(302, headers={"location": "/again"})) as http:
        with pytest.raises(FetchError) as info:
            await fetch_page("https://example.org/a", http, check_dns=False)
    assert info.value.reason == "too_many_redirects"


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(404), "http_404"),
        (
            httpx.Response(200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"}),
            "non_text",
        ),
    ],
)
async def test_errors_and_non_text_are_rejected(response: httpx.Response, reason: str) -> None:
    async with _client(lambda req: response) as http:
        with pytest.raises(FetchError) as info:
            await fetch_page("https://example.org/a", http, check_dns=False)
    assert info.value.reason == reason


async def test_body_is_size_capped() -> None:
    big = "<p>" + "a" * 50_000 + "</p>"
    async with _client(lambda req: httpx.Response(200, html=big)) as http:
        page = await fetch_page("https://example.org/a", http, max_bytes=1000, check_dns=False)
    assert sum(len(p) for p in page.paragraphs) <= 1000


async def test_hostname_resolving_to_private_ip_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_getaddrinfo(host: str, *args: Any, **kwargs: Any) -> list[Any]:
        ip = "10.0.0.5" if host == "intranet.example.com" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake_getaddrinfo)
    assert await fetch._resolves_to_public_ips("public.example.com")

    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("must not connect")

    async with _client(handler) as http:
        with pytest.raises(FetchError) as info:
            await fetch_page("https://intranet.example.com/", http)
    assert info.value.reason == "non_public_host"


def _stream(body: bytes, **headers: str) -> httpx.Response:
    """An unbuffered response, so the fetcher's real streaming path runs."""
    return httpx.Response(
        200, headers={"content-type": "text/html", **headers}, stream=httpx.ByteStream(body)
    )


def _dns(monkeypatch: pytest.MonkeyPatch, table: dict[str, list[str]]) -> None:
    async def fake_getaddrinfo(host: str, *args: Any, **kwargs: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in table[host]]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake_getaddrinfo)


async def test_connection_is_pinned_to_the_checked_address(monkeypatch: pytest.MonkeyPatch) -> None:
    _dns(monkeypatch, {"news.example.com": ["93.184.216.34"]})
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return _stream(b"<p>Hello pinned world, this is the article.</p>")

    async with _client(handler) as http:
        page = await fetch_page("https://news.example.com:8443/a?x=1", http)
    req = seen[0]
    assert req.url.host == "93.184.216.34" and req.url.port == 8443 and req.url.query == b"x=1"
    assert req.headers["host"] == "news.example.com:8443"
    assert req.extensions["sni_hostname"] == "news.example.com"  # TLS verified for the name
    assert page.url == "https://news.example.com:8443/a?x=1"  # citations keep the real URL


def test_pinned_request_brackets_ipv6() -> None:
    target, headers, ext = fetch._pinned_request("http://v6.example.com/p", "2606:4700::1")
    assert target == "http://[2606:4700::1]/p"
    assert headers == {"Host": "v6.example.com"} and ext == {}


async def test_any_private_address_in_the_answer_blocks_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _dns(monkeypatch, {"mixed.example.com": ["93.184.216.34", "127.0.0.1"]})

    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("must not connect")

    async with _client(handler) as http:
        with pytest.raises(FetchError) as info:
            await fetch_page("https://mixed.example.com/", http)
    assert info.value.reason == "non_public_host"


async def test_redirect_hop_is_resolved_and_pinned_again(monkeypatch: pytest.MonkeyPatch) -> None:
    _dns(monkeypatch, {"a.example.com": ["93.184.216.34"], "b.example.com": ["10.1.2.3"]})

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "93.184.216.34", "internal address must never be contacted"
        return httpx.Response(302, headers={"location": "https://b.example.com/x"})

    async with _client(handler) as http:
        with pytest.raises(FetchError) as info:
            await fetch_page("https://a.example.com/", http)
    assert info.value.reason == "non_public_host"


async def test_gzip_bomb_is_bounded() -> None:
    import gzip

    bomb = gzip.compress(b"<p>" + b"A" * 20_000_000 + b"</p>")
    async with _client(lambda req: _stream(bomb, **{"content-encoding": "gzip"})) as http:
        page = await fetch_page("https://example.org/a", http, max_bytes=100_000, check_dns=False)
    assert sum(len(p) for p in page.paragraphs) <= 100_000


async def test_unsupported_content_encoding_is_rejected() -> None:
    async with _client(lambda req: _stream(b"\x00\x01", **{"content-encoding": "br"})) as http:
        with pytest.raises(FetchError) as info:
            await fetch_page("https://example.org/a", http, check_dns=False)
    assert info.value.reason == "unsupported_encoding"


async def test_meta_charset_is_honoured_for_legacy_russian_pages() -> None:
    body = '<html><head><meta charset="windows-1251"></head><p>Буддизм возник в Индии.</p></html>'
    async with _client(lambda req: _stream(body.encode("cp1251"))) as http:
        page = await fetch_page("https://example.ru/a", http, check_dns=False)
    assert "Буддизм возник в Индии." in page.paragraphs
