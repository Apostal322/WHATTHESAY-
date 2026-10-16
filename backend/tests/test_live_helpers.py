import json
from pathlib import Path

from app.evaluation.live import (
    ClipRun,
    LiveCase,
    clipping_verdict,
    expected_clip_tokens,
    html_to_text,
    lexical_support,
    needs_review,
)

REPO = Path(__file__).resolve().parents[2]


def test_clipping_verdict() -> None:
    short = ClipRun(20, 2000, 700, 640)
    assert clipping_verdict(short, ClipRun(50, 5000, 1750, 1600))[0] == "clipping_honoured"
    # Full video processed both times -> equal media tokens
    assert (
        clipping_verdict(ClipRun(20, 90_000, 60_000, 30_000), ClipRun(50, 90_100, 60_000, 30_000))[
            0
        ]
        == "clipping_ignored"
    )
    assert clipping_verdict(short, ClipRun(50, 2600, 900, 800))[0] == "inconclusive"
    # Falls back to prompt tokens when modality details are missing
    assert (
        clipping_verdict(ClipRun(20, 1000, None, None), ClipRun(50, 2500, None, None))[0]
        == "clipping_honoured"
    )


def test_expected_clip_tokens() -> None:
    assert expected_clip_tokens(20, 0.5, "low") == 10 * 70 + 640


def test_html_to_text_strips_scripts_and_entities() -> None:
    raw = "<html><script>var x='Employment';</script><p>Growth of 17&nbsp;%</p><style>p{}</style></html>"
    assert html_to_text(raw) == "Growth of 17 %"


def test_lexical_support() -> None:
    page = "Employment of software developers is projected to grow 17 percent from 2023 to 2033."
    ok = lexical_support("Software developer employment is projected to grow 17 percent.", page)
    assert ok["numbers_found"] == ok["numbers"] == 1
    assert not needs_review(ok)
    bad = lexical_support("Developer employment collapsed by 80 percent in 2025.", page)
    assert bad["numbers_found"] < bad["numbers"]
    assert needs_review(bad)


def test_template_cases_are_placeholders() -> None:
    lines = (REPO / "eval" / "live_cases.template.jsonl").read_text(encoding="utf-8").splitlines()
    cases = [LiveCase.model_validate(json.loads(line)) for line in lines if line.strip()]
    assert cases and all(c.is_placeholder() for c in cases)
    assert {c.language for c in cases} >= {"en", "ru"}
