"""Attribution safeguard: a claim about what someone said needs that person's own words.

"X said Y" is a different proposition from "Y is true". Evidence for it is the speaker's
words: a quotation or a transcript line. An outlet's characterization ("X effectively backs
Y") is an author's interpretation, however accurate, and must not alone produce a confident
verdict about what X said. That holds in both directions: it can neither confirm nor refute it.

Three questions are kept apart, and a confident verdict needs all three:

1. *Identity*: are these the speaker's own words? Direct speech (a quotation or a transcript
   line) attributed to the named speaker in the excerpt itself. Deterministic when the names are
   in the same script; across scripts (Ivanov / Иванов) a name cannot be matched by spelling,
   so the semantic stage (3) decides who is speaking.
2. *Authenticity*: does the evidence really contain them? Verbatim page text, or the same
   evidence from at least two independent origins when it is only snippets or model summaries.
   Exact-quote claims (words the claim puts in quotation marks, or `exact_words` from the
   analysis) also need those words verbatim, unless the quote and the evidence are in
   different scripts, where only a faithful translation can be checked (by stage 3).
3. *Entailment*: do those words actually say what the claim says? A separate, narrow model
   call (see `entailment.py`) judges each candidate passage: entails, contradicts or neutral.
   A real quotation from the right person about something else is neutral and backs nothing.

Steps 1-2 run here, after the model's evaluation, with no model call; when they pass,
`AttributionCheck.passages` lists what step 3 must judge, and `apply_entailment` decides the
verdict from those judgments. A failed check makes the verdict `insufficient_evidence`; it
never turns an uncertain result into the opposite verdict. The change is recorded as a
`VerdictAdjustment` (a safeguard), separate from advisory guardrail notes (warnings).

Limits, stated plainly: "direct speech" is detected from quotation marks and transcript
speaker labels; whose words a quotation holds is inferred from the speaker being named in the
same excerpt or label (and, across scripts, by the model); and a claim may be treated as an
attribution when it only cites a report ("the study says"). Stage 3 is a model judgement: its
accuracy is only as good as the model's, and has not been measured on labelled cases.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

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
# Transcript speaker labels: "DAVIS:" / "LINSEY DAVIS:" / "ИВАНОВ:" anywhere, or "Donald Trump:" /
# "Иван Иванов:" at the start. Latin and Cyrillic.
_UP, _LOW = "A-ZА-ЯЁ", "a-zа-яё"
_LABEL_CAPS = re.compile(
    rf"(?:^|(?<=[\s.?!]))([{_UP}][{_UP}.'\-]+(?: [{_UP}][{_UP}.'\-]+){{0,3}}):\s+\S"
)
_LABEL_TITLE = re.compile(rf"^([{_UP}][{_LOW}'\-]+(?: [{_UP}][{_LOW}'.\-]+){{0,3}}):\s+\S")
_NOT_SPEAKERS = {
    "note", "update", "updated", "breaking", "editor's note", "correction", "photo", "video",
    "watch", "read more", "related", "source", "sources", "synthetic", "advertisement", "q", "a",
    "примечание", "обновлено", "срочно", "фото", "видео", "источник", "источники", "реклама",
    "вопрос", "ответ", "в", "о",
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


_CYRILLIC = re.compile(r"[а-яё]", re.I)
_LATIN = re.compile(r"[a-z]", re.I)
MAX_PASSAGES = 8
MAX_PASSAGE_CHARS = 700


def _scripts(text: str) -> set[str]:
    return {name for name, rx in (("cyr", _CYRILLIC), ("lat", _LATIN)) if rx.search(text)}


def _cross_script(names: str, text: str) -> bool:
    """`names` cannot be found in `text` by spelling: they share no script."""
    a, b = _scripts(names), _scripts(text)
    return bool(a and b) and not (a & b)


@dataclass(frozen=True)
class Passage:
    """One excerpt that may hold the speaker's own words, for the entailment stage."""

    id: str
    source: EvidenceSource
    text: str


@dataclass(frozen=True)
class AttributionCheck:
    verdict: Verdict
    adjustment: VerdictAdjustment | None = None
    # Non-empty: the verdict stands only if `apply_entailment` confirms it.
    passages: tuple[Passage, ...] = ()
    # The claim quotes words in another script than the evidence: only a translation can match.
    translated_quote: bool = False


def check_attribution_evidence(
    verdict: Verdict,
    stances: dict[str, Stance],
    sources: dict[str, EvidenceSource],
    attribution: Attribution,
) -> AttributionCheck:
    """Steps 1-2 (identity, authenticity) and the passages step 3 must judge."""
    if not attribution.is_attribution or verdict not in ("supported", "contradicted"):
        return AttributionCheck(verdict)
    wanted: Stance = "supports" if verdict == "supported" else "contradicts"
    basis = [sources[sid] for sid, st in stances.items() if st == wanted]
    groups = origin_groups(list(sources.values()))
    names = " ".join([attribution.speaker, *sorted(attribution.speaker_tokens)])

    def own_words(excerpt: str) -> bool:
        if has_direct_speech(excerpt, attribution.speaker_tokens):
            return True
        # Across scripts the name cannot match by spelling; direct speech by anyone is a
        # candidate whose speaker the entailment stage must confirm.
        return _cross_script(names, excerpt) and has_direct_speech(excerpt)

    translated = False
    if verdict == "supported" and attribution.exact_words:
        missing = [
            w for w in attribution.exact_words if not _confirmed_by(basis, _contains(w), groups)
        ]
        candidates = [e for s in basis for e in s.excerpts if own_words(e)]
        translated = bool(candidates) and all(
            _cross_script(w, e) for w in missing for e in candidates
        )
        if missing and not translated:
            return AttributionCheck(
                "insufficient_evidence",
                VerdictAdjustment(
                    rule="exact_quote_not_confirmed",
                    from_verdict=verdict,
                    to_verdict="insufficient_evidence",
                    reason=(
                        "The claim quotes exact words, but no source backing it contains them "
                        "verbatim (in page text, or in two independent sources): "
                        + "; ".join(f"“{w}”" for w in missing)
                    ),
                ),
            )

    if not _confirmed_by(basis, own_words, groups):
        speaker = f" by {attribution.speaker}" if attribution.speaker else ""
        direction = "confirm" if verdict == "supported" else "refute"
        return AttributionCheck(
            "insufficient_evidence",
            VerdictAdjustment(
                rule="attribution_without_direct_evidence",
                from_verdict=verdict,
                to_verdict="insufficient_evidence",
                reason=(
                    f"The claim is about what was said{speaker}. None of the sources assessed "
                    f"as backing the verdict contains the speaker's own words (a quotation or "
                    f"transcript line) in page text or in two independent sources, so a report "
                    f"or characterization cannot {direction} it on its own."
                ),
            ),
        )

    # Page text first: it is what the authenticity rule trusts most.
    ordered = sorted(basis, key=lambda s: s.origin != "page_text")
    excerpts = [(s, e) for s in ordered for e in s.excerpts if own_words(e)][:MAX_PASSAGES]
    # Who is speaking is always confirmed by the entailment stage too, not only by spelling.
    passages = tuple(
        Passage(id=f"P{i}", source=s, text=e[:MAX_PASSAGE_CHARS])
        for i, (s, e) in enumerate(excerpts, 1)
    )
    return AttributionCheck(verdict, passages=passages, translated_quote=translated)


Relation = Literal["entails", "contradicts", "neutral"]


@dataclass(frozen=True)
class Judgments:
    """Entailment stage output: who speaks in each passage; each claim part's relations."""

    speaker_is_named: dict[str, bool]
    parts: tuple[tuple[str, dict[str, Relation]], ...]


def apply_entailment(
    check: AttributionCheck,
    judgments: Judgments | None,
    sources: dict[str, EvidenceSource],
    speaker: str,
) -> tuple[Verdict, VerdictAdjustment | None]:
    """Step 3: keep a confident verdict only if the speaker's words are judged to say it.

    Each part of the claim is confirmed one way when passages by the named speaker judged that
    way meet the authenticity rule (page text, or two independent origins). Then:

    - `supported` stands only if every part is confirmed as said.
    - `contradicted` stands only if a part is confirmed as contradicted and none as said (a
      conjunction is false if one part is).
    - Some parts said and others contradicted or not established, or the speaker's own words
      going both ways on a part: `mixed`.
    - Nothing confirmed in the verdict's direction: `insufficient_evidence`. A disagreement
      between the evaluator and this stage never flips the verdict to its opposite.

    `judgments` is None when the stage could not run: the verdict cannot be confirmed.
    """
    verdict = check.verdict
    if not check.passages:
        return verdict, None
    who = f" by {speaker}" if speaker else ""

    def change(to: Verdict, reason: str) -> tuple[Verdict, VerdictAdjustment]:
        return to, VerdictAdjustment(
            rule="attribution_not_entailed", from_verdict=verdict, to_verdict=to, reason=reason
        )

    if judgments is None or not judgments.parts:
        return change(
            "insufficient_evidence",
            f"The claim is about what was said{who}. The check of whether the speaker's own "
            "words actually say this could not be completed, so the verdict cannot be confirmed.",
        )
    groups = origin_groups(list(sources.values()))

    def confirmed(relations: dict[str, Relation], relation: Relation) -> bool:
        hits = [
            p.source
            for p in check.passages
            if relations.get(p.id) == relation and judgments.speaker_is_named.get(p.id, False)
        ]
        if any(s.origin == "page_text" for s in hits):
            return True
        return len({groups[s.id] for s in hits}) >= MIN_INDEPENDENT_UNVERIFIED

    said: list[str] = []
    denied: list[str] = []
    both: list[str] = []
    open_parts: list[str] = []
    for text, relations in judgments.parts:
        yes, no = confirmed(relations, "entails"), confirmed(relations, "contradicts")
        (both if yes and no else said if yes else denied if no else open_parts).append(text)

    def listed(parts: list[str]) -> str:
        return "; ".join(f"“{p}”" for p in parts)

    if verdict == "supported" and not (denied or both or open_parts):
        return verdict, None
    # "X said A and B" is false if X's own words contradict A, whatever B.
    if verdict == "contradicted" and denied and not (said or both):
        return verdict, None
    if both:
        return change(
            "mixed",
            f"The speaker's own words in the sources both say and contradict {listed(both)}; "
            "they said different things at different times or places.",
        )
    if said and (denied or open_parts):
        return change(
            "mixed",
            f"The speaker's own words confirm part of the claim ({listed(said)}) but not the "
            f"rest ({listed(denied + open_parts)}).",
        )
    if (verdict == "supported" and denied) or (verdict == "contradicted" and said):
        return change(
            "insufficient_evidence",
            "The evaluation and the separate check of the speaker's own words disagree about "
            "what was said, so no confident verdict is given.",
        )
    return change(
        "insufficient_evidence",
        f"The sources contain the speaker's own words{who}, but none of them says what the "
        "claim says (they are about something else, or only touch on it).",
    )
