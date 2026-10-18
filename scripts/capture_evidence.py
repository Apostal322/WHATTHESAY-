#!/usr/bin/env python3
"""Capture verbatim evidence excerpts into an offline regression fixture, or test reachability.

Run this on a machine whose network can reach the publishers (e.g. your Windows PC). It makes
NO model calls and spends no API credit.

    # 1. Does this network reach the sources, YouTube and the Gemini endpoint? (no writes)
    python scripts/capture_evidence.py --check-only

    # 2. Let the application open each pending source and keep up to 3 short verbatim excerpts
    python scripts/capture_evidence.py

    # 3. A site that blocks the app's fetcher: save the page in your browser (Ctrl+S,
    #    "Webpage, HTML only") and let the app parse that file instead
    python scripts/capture_evidence.py --source U2 --html-file "$HOME\\Downloads\\u2.html"

Excerpts are cut by the application's own page parser (app.services.fetch / evidence) around
the fixture's anchor phrases, so they are verbatim page text. Anchors that are not on the page
are reported as missing, never filled in. Failed attempts are logged in the fixture. Review
the diff (git diff eval/fixtures) before committing: you are responsible for what is retained.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import socket
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

import httpx

from app.evaluation.fixture import (
    CaptureAttempt,
    EvidenceFixture,
    FixtureSource,
    capture_from_paragraphs,
)
from app.services.fetch import FetchError, fetch_page, html_to_paragraphs

DEFAULT_FIXTURE = REPO / "eval" / "fixtures" / "en-debate-1200" / "fixture.json"
# Hosts the full product needs besides the evidence pages. Only DNS + one unauthenticated
# HTTPS request each; no API key is sent, nothing is billed.
SERVICE_HOSTS = {
    "gemini_api": "https://generativelanguage.googleapis.com/",
    "youtube": "https://www.youtube.com/",
    "brave_search_api": "https://api.search.brave.com/",
}


def environment_label(explicit: str | None) -> str:
    # OS name and release only: never the hostname, user name or IP address.
    return explicit or f"local {platform.system()} {platform.release()}"


def load(path: Path) -> EvidenceFixture:
    return EvidenceFixture.model_validate(json.loads(path.read_text(encoding="utf-8")))


def save(path: Path, fixture: EvidenceFixture) -> None:
    data = fixture.model_dump(mode="json")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def dns_status(url: str) -> str:
    host = urlsplit(url).hostname or ""
    try:
        socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        return f"dns_failed:{type(exc).__name__}"
    return "dns_ok"


async def http_status(url: str, client: httpx.AsyncClient) -> str:
    try:
        resp = await client.get(url, follow_redirects=False)
    except httpx.HTTPError as exc:
        return f"http_failed:{type(exc).__name__}"
    return f"http_{resp.status_code}"


async def check_only(fixture: EvidenceFixture, client: httpx.AsyncClient) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    targets = [(s.id, s.url) for s in fixture.sources] + list(SERVICE_HOSTS.items())
    for name, url in targets:
        started = time.perf_counter()
        dns = dns_status(url)
        http = await http_status(url, client) if dns == "dns_ok" else "skipped"
        rows.append(
            {
                "target": name,
                "host": urlsplit(url).hostname,
                "dns": dns,
                "http": http,
                "ms": int((time.perf_counter() - started) * 1000),
            }
        )
    return rows


async def capture_one(
    src: FixtureSource, client: httpx.AsyncClient, env: str, html_file: Path | None
) -> tuple[bool, str]:
    now = datetime.now(UTC)
    if html_file is not None:
        raw = await asyncio.to_thread(html_file.read_text, encoding="utf-8", errors="replace")
        paragraphs, final_url, method = html_to_paragraphs(raw), src.url, "user_saved_html"
    else:
        try:
            page = await asyncio.wait_for(fetch_page(src.url, client), timeout=30)
        except (FetchError, TimeoutError) as exc:
            reason = exc.reason if isinstance(exc, FetchError) else "timeout"
            if reason == "non_public_host" and dns_status(src.url) != "dns_ok":
                reason = "dns_failed"  # the name did not resolve (blocked or offline network)
            src.attempts.append(
                CaptureAttempt(at=now.isoformat(timespec="seconds"), environment=env, result=reason)
            )
            return False, reason
        paragraphs, final_url, method = page.paragraphs, page.url, "app_fetch_page"
    cap = capture_from_paragraphs(
        paragraphs,
        src.anchors,
        final_url=final_url,
        method=method,  # type: ignore[arg-type]
        environment=env,
        now=now,
    )
    if not cap.excerpts:
        src.attempts.append(
            CaptureAttempt(
                at=now.isoformat(timespec="seconds"), environment=env, result="no_anchor_found"
            )
        )
        return False, f"no anchor found ({len(paragraphs)} paragraphs)"
    src.capture = cap
    missing = f"; missing anchors {cap.missing_anchors}" if cap.missing_anchors else ""
    return True, f"{len(cap.excerpts)} excerpt(s) via {method}{missing}"


async def main_async(args: argparse.Namespace) -> int:
    fixture = load(args.fixture)
    env = environment_label(args.environment)
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        if args.check_only:
            rows = await check_only(fixture, client)
            print(f"environment: {env}")
            for r in rows:
                print(
                    f"{r['target']:<18} {r['host']:<45} {r['dns']:<22} {r['http']:<26} {r['ms']} ms"
                )
            print(
                "\nhttp_2xx/3xx/4xx = the network reaches the host (a 401/403/404 from an API root "
                "without a key is expected). dns_failed / http_failed = blocked on this network."
            )
            return 0
        wanted = set(args.source or [])
        if args.html_file and len(wanted) != 1:
            raise SystemExit("--html-file needs exactly one --source")
        failures = 0
        for src in fixture.sources:
            if wanted and src.id not in wanted:
                continue
            if src.status == "captured" and not args.recapture:
                when = src.capture.captured_at if src.capture else ""
                print(f"{src.id}: already captured on {when}; skip (--recapture to redo)")
                continue
            ok, detail = await capture_one(src, client, env, args.html_file)
            failures += not ok
            print(f"{src.id}: {'captured' if ok else 'FAILED'} - {detail}")
    save(args.fixture, fixture)
    print(f"\nupdated {args.fixture}. Review the change with: git diff -- {args.fixture}")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    ap.add_argument("--source", action="append", help="source id (repeatable); default: all")
    ap.add_argument("--html-file", type=Path, help="a page you saved in your browser")
    ap.add_argument("--environment", help="label stored with the capture, e.g. 'local Windows 11'")
    ap.add_argument("--recapture", action="store_true", help="replace existing captures")
    ap.add_argument("--check-only", action="store_true", help="test reachability; write nothing")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
