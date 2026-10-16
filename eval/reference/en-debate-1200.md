# Fact-check reference: en-debate-1200 (ABC News debate, 10 September 2024)

> **What this is:** a *reference record*, the answer key the application is scored against. It was
> drafted by an AI assistant and has **not** been signed off by a person (`review.status:
> ai_reviewed`). Scores against it are **provisional** and are never reported as accuracy.
>
> **What this is not:** a fact-check produced by YouVerify, or a record of sources anyone on the
> project has read. Machine-readable version: `en-debate-1200.json`. Offline evidence fixture:
> `eval/fixtures/en-debate-1200/`.

## 0. How each source's status is recorded

Four states are kept apart, because they are easy to conflate:

| State | Field | Meaning | In this record |
|---|---|---|---|
| Externally located | `located_by`, `located_on`, `user_statement` | Someone found the URL and (maybe) said what it contains. **Nobody has necessarily read it.** | U1–U5 (user); R1–R6 (AI assistant's web search) |
| Inspected by the application | `inspected_by_app`, `app_capture` | The app's own fetcher opened the page and kept verbatim excerpts in the offline fixture | **none yet** (every source host is blocked from the development environment) |
| Human-approved | `human_approved` | A named person checked the source against the propositions. Only possible once `reviewer_kind` is `human` | **none** |
| AI interpretation | `stance_basis` | A stance an AI inferred, and from what | all stances on R1–R6 (`ai_interpretation_of_search_summary`) |

The schema enforces this: an AI reviewer cannot mark the record `human_verified` or a source
`human_approved`; a stance must state its basis; "inspected by the app" requires a named capture.

## 1. The statement

| | Value | How established |
|---|---|---|
| Video | https://www.youtube.com/watch?v=VgsC_aBquUE, about 20:00 (window 1155–1210 s) | evaluation case |
| Transcript position | Roll Call Factbase (U5): about **15:55–16:12** of *its* timeline | **user-reported** (2026-10-14); not opened by the project. **Not mapped to the YouTube upload**: the upload's timeline can differ (pre-show, cuts), and the case's 20:00 is ~4 minutes later. ABC's transcript (U1) has no timestamps |
| Speaker | Linsey Davis (ABC moderator), questioning Donald Trump | user-reported from U5 + secondary reports; not checked against a transcript |
| Words | "…you initially had said that it was too short and you said quote I'm going to be voting that we need more than six weeks, but then the very next day you reversed course and said you would vote to support the six-week ban." | **model transcription** (`gemini-3.6-flash`, 2026-10-14); unverified |

## 2. Historical context (as of 10 September 2024)

- Florida's six-week abortion law was in force.
- Amendment 4, a November 2024 ballot initiative, would have barred laws restricting abortion before
  viability (official record: U3, located by the user, not yet opened by the project).
- In late August 2024 Trump, a Florida voter, was asked how he would vote.
- The claim is judged on what was on record by the debate date (`claim_date`). Amendment 4 later
  failed (November 2024); that is after the claim date and irrelevant to its accuracy.

## 3. The claim, split into independently testable propositions

| # | Proposition | Kind | Reference (acceptable) | Basis |
|---|---|---|---|---|
| P1 | Trump said the six-week limit was too short and he'd be "voting that we need more than six weeks" | attribution: his **position on the limit** | supported | NBC interview as reported 29 Aug 2024 (R1, R2, R4, R6) |
| P2 | He then said he would vote against Amendment 4 | attribution: his **stated vote** | supported | Fox News, 30 Aug 2024: "I will be voting no" as reported (R3–R6). Primary: U4, U2 (unopened) |
| P3 | That came "the very next day" | temporal: **chronology** | supported (mixed) | E1 29 Aug → E2 30 Aug, both *reported* dates; checked deterministically (`chronology.py`): **consistent, unconfirmed** |
| P4 | Voting no = "vote to support the six-week ban" | characterization | mixed | Accurate in effect (P5), **not his words**: the phrase is the moderator's and some headlines'. Presenting it as Trump's quotation is an error |
| P5 | Amendment 4 would have barred pre-viability restrictions, so a "no" vote is a vote to keep the six-week law | legal_effect: **practical implication** | supported | Primary: U3 (unopened). Rests on secondary summaries until U3 is inspected |

P1 and P2 are different acts: criticising the limit is not a vote on the amendment. P4 is
judged on wording, P5 on law: a system can be right about P5 and still wrong to put P4's phrase
in Trump's mouth.

## 4. Sources and provenance

| id | Source | Type | Located by | Opened? | App-inspected? | Stances (basis) |
|---|---|---|---|---|---|---|
| U5 | [Roll Call Factbase, debate transcript](https://rollcall.com/factbase/trump/transcript/donald-trump-debate-first-presidential-kamala-harris-philadelphia-september-10-2024) | primary transcript | user, 2026-10-14 | no (DNS blocked) | no (pending capture) | none |
| U1 | [ABC News debate transcript](https://www-cdn.abcnews.com/Politics/harris-trump-presidential-debate-transcript/story?id=113560542) | primary transcript | user | no | no | none |
| U2 | [Reuters, 30 Aug 2024](https://www.reuters.com/world/us/trump-says-hell-vote-against-florida-amendment-enshrining-abortion-rights-2024-08-30/) | wire report | user, 2026-10-14 | no (refused) | no | none |
| U3 | [Florida Department of State, Amendment 4 record](https://constitutionalinitiatives.dos.fl.gov/Home/InitDetail?account=83927&seqnum=1) | official record | user, 2026-10-14 | no (DNS blocked) | no | none |
| U4 | [Roll Call Factbase, Fox interview 30 Aug 2024](https://rollcall.com/factbase/trump/transcript/donald-trump-interview-bryan-llenas-fox-news-august-30-2024/) | primary transcript | user | no | no | none |
| R1 | [Axios, 29 Aug](https://www.axios.com/2024/08/29/trump-abortion-limit-too-short) | news | AI search | no | no | P1 (AI, search summary) |
| R2 | [Florida Phoenix, 29 Aug](https://floridaphoenix.com/2024/08/29/donald-trump-says-floridas-6-week-abortion-law-is-too-short/) | news | AI search | no | no | P1 (AI) |
| R3 | [Fox News, 30 Aug](https://www.foxnews.com/politics/trump-opposes-floridas-amendment-4-which-offers-unfettered-abortion-access-its-radical) | news | AI search | no | no | P2 (AI) |
| R4 | [Catholic News Agency, 30 Aug](https://www.catholicnewsagency.com/news/259037/trump-says-he-will-vote-no-on-florida-pro-abortion-amendment) | news | AI search | no | no | P1–P3; P4 partly (AI) |
| R5 | [CBS News](https://www.cbsnews.com/amp/news/trump-florida-abortion-ban-amendment-vote) | news | AI search | no | no | P2; P4 partly (AI) |
| R6 | [OSV News](https://www.oursundayvisitor.com/trump-calls-florida-6-week-abortion-ban-protecting-most-unborn-children-too-short/) | news | AI search | no | no | P1, P2; P4 partly (AI) |

**Independence:** R4 is syndicated (NC Register, EWTN), and R2 is republished by the Louisiana
Illuminator; each counts once. R1–R6 report the same two interviews. The primary records are U4
(Fox) and U5/U1 (debate); for the NBC interview no primary record is in the set.

**Context, not verdicts:** the official record (U3) and the historical reporting establish what the
amendment did and what was said; they do not by themselves decide whether the moderator's wording
was fair. That judgement is P4's, and it is the part most likely to be gotten wrong.

## 5. Verdict

**Reference verdict: supported**, with a wording caveat. **Also acceptable: mixed.**

The checkable substance holds (P1–P3, P5). "Said you would vote to support the six-week ban"
describes the *effect* of the vote, not his words (P4), and he kept calling six weeks too short.

| System answer | Scored as |
|---|---|
| `supported` with the caveat, or `mixed` separating effect from wording | acceptable |
| `contradicted` | **false confident verdict** |
| `insufficient_evidence` | a miss |

Per proposition, `supported` on P4 is a false confident verdict: it means the system took a
characterization for a quotation.

## 6. Application runs for this claim

**No verdict has been produced by the application.** Live attempts (2026-10-13/14) are in
`eval/factchecks/en-debate-1200-*.{json,md}` and report §7b–7d: extraction succeeded once; verification
failed on Gemini 503 overload and a grounding 429 quota. On 2026-10-14 the repeated retries were
stopped. The next run should use `scripts/run_fixture_case.py` (offline evidence, live evaluator; 10
calls for all five propositions) once the fixture has captured excerpts.

## 7. Open items before `human_verified`

1. Open U5 (or U1) and check the quote word for word and the speaker; note where 15:55–16:12 of the
   Factbase timeline falls in the YouTube upload.
2. Run `scripts/capture_evidence.py` on a networked machine so the app inspects U2, U3, U5 (and U4, U1),
   then review and commit the captured excerpts.
3. Check U3's text for P5.
4. Confirm the NBC interview's recording vs airing date (E1, P3).
5. A person signs off: `reviewer_kind: human`, `review.status: human_verified`, and `human_approved`
   on each source they checked.
