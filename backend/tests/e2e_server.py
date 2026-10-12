"""E2E TEST FIXTURE: the real FastAPI app with a scripted fake model.

Used only by extension/e2e/smoke.mjs to exercise the extension UI, HTTP, CORS,
streaming, the verification pipeline and its guardrails without a Gemini key.
Everything above the LLM adapter is real; the model outputs below are canned
test data, not real research results. Never use this to demo the product.

    python -m tests.e2e_server   (from backend/)
"""

from __future__ import annotations

import asyncio
import json

import uvicorn
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

_DELAY = 0.4
_USAGE = Usage(prompt_tokens=1000, output_tokens=100, total_tokens=1100)

_OUTPUTS: dict[str, dict[str, object]] = {
    "ExtractionOutput": {
        "found": True,
        "claim": "Artificial intelligence has already replaced 80% of software engineers.",
        "claim_type": "factual",
        "evidence_from_video": "AI has already replaced, like, 80 percent of software engineers",
        "is_exact_quote": False,
        "context": "[E2E FIXTURE] The speaker is discussing the programming job market.",
        "language": "en",
    },
    "ClaimAnalysisOutput": {
        "normalized_claim": "AI has already replaced 80% of software engineers.",
        "claim_type": "factual",
        "is_checkworthy": True,
        "reason": "Quantitative employment claim.",
        "search_queries": ["software developer employment statistics"],
        "scope_notes": "Geography and time period unspecified.",
    },
    "EvaluationOutput": {
        "source_assessments": [
            {"source_id": "S1", "stance": "contradicts", "note": "[E2E FIXTURE] stance."},
            {"source_id": "S2", "stance": "partially_supports", "note": "[E2E FIXTURE] stance."},
            {"source_id": "S7", "stance": "supports", "note": "Unknown id; must be dropped."},
        ],
        "verdict": "contradicted",
        "explanation": "[E2E FIXTURE] Canned explanation citing [S1] and a bogus [S9].",
        "key_findings": [
            {"text": "[E2E FIXTURE] finding.", "stance": "contradicts", "source_ids": ["S1"]},
        ],
        "limitations": ["[E2E FIXTURE] This result is canned test data."],
    },
}


class ScriptedLLM:
    async def generate_json(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: type[BaseModel],
        video: VideoClip | None = None,
    ) -> LLMResult:
        await asyncio.sleep(_DELAY)
        return LLMResult(text=json.dumps(_OUTPUTS[schema.__name__]), usage=_USAGE, model=model)

    async def grounded_search(self, *, model: str, system: str, prompt: str) -> GroundedResult:
        await asyncio.sleep(_DELAY)
        return GroundedResult(
            text="",
            sources=[
                GroundingSource(
                    0,
                    "https://example.org/e2e-fixture-source-1",
                    "E2E fixture source 1",
                    "example.org",
                ),
                GroundingSource(
                    1,
                    "https://example.net/e2e-fixture-source-2",
                    "E2E fixture source 2",
                    "example.net",
                ),
                GroundingSource(2, "javascript:alert(1)", "Unsafe", None),
            ],
            supports=[
                GroundingSupportSpan("[E2E FIXTURE] excerpt one.", (0,)),
                GroundingSupportSpan("[E2E FIXTURE] excerpt two.", (1,)),
                GroundingSupportSpan("unsafe", (2,)),
            ],
            queries=["software developer employment statistics"],
            usage=_USAGE,
            model=model,
            raw_metadata_present=True,
        )


def main() -> None:
    settings = Settings(
        GEMINI_API_KEY="e2e-fixture-not-a-real-key",
        YOUVERIFY_RESOLVE_REDIRECTS=False,
        YOUVERIFY_CACHE_TTL_SEC=0,
        _env_file=None,  # type: ignore[call-arg]
    )
    uvicorn.run(
        create_app(settings, llm=ScriptedLLM()), host="127.0.0.1", port=8000, log_level="warning"
    )


if __name__ == "__main__":
    main()
