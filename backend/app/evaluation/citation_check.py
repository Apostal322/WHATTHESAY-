"""Does a source's excerpt actually appear on (or match) the page it cites?

Used by the comparison harness to measure citation accuracy per backend:
- page_text excerpts were cut from the fetched page and checked verbatim during
  retrieval, so they are `verbatim` by construction;
- snippets and grounding summaries are re-fetched (with the same SSRF guards as
  retrieval) and screened lexically. `consistent` means the page contains the
  excerpt's content words and numbers; it does not prove the page supports the
  claim. `needs_review` goes to a person.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.evaluation.live import lexical_support, needs_review
from app.services.citations import EvidenceSource
from app.services.fetch import FetchError, fetch_page


async def check_citation(
    src: EvidenceSource,
    http: httpx.AsyncClient,
    *,
    check_dns: bool = True,
    timeout_sec: float = 10.0,
) -> dict[str, Any]:
    if src.origin == "page_text":
        return {"status": "verbatim"}
    try:
        page = await asyncio.wait_for(
            fetch_page(src.url, http, check_dns=check_dns), timeout=timeout_sec
        )
    except FetchError as exc:
        return {"status": "unreachable", "reason": exc.reason}
    except TimeoutError:
        return {"status": "unreachable", "reason": "timeout"}
    text = " ".join(page.paragraphs)
    checks = [lexical_support(ex, text) for ex in src.excerpts]
    return {
        "status": "needs_review" if any(needs_review(c) for c in checks) else "consistent",
        "final_url": page.url,
        "checks": checks,
    }
