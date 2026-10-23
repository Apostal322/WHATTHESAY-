#!/usr/bin/env python3
"""What can this environment actually reach? Run before any live validation.

    python scripts/diagnose_env.py            # network + configuration only: no credentials sent
    python scripts/diagnose_env.py --live     # also 1 model call, 1 grounded call, 1 search call

Without --live nothing authenticated is sent and nothing is billed. Each check is classified,
so a blocked network is not mistaken for a broken key or an application defect:

    ok | http_<code> (reachable) | dns_failure | blocked_by_proxy | timeout | network_error
    auth_rejected | model_unavailable | quota_exhausted | rate_limited | overloaded | not_configured

Secrets are never printed. The key is shown only as a short SHA-256 fingerprint so the owner
can confirm that a rotated key is the one in use. Never run --live with a key that has been
exposed (pasted into a chat, an issue or a commit): rotate it first.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

import httpx

from app.core.config import get_settings
from app.core.errors import AppError

ENDPOINTS = {
    "gemini_api": "https://generativelanguage.googleapis.com/",
    "brave_api": "https://api.search.brave.com/",
    "youtube": "https://www.youtube.com/",
    "publisher_reuters": "https://www.reuters.com/",
    "publisher_florida_dos": "https://constitutionalinitiatives.dos.fl.gov/",
    "publisher_rollcall": "https://rollcall.com/",
    "publisher_wikipedia": "https://en.wikipedia.org/",
}


def fingerprint(secret: str | None) -> str | None:
    return hashlib.sha256(secret.encode()).hexdigest()[:12] if secret else None


def key_is_revoked(secret: str | None) -> bool:
    from app.services.llm import REVOKED_KEY_FINGERPRINTS

    return fingerprint(secret) in REVOKED_KEY_FINGERPRINTS


def classify_network(exc: Exception) -> str:
    text = f"{type(exc).__name__} {exc}".lower()
    if isinstance(exc, httpx.ProxyError) or ("403" in text and "proxy" in text):
        return "blocked_by_proxy"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if "name or service not known" in text or "nodename" in text or "getaddrinfo" in text:
        return "dns_failure"
    return "network_error"


async def reach(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    try:
        resp = await client.get(url, follow_redirects=False)
        return {"status": f"http_{resp.status_code}", "reachable": True}
    except Exception as exc:  # classified, never raised
        return {"status": classify_network(exc), "reachable": False, "detail": type(exc).__name__}


def classify_app_error(exc: AppError) -> str:
    code = exc.code
    status = (exc.diagnostics or {}).get("http_status")
    if code == "missing_credentials":
        return "auth_rejected"
    if code == "model_not_found":
        return "model_unavailable"
    if code == "provider_quota_exhausted":
        return "quota_exhausted"
    if code in ("provider_rate_limited", "rate_limited"):
        return "rate_limited"
    if status == 503:
        return "overloaded"
    if code == "provider_timeout":
        return "timeout"
    return code


async def live_checks(settings: Any, http: httpx.AsyncClient) -> dict[str, Any]:
    """At most one model call, one grounded call and one search call."""
    from app.api.deps import build_search_providers
    from app.models.llm_outputs import ClaimAnalysisOutput
    from app.services.llm import GeminiClient
    from app.services.search import SearchProviderError

    out: dict[str, Any] = {}
    key = settings.gemini_api_key
    if key is None:
        out["gemini_generate"] = out["gemini_grounding"] = {"status": "not_configured"}
    elif key_is_revoked(key.get_secret_value()):
        # The exposed key: no request is sent with it, whatever it would return.
        out["gemini_generate"] = out["gemini_grounding"] = {"status": "revoked_key_not_used"}
    else:
        llm = GeminiClient(key.get_secret_value(), timeout_sec=30, max_retries=0)
        model = settings.verification_model
        try:
            res = await llm.generate_json(
                model=model,
                system="Classify the claim.",
                prompt="Claim: The Eiffel Tower is in Paris. Return the JSON fields.",
                schema=ClaimAnalysisOutput,
            )
            out["gemini_generate"] = {
                "status": "ok",
                "model": res.model,
                "tokens": res.usage.total_tokens,
            }
        except AppError as exc:
            out["gemini_generate"] = {"status": classify_app_error(exc), "model": model}
        try:
            g = await llm.grounded_search(
                model=model, system="Find sources.", prompt="When was the Eiffel Tower completed?"
            )
            out["gemini_grounding"] = {
                "status": "ok" if g.raw_metadata_present else "no_grounding_metadata",
                "sources": len(g.sources),
                "queries": len(g.queries),
            }
        except AppError as exc:
            out["gemini_grounding"] = {"status": classify_app_error(exc), "model": model}
    providers = build_search_providers(settings, http)
    if not providers:
        out["search_provider"] = {"status": "not_configured"}
    for p in providers[:1]:
        try:
            r = await p.search("Eiffel Tower completed 1889", language="en", max_results=3)
            out[f"search_{p.name}"] = {"status": "ok", "hits": len(r.hits)}
        except SearchProviderError as exc:
            out[f"search_{p.name}"] = {"status": exc.reason}
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--live", action="store_true", help="also make up to 3 authenticated calls")
    args = ap.parse_args()
    s = get_settings()
    report: dict[str, Any] = {
        "configuration": {
            "gemini_key": "set" if s.gemini_api_key else "missing",
            "gemini_key_fingerprint": fingerprint(
                s.gemini_api_key.get_secret_value() if s.gemini_api_key else None
            ),
            "gemini_key_is_the_exposed_one": key_is_revoked(
                s.gemini_api_key.get_secret_value() if s.gemini_api_key else None
            ),
            "brave_key": "set" if s.brave_api_key else "missing",
            "searxng_url": "set" if s.searxng_url else "missing",
            "retrieval_backend": s.retrieval_backend,
            "extraction_model": s.extraction_model,
            "verification_model": s.verification_model,
            "fallback_model": s.fallback_model,
            "daily_call_limit": s.gemini_daily_call_limit,
        },
        "network": {},
    }
    async with httpx.AsyncClient(timeout=8.0) as http:
        names = list(ENDPOINTS)
        if s.searxng_url:
            ENDPOINTS["searxng"] = s.searxng_url
            names.append("searxng")
        results = await asyncio.gather(*(reach(http, ENDPOINTS[n]) for n in names))
        report["network"] = dict(zip(names, results, strict=True))
        if args.live:
            report["live"] = await live_checks(s, http)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
