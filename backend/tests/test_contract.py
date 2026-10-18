"""The extension's TypeScript API types must match the backend's Pydantic schemas.

A field the extension sends but the backend does not declare is rejected (requests forbid extra
fields); a field the extension requires but the backend never sends breaks the UI. This parses
`extension/src/shared/types.ts` (interfaces only, no TS toolchain needed) and compares names,
required-ness and literal value sets.
"""

from __future__ import annotations

import re
import typing
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.models import schemas

TYPES_TS = Path(__file__).resolve().parents[2] / "extension" / "src" / "shared" / "types.ts"
_INTERFACE = re.compile(r"export interface (\w+) \{(.*?)\n\}", re.S)
_FIELD = re.compile(r"^\s*(\w+)(\?)?:", re.M)
_ALIAS = re.compile(r'export type (\w+) = ((?:\s*\|?\s*"[^"]+")+);')


def ts_interfaces() -> dict[str, dict[str, bool]]:
    """Interface name -> {field: optional}."""
    text = TYPES_TS.read_text(encoding="utf-8")
    out: dict[str, dict[str, bool]] = {}
    for name, body in _INTERFACE.findall(text):
        body = re.sub(r"//.*|/\*.*?\*/", "", body, flags=re.S)
        # Only top-level fields: drop nested `{ ... }` object literals first.
        flat = re.sub(r"\{[^{}]*\}", "{}", body)
        out[name] = {f: bool(opt) for f, opt in _FIELD.findall(flat)}
    return out


def ts_literal_unions() -> dict[str, set[str]]:
    text = TYPES_TS.read_text(encoding="utf-8")
    return {n: set(re.findall(r'"([^"]+)"', v)) for n, v in _ALIAS.findall(text)}


RESPONSES = [
    "TokenUsage",
    "ExtractResponse",
    "SourceRef",
    "KeyFinding",
    "VerdictAdjustment",
    "VerifyResponse",
    "HealthResponse",
]
REQUESTS = ["ExtractRequest", "VerifyRequest"]


@pytest.mark.parametrize("name", RESPONSES)
def test_response_fields_match(name: str) -> None:
    model: type[BaseModel] = getattr(schemas, name)
    ts = ts_interfaces()[name]
    backend = set(model.model_fields)
    # Everything the UI requires is sent; anything the backend sends is known to the UI.
    missing_in_backend = {f for f, optional in ts.items() if not optional} - backend
    unknown_to_ui = backend - set(ts)
    assert not missing_in_backend, f"{name}: UI requires fields the API never sends"
    assert not unknown_to_ui, f"{name}: API fields missing from types.ts: {unknown_to_ui}"


@pytest.mark.parametrize("name", REQUESTS)
def test_request_fields_are_accepted(name: str) -> None:
    model: type[BaseModel] = getattr(schemas, name)
    ts = ts_interfaces()[name]
    assert set(ts) <= set(model.model_fields), f"{name}: the API would reject these fields"
    required = {f for f, info in model.model_fields.items() if info.is_required()}
    assert required <= {f for f, optional in ts.items() if not optional}


@pytest.mark.parametrize(
    ("ts_name", "py_type"),
    [
        ("ClaimType", schemas.ClaimType),
        ("Verdict", schemas.Verdict),
        ("Stance", schemas.Stance),
        ("Language", schemas.Language),
    ],
)
def test_literal_value_sets_match(ts_name: str, py_type: object) -> None:
    assert ts_literal_unions()[ts_name] == set(typing.get_args(py_type))


def test_verdict_adjustment_rules_match() -> None:
    rule = schemas.VerdictAdjustment.model_fields["rule"].annotation
    text = TYPES_TS.read_text(encoding="utf-8")
    body = re.search(r"interface VerdictAdjustment \{(.*?)\}", text, re.S)
    assert body is not None
    assert set(re.findall(r'"([^"]+)"', body.group(1))) == set(typing.get_args(rule))
