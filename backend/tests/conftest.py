from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.config import Settings
from app.main import create_app
from app.services.llm import (
    GroundedResult,
    GroundingSource,
    GroundingSupportSpan,
    LLMResult,
    Usage,
    VideoClip,
)


@dataclass
class FakeLLM:
    """Deterministic stand-in for GeminiClient.

    `json_responses` maps a schema class name to either a dict (serialised as
    JSON), a raw string (to simulate malformed output) or an Exception to raise.
    """

    json_responses: dict[str, Any] = field(default_factory=dict)
    grounded: GroundedResult | Exception | None = None
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def generate_json(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: type[BaseModel],
        video: VideoClip | None = None,
    ) -> LLMResult:
        self.calls.append((schema.__name__, {"prompt": prompt, "video": video}))
        value = self.json_responses[schema.__name__]
        if isinstance(value, Exception):
            raise value
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return LLMResult(
            text=text,
            usage=Usage(prompt_tokens=100, output_tokens=20, total_tokens=120),
            model=model,
        )

    async def grounded_search(self, *, model: str, system: str, prompt: str) -> GroundedResult:
        self.calls.append(("grounded_search", {"prompt": prompt}))
        if isinstance(self.grounded, Exception):
            raise self.grounded
        assert self.grounded is not None, "grounded_search was not expected"
        return self.grounded


def judge_passages(
    relation: str | list[str], speaker_is_named: bool = True, n: int = 8
) -> dict[str, Any]:
    """Scripted EntailmentOutput for passages P1..Pn (unknown ids are ignored).

    `relation` is one relation for a one-part claim, or one per claim part; every passage is
    judged alike. Use it only where the fixture's passages really do say (or deny) that.
    """
    relations = [relation] if isinstance(relation, str) else relation
    ids = [f"P{i}" for i in range(1, n + 1)]
    return {
        "speakers": [{"passage_id": p, "speaker_is_named": speaker_is_named} for p in ids],
        "parts": [
            {"text": f"part {k}", "relations": [{"passage_id": p, "relation": r} for p in ids]}
            for k, r in enumerate(relations, 1)
        ],
    }


def make_grounded(
    chunks: list[tuple[str, str | None, str | None]],
    supports: list[tuple[str, tuple[int, ...]]],
    queries: list[str] | None = None,
) -> GroundedResult:
    return GroundedResult(
        text="research summary",
        sources=[
            GroundingSource(index=i, uri=u, title=t, domain=d) for i, (u, t, d) in enumerate(chunks)
        ],
        supports=[GroundingSupportSpan(text=t, source_indices=idx) for t, idx in supports],
        queries=queries or ["test query"],
        usage=Usage(prompt_tokens=300, output_tokens=200, total_tokens=500),
        model="fake-model",
        raw_metadata_present=True,
    )


@pytest.fixture
def settings() -> Settings:
    return Settings(
        GEMINI_API_KEY=None,
        YOUVERIFY_EXTRACTION_MODEL="fake-model",
        YOUVERIFY_VERIFICATION_MODEL="fake-model",
        YOUVERIFY_RESOLVE_REDIRECTS=False,
        YOUVERIFY_RATE_LIMIT_PER_MINUTE=100,
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def client(settings: Settings, fake_llm: FakeLLM) -> Iterator[TestClient]:
    with TestClient(create_app(settings, llm=fake_llm)) as c:
        yield c


EXTRACTION_OK = {
    "found": True,
    "claim": "Artificial intelligence has already replaced 80% of software engineers.",
    "claim_type": "factual",
    "evidence_from_video": "AI has already replaced like eighty percent of software engineers",
    "is_exact_quote": False,
    "context": "The speaker is discussing the job market for programmers.",
    "language": "en",
}

ANALYSIS_FACTUAL = {
    "normalized_claim": "AI has replaced 80% of software engineers (i.e. 80% of such jobs no longer exist).",
    "claim_type": "factual",
    "is_checkworthy": True,
    "reason": "A quantitative claim about employment.",
    "search_queries": [
        "software developer employment statistics 2025",
        "AI replacing software engineers data",
    ],
    "scope_notes": "Global vs US; time period; definition of 'replaced'.",
}
