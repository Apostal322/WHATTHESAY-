"""Semantic entailment stage for attribution claims (step 3 in `attribution.py`).

The evaluator judges all sources at once and can call a real quotation "supporting" because it
is by the right person on the right topic. This stage asks a narrower question about each
candidate passage alone: do the speaker's words say what the claim says? It sees only the claim,
the named speaker and the passages: no verdict, no other sources, nothing to anchor on.

Backend choice (2026-10-19): a bounded call to the verification model, because it is already
configured, handles Russian <-> English and reported speech, and costs one call only for
attribution claims that reach a confident verdict. An offline multilingual NLI model (XNLI-style
mDeBERTa or XLM-R) would be free per call but adds a large dependency, is trained on short
sentence pairs rather than transcript lines, and could not be downloaded or evaluated in the
development environment. `EntailmentChecker` is the seam for adding and comparing one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.errors import AppError
from app.models.llm_outputs import EntailmentOutput
from app.services import prompts
from app.services.attribution import AttributionCheck, Judgments, Relation
from app.services.llm import LLMClient, Usage
from app.services.structured import clip, parse_model_json

MAX_PARTS = 6


@dataclass(frozen=True)
class EntailmentResult:
    judgments: Judgments | None  # None: the check could not be completed
    usage: Usage
    model: str | None
    failure: str | None = None  # why it could not be completed (no model text, no secrets)


class EntailmentChecker(Protocol):
    name: str

    async def judge(self, claim: str, speaker: str, check: AttributionCheck) -> EntailmentResult:
        """Judge every passage for every claim part; a partial answer is a failed check."""
        ...


class ModelEntailmentChecker:
    name = "model"

    def __init__(self, llm: LLMClient, model: str) -> None:
        self._llm = llm
        self._model = model

    async def judge(self, claim: str, speaker: str, check: AttributionCheck) -> EntailmentResult:
        try:
            res = await self._llm.generate_json(
                model=self._model,
                system=prompts.ENTAILMENT_SYSTEM,
                prompt=prompts.entailment_prompt(
                    claim=claim,
                    speaker=speaker,
                    passages=[(p.id, p.text) for p in check.passages],
                    translated_quote=check.translated_quote,
                ),
                schema=EntailmentOutput,
            )
        except AppError as exc:
            return EntailmentResult(None, Usage(), None, f"provider_error:{exc.code}")
        try:
            out = parse_model_json(res.text, EntailmentOutput)
        except AppError:
            return EntailmentResult(None, res.usage, res.model, "malformed_output")
        judgments, failure = _complete_judgments(out, [p.id for p in check.passages])
        return EntailmentResult(judgments, res.usage, res.model, failure)


def _complete_judgments(
    out: EntailmentOutput, passage_ids: list[str]
) -> tuple[Judgments | None, str | None]:
    """Accept only a complete answer: anything missing could hide an unconfirmed part.

    Every passage needs a speaker judgment, every part a relation for every passage, and the
    claim 1 to MAX_PARTS parts (more are not dropped: the check fails instead). A passage or
    relation judged twice, differently, is taken as "not established" (not named, neutral).
    """
    known = set(passage_ids)
    speakers: dict[str, bool] = {}
    for sp in out.speakers:
        if sp.passage_id in known:
            speakers[sp.passage_id] = speakers.get(sp.passage_id, sp.speaker_is_named) and (
                sp.speaker_is_named
            )
    if set(speakers) != known:
        return None, "incomplete_output:speakers"
    if not out.parts:
        return None, "incomplete_output:no_parts"
    if len(out.parts) > MAX_PARTS:
        return None, f"too_many_parts:{len(out.parts)}"
    parts: list[tuple[str, dict[str, Relation]]] = []
    for part in out.parts:
        relations: dict[str, Relation] = {}
        for r in part.relations:
            if r.passage_id in known:
                previous = relations.get(r.passage_id, r.relation)
                relations[r.passage_id] = r.relation if previous == r.relation else "neutral"
        if set(relations) != known:
            return None, "incomplete_output:relations"
        parts.append((clip(part.text, 300) or "", relations))
    return Judgments(speakers, tuple(parts)), None
