"""Retrieval comparison harness: helpers and script wiring (no network)."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.errors import ProviderQuotaExhaustedError
from app.evaluation.retrieval import RetrievalCase, classify_source, summarize
from app.services.citations import EvidenceSource
from app.services.retrieval import RetrievalResult, SearchRetriever
from app.services.search import FailoverSearch, SearchProviderError, SearchResponse

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "compare_retrieval", REPO / "scripts" / "compare_retrieval.py"
)
assert spec and spec.loader
cr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cr)

CASE = RetrievalCase(
    id="en-eiffel-1889",
    claim="The Eiffel Tower was completed in 1889.",
    queries=["Eiffel Tower completed 1889"],
    category="history",
    draft_verdict="supported",
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.nasa.gov/x", "official"),
        ("https://www.gov.uk/guidance", "official"),
        ("https://www.who.int/news", "official"),
        ("https://pubmed.ncbi.nlm.nih.gov/1", "academic"),
        ("https://www.stanford.edu/x", "academic"),
        ("https://en.wikipedia.org/wiki/X", "reference"),
        ("https://www.snopes.com/fact-check/x", "fact_check"),
        ("https://factcheck.afp.com/x", "fact_check"),
        ("https://www.reuters.com/fact-check/x", "fact_check"),
        ("https://www.reuters.com/world/x", "news_wire"),
        ("https://someblog.example/x", "other"),
    ],
)
def test_classify_source(url: str, expected: str) -> None:
    assert classify_source(url) == expected


def test_summarize_excludes_failures_from_averages() -> None:
    src = {
        "site": "wikipedia.org",
        "source_type": "reference",
        "origin": "page_text",
        "relevance": 0.8,
    }
    rows = [
        {
            "backend": "searxng",
            "latency_ms": 1000,
            "cost_usd": 0.0,
            "sources": [src, {**src, "site": "nasa.gov", "source_type": "official"}],
        },
        {"backend": "searxng", "latency_ms": 3000, "cost_usd": 0.0, "sources": []},
        {"backend": "searxng", "latency_ms": 99999, "error": "all_searches_failed"},
        {"backend": "brave", "latency_ms": 500, "cost_usd": None, "sources": [src]},
    ]
    s = summarize(rows)
    assert s["searxng"]["failed"] == 1 and s["searxng"]["cases"] == 3
    assert s["searxng"]["median_latency_ms"] == 2000.0  # the failed 99999 ms run is excluded
    assert s["searxng"]["mean_sources"] == 1.0
    assert s["searxng"]["mean_independent_sites"] == 1.0
    assert s["searxng"]["source_types"] == {"reference": 1, "official": 1}
    assert s["brave"]["cost_usd_per_claim"] is None and s["brave"]["cost_known_for"] == "0/1"


class Provider:
    name = "searxng"
    price_per_1k_requests = 0.0

    def __init__(self, result: object) -> None:
        self._result = result

    async def search(self, query: str, *, language: str, max_results: int) -> SearchResponse:
        if isinstance(self._result, Exception):
            raise self._result
        return SearchResponse(hits=self._result, provider=self.name)  # type: ignore[arg-type]


async def test_all_failed_searches_are_reported_as_a_failure_not_as_no_evidence(
    settings: Settings,
) -> None:
    async with httpx.AsyncClient() as http:
        retriever = SearchRetriever(
            FailoverSearch([Provider(SearchProviderError("searxng", "http_502"))]),
            http,
            check_dns=False,
        )
        row = await cr.run_one("searxng", retriever, CASE, settings)
    assert row["error"] == "retrieval_unavailable"
    assert row["diagnostics"]["provider_errors"] == ["http_502"]


async def test_provider_error_is_recorded_with_diagnostics(settings: Settings) -> None:
    class Quota:
        name = "gemini_grounding"

        async def retrieve(self, **kw: object) -> RetrievalResult:
            raise ProviderQuotaExhaustedError(
                "used up", diagnostics={"provider_status": "RESOURCE_EXHAUSTED"}
            )

    row = await cr.run_one("gemini_grounding", Quota(), CASE, settings)
    assert row["error"] == "provider_quota_exhausted"
    assert row["diagnostics"]["provider_status"] == "RESOURCE_EXHAUSTED"


async def test_grounding_rows_classify_by_publisher_domain_and_report_renders(
    settings: Settings,
) -> None:
    class Grounding:
        name = "gemini_grounding"

        async def retrieve(self, **kw: object) -> RetrievalResult:
            return RetrievalResult(
                sources=[
                    EvidenceSource(
                        "S1",
                        "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc",
                        "nasa.gov",
                        "nasa.gov",
                        ["The Eiffel Tower was completed in 1889."],
                    )
                ],
                executed_queries=["q"],
                backend="gemini_grounding",
                billed_searches=1,
            )

    s = settings.model_copy(
        update={
            "price_input_per_million": 1.0,
            "price_output_per_million": 1.0,
            "price_per_1k_search_queries": 14.0,
        }
    )
    row = await cr.run_one("gemini_grounding", Grounding(), CASE, s)
    assert row["sources"][0]["source_type"] == "official"
    assert row["sources"][0]["site"] == "nasa.gov"
    assert row["cost_usd"] == 0.014
    report = cr.render_report(
        {"run_at": "t", "backends": ["gemini_grounding"], "prices": {}}, [CASE], [row]
    )
    assert "| gemini_grounding | 1 | 0 | 1 |" in report
    assert "Draft verdict: supported (draft, unverified)" in report
    json.dumps(row)


def test_repo_retrieval_cases_are_valid_and_unverified_drafts_are_marked() -> None:
    lines = (REPO / "eval" / "retrieval_cases.jsonl").read_text(encoding="utf-8").splitlines()
    cases = [RetrievalCase.model_validate(json.loads(x)) for x in lines if x.strip()]
    assert len(cases) >= 10
    assert {c.language for c in cases} == {"en", "ru"}
    assert len({c.id for c in cases}) == len(cases)
    assert {c.draft_verdict for c in cases} >= {"supported", "contradicted"}


async def test_citation_check_outcomes() -> None:
    from app.evaluation.citation_check import check_citation

    page = "<p>The Eiffel Tower was completed in March 1889 for the Exposition Universelle.</p>"

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "good.example":
            return httpx.Response(200, html=page)
        return httpx.Response(404)

    def src(url: str, text: str, origin: str) -> EvidenceSource:
        return EvidenceSource("S1", url, None, None, [text], origin=origin)  # type: ignore[arg-type]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        verbatim = await check_citation(
            src("https://x.example/", "t", "page_text"), http, check_dns=False
        )
        ok = await check_citation(
            src("https://good.example/a", "Eiffel Tower completed 1889", "search_snippet"),
            http,
            check_dns=False,
        )
        wrong = await check_citation(
            src(
                "https://good.example/a",
                "The tower was demolished in 1952 by engineers",
                "grounding_summary",
            ),
            http,
            check_dns=False,
        )
        gone = await check_citation(
            src("https://gone.example/a", "Eiffel Tower completed 1889", "search_snippet"),
            http,
            check_dns=False,
        )
    assert verbatim == {"status": "verbatim"}
    assert ok["status"] == "consistent"
    assert wrong["status"] == "needs_review"  # 1952 is not on the page
    assert gone == {"status": "unreachable", "reason": "http_404"}


async def test_run_one_records_origin_groups_and_citations(settings: Settings) -> None:
    text = "The Eiffel Tower was completed in March 1889 for the Exposition Universelle in Paris, France."

    class Search:
        name = "search"

        async def retrieve(self, **kw: object) -> RetrievalResult:
            return RetrievalResult(
                sources=[
                    EvidenceSource(
                        "S1", "https://a.example/x", "A", "a.example", [text], origin="page_text"
                    ),
                    EvidenceSource(
                        "S2", "https://b.example/y", "B", "b.example", [text], origin="page_text"
                    ),
                ],
                executed_queries=["q"],
                backend="brave",
                search_cost_usd=0.01,
            )

    async with httpx.AsyncClient() as http:
        row = await cr.run_one(
            "brave", Search(), CASE, settings, citation_http=http, check_dns=False
        )
    assert row["sources"][0]["origin_group"] == row["sources"][1]["origin_group"]
    assert [s["citation"]["status"] for s in row["sources"]] == ["verbatim", "verbatim"]
    summary = summarize([row, {"backend": "brave", "error": "retrieval_unavailable"}])["brave"]
    assert summary["mean_independent_sites"] == 2.0
    assert summary["mean_independent_origins"] == 1.0  # one report, two sites
    assert summary["citations"] == {"verbatim": 2}
    assert summary["errors"] == {"retrieval_unavailable": 1}


def test_script_refuses_paid_backend_without_budget(settings: Settings) -> None:
    s = settings.model_copy(
        update={"brave_api_key": SecretStr("k"), "price_per_1k_brave_requests": 5.0}
    )
    with pytest.raises(SystemExit) as info:
        cr.build_retriever("brave", s, httpx.AsyncClient())
    assert "YOUVERIFY_SEARCH_DAILY_BUDGET_USD" in str(info.value)
    ok = cr.build_retriever(
        "brave", s.model_copy(update={"search_daily_budget_usd": 1.0}), httpx.AsyncClient()
    )
    assert [p.name for p in ok._search.providers] == ["brave"]
