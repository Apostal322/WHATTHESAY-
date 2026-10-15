"""Property-based tests: invariants of the deterministic layer, over generated inputs.

These complement the example tests. A failure here is a counterexample that Hypothesis
shrinks to a minimal input; add it as an explicit regression test when fixing it.
"""

from __future__ import annotations

import ipaddress
import string

from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.rate_limit import SlidingWindowRateLimiter
from app.core.errors import RateLimitedError
from app.models.schemas import Stance, Verdict
from app.services.citations import EvidenceSource, is_safe_display_url
from app.services.evidence import canonical_url
from app.services.extraction import compute_window
from app.services.prompts import escape_untrusted
from app.services.search import SpendGuard
from app.services.verification import apply_verdict_guardrails, strip_unknown_citations

VERDICTS: list[Verdict] = [
    "supported",
    "contradicted",
    "mixed",
    "insufficient_evidence",
    "not_verifiable",
]
STANCES: list[Stance] = ["supports", "contradicts", "partially_supports", "unclear", "irrelevant"]
ids = st.sampled_from([f"S{i}" for i in range(1, 7)])


def _sources(stances: dict[str, Stance]) -> dict[str, EvidenceSource]:
    return {
        sid: EvidenceSource(sid, f"https://site{sid}.example.org/a", None, f"site{sid}.example.org")
        for sid in stances
    }


@given(st.sampled_from(VERDICTS), st.dictionaries(ids, st.sampled_from(STANCES), max_size=6))
def test_a_confident_verdict_always_has_a_source_assessed_in_its_direction(
    verdict: Verdict, stances: dict[str, Stance]
) -> None:
    final, _ = apply_verdict_guardrails(verdict, stances, _sources(stances))
    values = set(stances.values())
    if final == "supported":
        assert "supports" in values
    if final == "contradicted":
        assert "contradicts" in values
    if final == "mixed":
        assert "partially_supports" in values or {"supports", "contradicts"} <= values
    # The guardrail only ever removes confidence; it never invents a verdict.
    assert final in (verdict, "insufficient_evidence")


@given(
    st.text(alphabet=string.ascii_letters + " []S0123456789.", max_size=200),
    st.sets(ids, max_size=6),
)
def test_no_unknown_citation_survives(text: str, known: set[str]) -> None:
    import re

    out = strip_unknown_citations(text, known)
    assert all(m in known for m in re.findall(r"\[(S\d{1,2})\]", out))


_tagish = st.sampled_from(
    ["<", ">", "/", " ", "\n", "\t", "untrusted", "UNTRUSTED", "Untrusted", "x="]
)


@given(st.lists(st.one_of(_tagish, st.text(max_size=5)), max_size=30).map("".join))
def test_untrusted_text_can_never_contain_our_delimiter(text: str) -> None:
    flat = "".join(escape_untrusted(text).lower().split())
    assert "<untrusted" not in flat and "</untrusted" not in flat


@given(st.integers(0, 2**32 - 1), st.sampled_from(["dotted", "octal", "hex", "short"]))
def test_numeric_ipv4_spellings_are_displayed_only_when_public(value: int, form: str) -> None:
    ip = ipaddress.IPv4Address(value)
    a, b, c, d = ip.packed
    host = {
        "dotted": f"{a}.{b}.{c}.{d}",
        "octal": f"0{a:o}.0{b:o}.0{c:o}.0{d:o}",
        "hex": f"0x{a:x}.0x{b:x}.0x{c:x}.0x{d:x}",
        "short": f"{a}.{(b << 16) | (c << 8) | d}",
    }[form]
    if not ip.is_global:
        assert not is_safe_display_url(f"http://{host}/")


@given(
    st.sampled_from(["http", "https"]),
    st.sampled_from(["example.org", "www.example.org", "m.example.org", "EXAMPLE.org"]),
    st.lists(st.sampled_from(["a", "b", "amp", "news"]), max_size=3).map("/".join),
    st.dictionaries(st.sampled_from(["utm_source", "id", "q", "fbclid", "page"]), st.text(
        alphabet=string.ascii_letters + string.digits, min_size=1, max_size=4), max_size=3),
)  # fmt: skip
def test_canonical_url_is_idempotent(
    scheme: str, host: str, path: str, query: dict[str, str]
) -> None:
    from urllib.parse import urlencode

    url = f"{scheme}://{host}/{path}?{urlencode(query)}"
    once = canonical_url(url)
    assert canonical_url(once) == once
    assert "utm_" not in once and "fbclid" not in once


@settings(max_examples=200)
@given(
    st.floats(0.01, 5.0),
    st.lists(st.tuples(st.booleans(), st.floats(0.0, 1.0)), max_size=40),
)
def test_spend_guard_never_exceeds_its_daily_limit(
    limit: float, ops: list[tuple[bool, float]]
) -> None:
    guard = SpendGuard(limit)
    reserved: list[float] = []
    for reserve, usd in ops:
        if reserve:
            if guard.try_reserve(usd):
                reserved.append(usd)
        elif reserved:
            guard.refund(reserved.pop())
        assert guard.spent_usd <= limit + 1e-9
        assert guard.spent_usd >= 0


@given(st.integers(1, 10), st.lists(st.floats(0.0, 5.0), min_size=1, max_size=60))
def test_rate_limiter_never_admits_more_than_its_limit_per_window(
    limit: int, gaps: list[float]
) -> None:
    now = [0.0]
    limiter = SlidingWindowRateLimiter(limit, window_sec=10, clock=lambda: now[0])
    admitted: list[float] = []
    for gap in gaps:
        now[0] += gap
        try:
            limiter.check("client")
            admitted.append(now[0])
        except RateLimitedError:
            pass
        recent = [t for t in admitted if now[0] - t < 10]
        assert len(recent) <= limit


@given(
    st.floats(0, 43200),
    st.one_of(st.none(), st.floats(1, 43200)),
    st.integers(5, 300),
    st.integers(0, 120),
)
def test_video_window_is_well_formed(
    t: float, duration: float | None, before: int, after: int
) -> None:
    if duration is not None and t > duration + 1:
        return  # rejected by request validation
    w = compute_window(t, duration, before, after)
    assert 0 <= w.start_sec < w.end_sec
    assert w.end_sec - w.start_sec <= before + after or w.end_sec == w.start_sec + 1
    assert w.start_sec <= int(t)
