"""Deterministic chronology checks for a reference record's dated events.

The model is not trusted with date arithmetic or with telling "what was on record when the
claim was made" from "what happened later". This module answers both from the record's
`events`, and says how firm each date is: a relation built on reported (secondary) dates is
`consistent_unconfirmed`, never plain `consistent`.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from app.evaluation.reference import Event, Proposition, ReferenceRecord

ChronologyStatus = Literal["consistent", "consistent_unconfirmed", "inconsistent"]


def _day(value: str) -> date:
    return date.fromisoformat(value[:10])


def relation_holds(relation: str, days: int) -> bool:
    return {
        "same_day": days == 0,
        "next_day": days == 1,
        "before": days > 0,
        "after": days < 0,
    }[relation]


def check_temporal(ref: ReferenceRecord, prop: Proposition) -> dict[str, Any]:
    """Check a temporal proposition's relation against the record's event dates."""
    if prop.temporal is None:
        raise ValueError(f"{prop.id} is not a temporal proposition")
    events = {e.id: e for e in ref.events}
    first, second = events[prop.temporal.from_event], events[prop.temporal.to_event]
    days = (_day(second.date) - _day(first.date)).days
    holds = relation_holds(prop.temporal.relation, days)
    firm = first.date_basis == second.date_basis == "confirmed_primary"
    status: ChronologyStatus = (
        "inconsistent" if not holds else "consistent" if firm else "consistent_unconfirmed"
    )
    caveats = [
        f"{e.id} date is {e.date_basis.replace('_', ' ')}" + (f": {e.note}" if e.note else "")
        for e in (first, second)
        if e.date_basis != "confirmed_primary"
    ]
    return {
        "proposition": prop.id,
        "relation": prop.temporal.relation,
        "from": first.date,
        "to": second.date,
        "days": days,
        "status": status,
        "caveats": caveats,
    }


def on_record_by(ref: ReferenceRecord, as_of: str | None = None) -> dict[str, list[Event]]:
    """Split events into those on record by the claim date and those after it."""
    cutoff = as_of or ref.claim_date
    if cutoff is None:
        raise ValueError("no claim_date to judge against")
    limit = _day(cutoff)
    return {
        "on_record": [e for e in ref.events if _day(e.date) <= limit],
        "after_claim": [e for e in ref.events if _day(e.date) > limit],
    }
