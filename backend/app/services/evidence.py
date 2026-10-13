"""Turn raw search hits into ranked, deduplicated, citable evidence.

Deterministic and model-free, so it is cheap, testable and identical across
search providers:
- canonical URLs merge duplicates (tracking parameters, fragments, www, AMP);
- reciprocal rank fusion combines the hit lists of several queries;
- lexical relevance to the claim reorders them and filters unrelated hits;
- at most `per_domain` sources per site keeps the evidence set independent;
- passages are cut *verbatim* from the fetched page, so each excerpt can be
  checked against the page it cites (unlike grounding summaries).

Lexical relevance is a crude multilingual proxy (prefix stems); it ranks, it
does not judge. The evaluation step still assesses every source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.evaluation.metrics import tokens
from app.services.citations import is_safe_display_url, normalize_domain
from app.services.search import SearchHit

RRF_K = 60
MAX_PASSAGE_CHARS = 400
_STEM = 5
_TRACKING_PARAMS = re.compile(r"^(utm_|fbclid$|gclid$|yclid$|mc_|ref$|ref_src$|igshid$)")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")
_MULTI_PART_SUFFIXES = {"co", "com", "org", "net", "gov", "ac", "edu"}


def canonical_url(url: str) -> str:
    """Key for duplicate detection (not for display)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    path = re.sub(r"/amp/?$", "", parts.path).rstrip("/") or "/"
    query = urlencode(
        sorted((k, v) for k, v in parse_qsl(parts.query) if not _TRACKING_PARAMS.match(k))
    )
    return urlunsplit(("https", host, path, query, ""))


def site_of(url: str) -> str:
    """Approximate registrable domain (eTLD+1) used for independence counting."""
    host = normalize_domain(url) or ""
    labels = host.split(".")
    if len(labels) >= 3 and labels[-2] in _MULTI_PART_SUFFIXES and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _stems(text: str) -> set[str]:
    return {t[:_STEM] for t in tokens(text) if len(t) >= 4 and not t.isdigit()}


def _numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in _NUM_RE.findall(text)}


@dataclass(frozen=True)
class ClaimTerms:
    """Stem sets for the claim and for each query, scored separately.

    Keeping them apart matters for cross-language evidence: a Russian claim with
    an English query must let an English page match the English query, instead
    of diluting one pooled set with terms the page cannot contain.
    """

    groups: tuple[frozenset[str], ...]
    numbers: frozenset[str]

    @classmethod
    def build(cls, claim: str, queries: list[str]) -> ClaimTerms:
        groups = tuple(g for g in (frozenset(_stems(t)) for t in [claim, *queries]) if g)
        return cls(groups=groups, numbers=frozenset(_numbers(claim)))


def lexical_relevance(text: str, terms: ClaimTerms) -> float:
    """Best share of claim or query stems present in `text`, plus a bonus for claim numbers."""
    if not terms.groups:
        return 0.0
    found = _stems(text)
    score = max(len(found & g) / len(g) for g in terms.groups)
    if terms.numbers and terms.numbers & _numbers(text):
        score += 0.2
    return min(score, 1.0)


@dataclass
class RankedHit:
    url: str
    title: str | None
    snippet: str | None
    site: str
    score: float
    rrf: float
    relevance: float
    found_by: set[str] = field(default_factory=set)  # queries that returned it


def rank_hits(
    hits_per_query: dict[str, list[SearchHit]],
    terms: ClaimTerms,
    *,
    limit: int,
    per_domain: int = 2,
    min_relevance: float = 0.15,
) -> list[RankedHit]:
    merged: dict[str, RankedHit] = {}
    for query, hits in hits_per_query.items():
        for hit in hits:
            if not is_safe_display_url(hit.url):
                continue
            key = canonical_url(hit.url)
            item = merged.get(key)
            if item is None:
                item = merged[key] = RankedHit(
                    url=hit.url,
                    title=hit.title,
                    snippet=hit.snippet,
                    site=site_of(hit.url),
                    score=0.0,
                    rrf=0.0,
                    relevance=0.0,
                )
            item.rrf += 1.0 / (RRF_K + hit.rank)
            item.found_by.add(query)
            if hit.snippet and len(hit.snippet) > len(item.snippet or ""):
                item.snippet = hit.snippet
    for item in merged.values():
        item.relevance = lexical_relevance(f"{item.title or ''} {item.snippet or ''}", terms)
        # Rank fusion orders by agreement across queries; relevance scales it.
        item.score = item.rrf * (0.5 + item.relevance)
    ranked = sorted(
        (m for m in merged.values() if m.relevance >= min_relevance),
        key=lambda m: m.score,
        reverse=True,
    )
    out: list[RankedHit] = []
    per_site: dict[str, int] = {}
    for item in ranked:
        if per_site.get(item.site, 0) >= per_domain:
            continue
        per_site[item.site] = per_site.get(item.site, 0) + 1
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _windows(paragraph: str) -> list[str]:
    """Split a paragraph into verbatim windows of whole sentences, each <= MAX_PASSAGE_CHARS."""
    if len(paragraph) <= MAX_PASSAGE_CHARS:
        return [paragraph]
    out: list[str] = []
    current = ""
    for sentence in _SENTENCE_RE.split(paragraph):
        if len(sentence) > MAX_PASSAGE_CHARS:
            if current:
                out.append(current)
                current = ""
            out.append(sentence[:MAX_PASSAGE_CHARS])
            continue
        candidate = f"{current} {sentence}".strip()
        if len(candidate) > MAX_PASSAGE_CHARS:
            out.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        out.append(current)
    return out


def extract_passages(
    paragraphs: list[str], terms: ClaimTerms, *, max_passages: int = 3, min_relevance: float = 0.2
) -> list[str]:
    """Most claim-relevant verbatim passages of a page, in page order."""
    scored: list[tuple[float, int, str]] = []
    for paragraph in paragraphs:
        for window in _windows(paragraph):
            if len(window) < 40:  # menus, captions, buttons
                continue
            rel = lexical_relevance(window, terms)
            if rel >= min_relevance:
                scored.append((rel, len(scored), window))
    best = sorted(scored, key=lambda x: (-x[0], x[1]))[:max_passages]
    return [w for _, _, w in sorted(best, key=lambda x: x[1])]


def excerpt_is_verbatim(excerpt: str, paragraphs: list[str]) -> bool:
    """Citation check: a page excerpt must occur verbatim in the page it cites."""
    return any(excerpt in p for p in paragraphs)
