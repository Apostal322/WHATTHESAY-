"""Validation of untrusted model output and usage accounting."""

from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.errors import InvalidModelOutputError
from app.models.schemas import TokenUsage
from app.services.llm import Usage

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_model_json(text: str, schema: type[T]) -> T:
    """Parse and validate model JSON. Tolerates markdown fences; nothing else."""
    cleaned = _FENCE_RE.sub("", text.strip()).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise InvalidModelOutputError("The model returned malformed JSON.") from exc
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        fields = ", ".join(".".join(str(p) for p in e["loc"]) for e in exc.errors()[:5])
        raise InvalidModelOutputError(
            f"The model output did not match the expected schema ({fields})."
        ) from exc


def clip(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    text = " ".join(text.split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def to_token_usage(usage: Usage, settings: Settings, search_queries: int = 0) -> TokenUsage:
    """Token usage plus an optional cost estimate.

    The estimate covers tokens and, when priced, executed grounding searches.
    Free-tier search allowances are not modelled.
    """
    cost: float | None = None
    if (
        settings.price_input_per_million is not None
        and settings.price_output_per_million is not None
    ):
        cost = (
            usage.prompt_tokens / 1e6 * settings.price_input_per_million
            + usage.output_tokens / 1e6 * settings.price_output_per_million
        )
        if search_queries and settings.price_per_1k_search_queries is not None:
            cost += search_queries / 1000 * settings.price_per_1k_search_queries
        cost = round(cost, 6)
    return TokenUsage(
        prompt_tokens=usage.prompt_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        video_tokens=usage.video_tokens,
        audio_tokens=usage.audio_tokens,
        search_queries=search_queries,
        estimated_cost_usd=cost,
    )
