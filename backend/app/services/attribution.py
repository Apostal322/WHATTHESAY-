"""Attribution safeguard: a claim about what someone said needs that person's own words.

"X said Y" is a different proposition from "Y is true". Evidence for it is the speaker's
words: a quotation or a transcript line. An outlet's characterization ("X effectively backs
Y") is an author's interpretation, however accurate, and must not alone produce a confident
verdict about what X said. That holds in both directions: it can neither confirm nor refute it.

All checks here are deterministic and run after the model's evaluation:

- *Attribution claims* (detected from reported-speech verbs in the claim, or flagged by the
  analysis step): a `supported` / `contradicted` verdict needs direct speech in a source
  assessed in that direction. That means verbatim page text, or the same quotation-bearing
  evidence from at least two independent origins when it is only snippets or model summaries.
- *Exact-quote claims* (words the claim puts in quotation marks, or `exact_words` from the
  analysis): `supported` additionally needs those exact words in a supporting source,
  verbatim in page text or across two independent origins.

Otherwise the verdict becomes `insufficient_evidence` and the change is recorded as a
`VerdictAdjustment` (a safeguard), separate from advisory guardrail notes (warnings).

Limits, stated plainly: "direct speech" is detected from quotation marks and transcript
speaker labels, not from meaning; whose words a quotation holds is inferred only from the
speaker being named in the same excerpt or transcript label; and a claim may be treated as an
attribution when it only cites a report ("the study says"). In every such case the error
direction is toward `insufficient_evidence`, never toward a confident verdict.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from app.models.schemas import Stance, Verdict, VerdictAdjustment
from app.services.citations import EvidenceSource
from app.services.evidence import origin_groups

MIN_QUOTED_WORDS = 3
MIN_INDEPENDENT_UNVERIFIED = 2

# Reported speech. Deliberately absent: "claim(s)", "according to" and "state(s)", which are
# common in factual claims ("jobless claims", "according to the census", "United States").
_SPEECH_EN = re.compile(
    r"\b(say|says|said|saying|stated|stating|tell|tells|told|telling|announced|declared|"
    r"promised|vowed|pledged|wrote|admitted|insisted|argued|asserted|denied|quoted)\b",
    re.I,
)
_SPEECH_RU = re.compile(
    r"(?<![а-яё])(сказа|заяви|написа|утвержда|пообеща|сообщи|говори|призна|отрица)[а-яё]*",
    re.I,
)
# Quotation pairs: "…", “…”, „…“/„…”, «…».
_QUOTED = re.compile(r'"([^"]{3,400})"|“([^”]{3,400})”|„([^“”]{3,400})[“”]|«([^»]{3,400})»')
# Single quotes, guarded against apostrophes: the opening quote follows a space, bracket or the
# start; the closing one precedes a space, punctuation or the end; apostrophes inside a word
# ("I'm", "state's") are allowed within the span.
_SINGLE_QUOTED = re.compile(
    r"(?:^|(?<=[\s(\[]))'((?:[^']|(?<=\w)'(?=\w)){3,400}?)'(?=[\s.,;:!?)\]]|$)"
)
# Transcript speaker labels: "DAVIS:" / "LINSEY DAVIS:" anywhere, or "Donald Trump:" at the start.
_LABEL_CAPS = re.compile(r"(?:^|(?<=[\s.?!]))([A-Z][A-Z.'\-]+(?: [A-Z][A-Z.'\-]+){0,3}):\s+\S")
_LABEL_TITLE = re.compile(r"^([A-Z][a-z'\-]+(?: [A-Z][a-z'.\-]+){0,3}):\s+\S")
_NOT_SPEAKERS = {
    "note", "update", "updated", "breaking", "editor's note", "correction", "photo", "video",
    "watch", "read more", "related", "source", "sources", "synthetic", "advertisement", "q", "a",
}  # fmt: skip
_WORD = re.compile(r"\w+", re.U)
# Words that name a role, not a person: they never identify whose words a quote holds.
_TITLES = {
    "senator", "sen", "president", "former", "vice", "governor", "gov", "mayor", "candidate",
    "moderator", "mr", "mrs", "ms", "dr", "rep", "representative", "minister", "prime", "the",
    "сенатор", "президент", "губернатор", "министр", "кандидат", "ведущий", "ведущая",
}  # fmt: skip
# Capitalised words that start a sentence or a time phrase rather than name someone.
_NOT_NAMES = {
    "in", "on", "at", "before", "after", "then", "later", "earlier", "saying", "the", "a", "an",
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december", "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday",
}  # fmt: skip
_CAP_WORD = re.compile(r"[A-ZА-ЯЁ][\w'’.\-]*")


def _words(text: str) -> str:
    return " ".join(_WORD.findall(text.replace("’", "'").lower()))


def quoted_spans(text: str) -> list[str]:
    spans = [next(g for g in m.groups() if g) for m in _QUOTED.finditer(text)]
    spans += [m.group(1) for m in _SINGLE_QUOTED.finditer(text)]
    return [s.strip() for s in spans if len(_WORD.findall(s)) >= MIN_QUOTED_WORDS]


def _name_tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall(text.lower()) if len(t) >= 3 and t not in _TITLES}


def has_direct_speech(text: str, speaker: frozenset[str] = frozenset()) -> bool:
    """A quotation of at least three words, or a transcript speaker line.

    With `speaker` (name tokens of the person the claim quotes), the words must be theirs: a
    transcript line must carry their label, and a quotation must sit in an excerpt that names
    them. A moderator restating the claim, or someone else's quote, does not count.
    """
    if quoted_spans(text) and (not speaker or speaker & _name_tokens(text)):
        return True
    for regex in (_LABEL_CAPS, _LABEL_TITLE):
        for m in regex.finditer(text):
            label = m.group(1).lower()
            if label in _NOT_SPEAKERS:
                continue
            if not speaker or speaker & _name_tokens(label):
                return True
    return False


def speaker_from_claim(claim: str) -> frozenset[str]:
    """Name tokens of the capitalised words just before the first reported-speech verb."""
    m = _SPEECH_EN.search(claim) or _SPEECH_RU.search(claim)
    if not m:
        return frozenset()
    before = claim[: m.start()].split()[-6:]
    names = [
        w for w in before if _CAP_WORD.fullmatch(w.strip(",;:")) and w.lower() not in _NOT_NAMES
    ]
    return frozenset(_name_tokens(" ".join(names)))


@dataclass(frozen=True)
class Attribution:
    is_attribution: bool
    speaker: str
    exact_words: tuple[str, ...]
    speaker_tokens: frozenset[str] = frozenset()


def detect_attribution(claim: str, attributed_to: str, exact_words: list[str]) -> Attribution:
    """`claim` is the user's/extracted claim; the other two come from the analysis step.

    Quoted words are taken from the claim itself (not the model's restatement, which may add
    quotation marks to a paraphrase).
    """
    words = tuple(
        dict.fromkeys(
            w
            for w in [*quoted_spans(claim), *exact_words]
            if len(_WORD.findall(w)) >= MIN_QUOTED_WORDS
        )
    )
    reported = bool(_SPEECH_EN.search(claim) or _SPEECH_RU.search(claim))
    is_attr = bool(attributed_to.strip()) or reported or bool(words)
    tokens = frozenset(_name_tokens(attributed_to)) | speaker_from_claim(claim)
    return Attribution(is_attr, attributed_to.strip(), words, tokens)


def _contains(words: str) -> Callable[[str], bool]:
    needle = _words(words)
    return lambda excerpt: needle in _words(excerpt)


def _confirmed_by(
    basis: list[EvidenceSource], test: Callable[[str], bool], groups: dict[str, int]
) -> bool:
    """True if `test` holds for verbatim page text, or for two independent other origins."""
    hits = [s for s in basis if any(test(e) for e in s.excerpts)]
    if any(s.origin == "page_text" for s in hits):
        return True
    return len({groups[s.id] for s in hits}) >= MIN_INDEPENDENT_UNVERIFIED


def apply_attribution_safeguard(
    verdict: Verdict,
    stances: dict[str, Stance],
    sources: dict[str, EvidenceSource],
    attribution: Attribution,
) -> tuple[Verdict, VerdictAdjustment | None]:
    if not attribution.is_attribution or verdict not in ("supported", "contradicted"):
        return verdict, None
    wanted: Stance = "supports" if verdict == "supported" else "contradicts"
    basis = [sources[sid] for sid, st in stances.items() if st == wanted]
    groups = origin_groups(list(sources.values()))

    if verdict == "supported" and attribution.exact_words:
        missing = [
            w for w in attribution.exact_words if not _confirmed_by(basis, _contains(w), groups)
        ]
        if missing:
            return "insufficient_evidence", VerdictAdjustment(
                rule="exact_quote_not_confirmed",
                from_verdict=verdict,
                to_verdict="insufficient_evidence",
                reason=(
                    "The claim quotes exact words, but no source backing it contains them "
                    "verbatim (in page text, or in two independent sources): "
                    + "; ".join(f"“{w}”" for w in missing)
                ),
            )

    def own_words(excerpt: str) -> bool:
        return has_direct_speech(excerpt, attribution.speaker_tokens)

    if not _confirmed_by(basis, own_words, groups):
        speaker = f" by {attribution.speaker}" if attribution.speaker else ""
        direction = "confirm" if verdict == "supported" else "refute"
        return "insufficient_evidence", VerdictAdjustment(
            rule="attribution_without_direct_evidence",
            from_verdict=verdict,
            to_verdict="insufficient_evidence",
            reason=(
                f"The claim is about what was said{speaker}. None of the sources assessed as "
                f"backing the verdict contains the speaker's own words (a quotation or "
                f"transcript line) in page text or in two independent sources, so a report "
                f"or characterization cannot {direction} it on its own."
            ),
        )
    return verdict, None
