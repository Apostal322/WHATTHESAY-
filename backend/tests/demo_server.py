"""SCREENSHOT DEMO: the real FastAPI app and pipeline with a scripted fake model.

Used only by extension/e2e/screenshots.mjs to render the README screenshots. Every model output
below is invented (fictional town, fictional mayor, example.* sources) and labelled DEMO. What is
real is everything above the model adapter: the API, streaming, the extension UI, and the
deterministic guardrails. In particular, the attribution safeguard shown in the screenshot is the
real code reacting to a scripted model verdict, not a canned result.

Two scenarios, chosen by the claim text:
- "said ... bus fares": the model calls an outlet's characterization proof of what the mayor
  said; the attribution safeguard overrides the verdict.
- anything else (the bridge claim): two independent sources back a factual claim.

    python -m tests.demo_server   (from backend/)
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

_DELAY = 0.3
_USAGE = Usage(prompt_tokens=1000, output_tokens=100, total_tokens=1100)
_FARES = "Mayor Jane Doe said Riverton would cut bus fares in half next year."
_BRIDGE = "The Riverton river bridge opened to traffic in 2019."

_EXTRACTION: dict[str, object] = {
    "found": True,
    "claim": _FARES,
    "claim_type": "factual",
    "evidence_from_video": "the mayor told us she'd cut bus fares in half next year",
    "is_exact_quote": False,
    "context": "[DEMO] A council member recounts the mayor's transport plans.",
    "language": "en",
}


def _analysis(claim: str) -> dict[str, object]:
    fares = "bus fares" in claim
    return {
        "normalized_claim": claim,
        "claim_type": "factual",
        "is_checkworthy": True,
        "reason": "[DEMO] Checkable statement.",
        "search_queries": ["Riverton mayor bus fares" if fares else "Riverton bridge opening"],
        "scope_notes": "",
        "attributed_to": "Jane Doe" if fares else "",
        "exact_words": [],
    }


_EVAL_FARES: dict[str, object] = {
    "source_assessments": [
        {
            "source_id": "S1",
            "stance": "supports",
            "note": "[DEMO] The outlet describes the plan as halving fares.",
        },
        {"source_id": "S2", "stance": "unclear", "note": "[DEMO] Budget figures only."},
    ],
    # A model error the safeguard exists for: an outlet's summary taken as her words.
    "verdict": "supported",
    "explanation": "[DEMO] The Riverton Ledger reports the plan halves bus fares [S1].",
    "key_findings": [
        {
            "text": "[DEMO] Outlet's analysis: the plan effectively halves fares.",
            "stance": "supports",
            "source_ids": ["S1"],
        },
    ],
    "limitations": ["[DEMO] Synthetic data for a screenshot; not a real fact-check."],
}

_EVAL_BRIDGE: dict[str, object] = {
    "source_assessments": [
        {"source_id": "S1", "stance": "supports", "note": "[DEMO] Opening date stated."},
        {"source_id": "S2", "stance": "supports", "note": "[DEMO] Same date, separate report."},
    ],
    "verdict": "supported",
    "explanation": (
        "[DEMO] The city's project page [S1] and a regional paper [S2] both give a 2019 opening."
    ),
    "key_findings": [
        {
            "text": "[DEMO] Opened to traffic in May 2019.",
            "stance": "supports",
            "source_ids": ["S1"],
        },
        {
            "text": "[DEMO] Independent report, same date.",
            "stance": "supports",
            "source_ids": ["S2"],
        },
    ],
    "limitations": ["[DEMO] Synthetic data for a screenshot; not a real fact-check."],
}


def _grounded(fares: bool, model: str) -> GroundedResult:
    if fares:
        sources = [
            ("https://ledger.example.org/riverton-transport-plan", "Riverton Ledger (demo)"),
            ("https://budget.example.gov/riverton-2026", "Riverton budget office (demo)"),
        ]
        excerpts = [
            "[DEMO] Doe's transport plan would effectively halve bus fares, the paper's "
            "analysis concludes.",
            "[DEMO] The draft budget lists a fare subsidy line but no fare level.",
        ]
    else:
        sources = [
            ("https://city.example.gov/projects/river-bridge", "City of Riverton (demo)"),
            ("https://news.example.net/2019/05/bridge-opens", "Valley News (demo)"),
        ]
        excerpts = [
            "[DEMO] The river bridge opened to traffic on 14 May 2019.",
            "[DEMO] Drivers crossed the new bridge for the first time in May 2019.",
        ]
    return GroundedResult(
        text="",
        sources=[
            GroundingSource(i, url, title, url.split("/")[2])
            for i, (url, title) in enumerate(sources)
        ],
        supports=[GroundingSupportSpan(e, (i,)) for i, e in enumerate(excerpts)],
        queries=["demo query"],
        usage=_USAGE,
        model=model,
        raw_metadata_present=True,
    )


class DemoLLM:
    def __init__(self) -> None:
        self.claim = _FARES

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
        name = schema.__name__
        if name == "ExtractionOutput":
            out: dict[str, object] = _EXTRACTION
        elif name == "ClaimAnalysisOutput":
            self.claim = _FARES if "bus fares" in prompt else _BRIDGE
            out = _analysis(self.claim)
        else:
            out = _EVAL_FARES if self.claim == _FARES else _EVAL_BRIDGE
        return LLMResult(text=json.dumps(out), usage=_USAGE, model="demo-model")

    async def grounded_search(self, *, model: str, system: str, prompt: str) -> GroundedResult:
        await asyncio.sleep(_DELAY)
        return _grounded(self.claim == _FARES, "demo-model")


def main() -> None:
    settings = Settings(
        GEMINI_API_KEY="demo-not-a-real-key",  # secret-scan: allow (fake)
        YOUVERIFY_RESOLVE_REDIRECTS=False,
        YOUVERIFY_CACHE_TTL_SEC=0,
        _env_file=None,  # type: ignore[call-arg]
    )
    uvicorn.run(
        create_app(settings, llm=DemoLLM()), host="127.0.0.1", port=8000, log_level="warning"
    )


if __name__ == "__main__":
    main()
