"""Verify a reference record's propositions one at a time.

A compound claim ("he said X, then the very next day reversed course and said Y") can be
right in substance and wrong in one clause. Running the normal verification pipeline once
per proposition, against the same evidence, shows which part the system gets right and
which part it confuses (e.g. an outlet's characterization taken for a quotation).

Scores are provisional unless the reference record is human-verified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.errors import AppError
from app.evaluation.chronology import check_temporal
from app.evaluation.reference import ReferenceRecord, score_proposition
from app.models.schemas import Language, VerifyRequest, VerifyResponse
from app.services.verification import VerificationService


@dataclass
class PropositionResult:
    proposition_id: str
    kind: str
    response: VerifyResponse | None
    error: str | None
    score: dict[str, Any]
    chronology: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposition_id": self.proposition_id,
            "kind": self.kind,
            "verification": self.response.model_dump() if self.response else None,
            "error": self.error,
            "reference_score": self.score,
            "chronology": self.chronology,
        }


async def verify_propositions(
    service: VerificationService,
    ref: ReferenceRecord,
    *,
    only: list[str] | None = None,
    language: Language = "en",
) -> list[PropositionResult]:
    out: list[PropositionResult] = []
    for prop in ref.propositions:
        if only and prop.id not in only:
            continue
        resp: VerifyResponse | None = None
        error: str | None = None
        try:
            resp = await service.verify(
                VerifyRequest(claim=prop.text, context=ref.context, language=language)
            )
        except AppError as exc:
            error = exc.code
        out.append(
            PropositionResult(
                proposition_id=prop.id,
                kind=prop.kind,
                response=resp,
                error=error,
                score=score_proposition(resp.verdict if resp else None, prop, ref),
                chronology=check_temporal(ref, prop) if prop.temporal else None,
            )
        )
    return out
