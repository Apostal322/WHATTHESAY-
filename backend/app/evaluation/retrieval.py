"""Pure helpers for comparing retrieval backends (scripts/compare_retrieval.py).

The source-type classification is a transparent heuristic for summarising
*where* evidence comes from. It is not a reliability verdict: an official site
can be wrong and a blog can be right. Relevance and support must still be
judged by a person on the review sheet the script writes.
"""

from __future__ import annotations

import statistics
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.schemas import Verdict
from app.services.evidence import site_of

SourceType = Literal["official", "academic", "reference", "fact_check", "news_wire", "other"]

_FACT_CHECKERS = {
    "factcheck.org", "politifact.com", "snopes.com", "fullfact.org", "apnews.com/hub/ap-fact-check",
    "reuters.com/fact-check", "factcheck.afp.com", "leadstories.com", "provereno.media",
}  # fmt: skip
_NEWS_WIRES = {
    "reuters.com",
    "apnews.com",
    "afp.com",
    "bbc.co.uk",
    "bbc.com",
    "tass.ru",
    "interfax.ru",
}
_REFERENCE = {"wikipedia.org", "britannica.com", "wikidata.org"}
_OFFICIAL_SUFFIXES = (".gov", ".mil", ".int", ".gov.uk", ".gouv.fr", ".gov.ru", ".europa.eu")
_ACADEMIC_SUFFIXES = (".edu", ".ac.uk", ".ac.jp")
_ACADEMIC_SITES = {"nih.gov", "nature.com", "science.org", "doi.org", "arxiv.org", "jstor.org"}


def classify_source(url: str) -> SourceType:
    site = site_of(url)
    host_path = url.split("://", 1)[-1].lower().removeprefix("www.")
    host = host_path.split("/", 1)[0]
    if (
        host in _FACT_CHECKERS
        or site in _FACT_CHECKERS
        or any(host_path.startswith(f) for f in _FACT_CHECKERS if "/" in f)
    ):
        return "fact_check"
    if site in _ACADEMIC_SITES or f".{host}".endswith(_ACADEMIC_SUFFIXES):
        return "academic"
    if f".{host}".endswith(_OFFICIAL_SUFFIXES):
        return "official"
    if site in _REFERENCE:
        return "reference"
    if site in _NEWS_WIRES:
        return "news_wire"
    return "other"


class RetrievalCase(BaseModel):
    """One claim with fixed queries, so every backend gets identical input."""

    id: str
    claim: str
    language: Literal["auto", "en", "ru"] = "en"
    queries: list[str] = Field(min_length=1, max_length=4)
    category: str = Field(description="e.g. science, history, politics, religion-text")
    draft_verdict: Verdict | None = Field(
        default=None,
        description="Author's expected verdict. NOT ground truth until verified_by is set.",
    )
    verified_by: str | None = Field(
        default=None, description="Who checked draft_verdict against primary sources, and when."
    )
    notes: str | None = None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def summarize(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per-backend statistics over successful runs only; failures are counted, not averaged."""
    out: dict[str, dict[str, Any]] = {}
    for backend in dict.fromkeys(r["backend"] for r in rows):
        mine = [r for r in rows if r["backend"] == backend]
        ok = [r for r in mine if not r.get("error")]
        sources = [s for r in ok for s in r["sources"]]
        types = [s["source_type"] for s in sources]
        costs = [r["cost_usd"] for r in ok if r.get("cost_usd") is not None]
        out[backend] = {
            "cases": len(mine),
            "failed": len(mine) - len(ok),
            "with_sources": sum(1 for r in ok if r["sources"]),
            "median_latency_ms": _median([r["latency_ms"] for r in ok]),
            "mean_sources": round(statistics.mean([len(r["sources"]) for r in ok]), 2)
            if ok
            else None,
            "mean_independent_sites": round(
                statistics.mean([len({s["site"] for s in r["sources"]}) for r in ok]), 2
            )
            if ok
            else None,
            "source_types": {t: types.count(t) for t in dict.fromkeys(types)},
            "page_text_share": round(
                sum(1 for s in sources if s["origin"] == "page_text") / len(sources), 2
            )
            if sources
            else None,
            "mean_excerpt_relevance": round(statistics.mean([s["relevance"] for s in sources]), 3)
            if sources
            else None,
            "cost_usd_per_claim": round(statistics.mean(costs), 5) if costs else None,
            "cost_known_for": f"{len(costs)}/{len(ok)}",
        }
    return out
