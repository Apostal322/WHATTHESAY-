#!/usr/bin/env python3
"""Verify one claim end to end (analysis -> retrieval -> evaluation) and save a review artifact.

The claim comes from a saved extraction (`--from-run eval/runs/<run>/results.json --case ID`),
so no extraction call is repeated, or is typed manually (`--claim ... [--context ...]`).
Manual input is labelled as such in the artifact and is never reported as a video extraction.

    python scripts/verify_claim.py --from-run eval/runs/live-X/results.json --case en-debate-1200 \\
        --price-in 0.75 --price-out 3.75 --price-search-1k 14

Model calls: 3 per run when nothing fails (analysis, retrieval, evaluation), 4 for a claim about
what someone said that reaches a confident verdict (entailment check); each step may add one
fallback call on HTTP 503 when YOUVERIFY_FALLBACK_MODEL is set. SDK retries follow
YOUVERIFY_MAX_RETRIES (set it to 0 to bound calls exactly). Writes
eval/factchecks/<id>-<timestamp>.json, plus a Markdown review sheet next to it.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import json
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

import httpx

from app import __version__ as app_version
from app.api.deps import build_search_retriever
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.evaluation.reference import ReferenceRecord, score_proposition, score_verdict
from app.models.schemas import VerifyRequest, VerifyResponse
from app.services.extraction import EXTRACTION_PROMPT_VERSION
from app.services.llm import (
    REVOKED_KEY_FINGERPRINTS,
    GeminiClient,
    GroundedResult,
    LLMClient,
    LLMResult,
    VideoClip,
    key_fingerprint,
)
from app.services.verification import VERIFICATION_POLICY_VERSION, VerificationService

# Live artifacts hold full prompts and raw model outputs: git-ignored (eval/runs/), kept local.
OUT_DIR = REPO / "eval" / "runs" / "live-factchecks"
# A run makes at most 4 model calls (analysis, retrieval, evaluation, speaker's-words check).
MAX_CALLS_PER_RUN = 4
REFERENCE_DIR = REPO / "eval" / "reference"


def reference_score(
    case_id: str, verdict: str | None, ref_dir: Path = REFERENCE_DIR
) -> dict[str, Any] | None:
    """Score against eval/reference/<case>.json when one exists (provisional unless human-verified).

    `case_id` may name one proposition of the case, as `case:P2`.
    """
    case, _, prop_id = case_id.partition(":")
    path = ref_dir / f"{case}.json"
    if not path.exists():
        return None
    ref = ReferenceRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
    if prop_id:
        prop = ref.proposition(prop_id)
        return {
            **score_proposition(verdict, prop, ref),  # type: ignore[arg-type]
            "reference": f"{case}:{prop_id}",
            "reference_text": prop.text,
            "reference_verdict": prop.reference_verdict,
            "acceptable_verdicts": prop.acceptable_verdicts,
            "reference_review_status": ref.review.status,
        }
    return {
        **score_verdict(verdict, ref),  # type: ignore[arg-type]
        "reference": case,
        "reference_verdict": ref.reference_verdict,
        "acceptable_verdicts": ref.acceptable_verdicts,
        "reference_review_status": ref.review.status,
    }


# ---------------------------------------------------------------- provenance


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ExperimentBudgetExhausted(AppError):
    code = "experiment_budget_exhausted"
    status_code = 429


class CallLedger:
    """Model-call budget shared by every run of an experiment, across processes and restarts.

    Each call is written to the ledger *before* it is sent (status `started`) and settled
    afterwards, so failed and interrupted calls count too. No prompts or outputs are stored
    here: only what was called, when, its outcome, tokens and estimated cost.
    """

    def __init__(
        self, path: Path, budget_calls: int, budget_usd: float, prices: dict[str, float | None]
    ) -> None:
        self.path = path
        self.budget_calls = budget_calls
        self.budget_usd = budget_usd
        self.prices = prices
        self.data: dict[str, Any] = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"entries": []}
        )
        self.data["budget"] = {"calls": budget_calls, "usd": budget_usd}

    @property
    def entries(self) -> list[dict[str, Any]]:
        return self.data["entries"]  # type: ignore[no-any-return]

    def used_calls(self) -> int:
        return len(self.entries)

    def remaining_calls(self) -> int:
        return max(0, self.budget_calls - self.used_calls())

    def spent_usd(self) -> float:
        return round(sum(e.get("est_cost_usd") or 0.0 for e in self.entries), 6)

    def status(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "budget_calls": self.budget_calls,
            "used_calls": self.used_calls(),
            "remaining_calls": self.remaining_calls(),
            "failed_calls": sum(1 for e in self.entries if e.get("status") != "ok"),
            "budget_usd": self.budget_usd,
            "estimated_spend_usd": self.spent_usd(),
            "calls_with_unknown_cost": sum(
                1 for e in self.entries if e.get("est_cost_usd") is None
            ),
        }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def reserve(self, run_id: str, operation: str, model: str) -> int:
        if self.used_calls() >= self.budget_calls:
            raise ExperimentBudgetExhausted(
                f"Experiment call budget used up ({self.used_calls()}/{self.budget_calls})."
            )
        if self.spent_usd() >= self.budget_usd:
            raise ExperimentBudgetExhausted(
                f"Experiment cost budget used up (about ${self.spent_usd()} of ${self.budget_usd})."
            )
        self.entries.append(
            {
                "seq": self.used_calls() + 1,
                "run_id": run_id,
                "operation": operation,
                "model_requested": model,
                "started_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "status": "started",
            }
        )
        self._save()
        return len(self.entries) - 1

    def estimate(self, usage: dict[str, Any] | None, search_queries: int = 0) -> float | None:
        p_in, p_out = self.prices.get("input_per_M"), self.prices.get("output_per_M")
        p_search = self.prices.get("search_per_1k")
        if usage is None or p_in is None or p_out is None:
            return None  # a failed call's billing is unknown: never assumed free
        cost = (usage.get("prompt_tokens") or 0) * p_in / 1e6
        cost += (usage.get("output_tokens") or 0) * p_out / 1e6
        if search_queries:
            if p_search is None:
                return None
            cost += search_queries * p_search / 1000
        return round(cost, 6)

    def settle(self, idx: int, entry: dict[str, Any]) -> None:
        queries = len((entry.get("grounding") or {}).get("queries") or [])
        self.entries[idx].update(
            status="error" if "error" in entry else "ok",
            model_served=entry.get("model_served"),
            latency_ms=entry.get("latency_ms"),
            usage=entry.get("usage"),
            search_queries=queries,
            est_cost_usd=self.estimate(entry.get("usage"), queries),
            error_code=(entry.get("error") or {}).get("code"),
        )
        self._save()


class RecordingLLM:
    """Wraps the real client and keeps every call exactly as made and answered.

    Recorded per call: operation, requested and serving model, the full prompt (it holds the
    claim and the retrieved excerpts the model saw), the system prompt's hash, the raw output
    text, grounding sources and supports as returned, token usage, wall-clock latency, and the
    error code and message on failure. Nothing here is rewritten or summarised.
    """

    def __init__(
        self,
        inner: LLMClient,
        calls: list[dict[str, Any]],
        ledger: CallLedger | None = None,
        run_id: str = "",
    ) -> None:
        self._inner = inner
        self.calls = calls
        self._ledger = ledger
        self._run_id = run_id
        self._slots: dict[int, int] = {}  # call seq -> ledger index

    def _settle(self, entry: dict[str, Any]) -> None:
        if self._ledger is not None and entry["seq"] in self._slots:
            self._ledger.settle(self._slots[entry["seq"]], entry)

    def _start(self, operation: str, model: str, system: str, prompt: str) -> dict[str, Any]:
        # The ledger is written before the request: a refused reservation sends nothing.
        slot = self._ledger.reserve(self._run_id, operation, model) if self._ledger else None
        entry: dict[str, Any] = {
            "seq": len(self.calls) + 1,
            "operation": operation,
            "model_requested": model,
            "started_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "system_sha256": _sha256(system),
            "prompt_sha256": _sha256(prompt),
            "prompt": prompt,
        }
        self.calls.append(entry)
        if slot is not None:
            self._slots[entry["seq"]] = slot
        return entry

    @staticmethod
    def _fail(entry: dict[str, Any], exc: Exception, t0: float) -> None:
        entry["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        entry["error"] = {
            "code": getattr(exc, "code", type(exc).__name__),
            "message": getattr(exc, "message", None) or type(exc).__name__,
            "diagnostics": getattr(exc, "diagnostics", None),
        }

    async def generate_json(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: type[Any],
        video: VideoClip | None = None,
    ) -> LLMResult:
        entry = self._start(schema.__name__, model, system, prompt)
        if video is not None:
            entry["video"] = dataclasses.asdict(video)
        t0 = time.perf_counter()
        try:
            res = await self._inner.generate_json(
                model=model, system=system, prompt=prompt, schema=schema, video=video
            )
        except Exception as exc:
            self._fail(entry, exc, t0)
            self._settle(entry)
            raise
        entry.update(
            latency_ms=int((time.perf_counter() - t0) * 1000),
            model_served=res.model,
            usage=dataclasses.asdict(res.usage),
            raw_output=res.text,
        )
        self._settle(entry)
        return res

    async def grounded_search(self, *, model: str, system: str, prompt: str) -> GroundedResult:
        entry = self._start("grounded_search", model, system, prompt)
        t0 = time.perf_counter()
        try:
            res = await self._inner.grounded_search(model=model, system=system, prompt=prompt)
        except Exception as exc:
            self._fail(entry, exc, t0)
            self._settle(entry)
            raise
        entry.update(
            latency_ms=int((time.perf_counter() - t0) * 1000),
            model_served=res.model,
            usage=dataclasses.asdict(res.usage),
            raw_output=res.text,
            grounding={
                "queries": list(res.queries),
                "sources": [dataclasses.asdict(src) for src in res.sources],
                "supports": [dataclasses.asdict(sup) for sup in res.supports],
                "raw_metadata_present": res.raw_metadata_present,
            },
        )
        self._settle(entry)
        return res


def calls_summary(calls: list[dict[str, Any]]) -> dict[str, Any]:
    tokens = [c.get("usage") or {} for c in calls]
    return {
        "model_calls": len(calls),
        "failed_calls": sum(1 for c in calls if "error" in c),
        "operations": [c["operation"] for c in calls],
        "prompt_tokens": sum(t.get("prompt_tokens") or 0 for t in tokens),
        "output_tokens": sum(t.get("output_tokens") or 0 for t in tokens),
        "total_tokens": sum(t.get("total_tokens") or 0 for t in tokens),
        "model_latency_ms": sum(c.get("latency_ms") or 0 for c in calls),
    }


def git_state() -> dict[str, Any]:
    def git(*a: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", *a], cwd=REPO, capture_output=True, text=True, timeout=10, check=True
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip()

    status = git("status", "--porcelain", "--untracked-files=no")
    return {
        "commit": git("rev-parse", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": None if status is None else bool(status),
    }


def load_input(args: argparse.Namespace) -> dict[str, Any]:
    if args.from_run:
        data = json.loads(Path(args.from_run).read_text(encoding="utf-8"))
        if "input_method" in data:  # a committed fact-check artifact: reuse its input as is
            keys = ("id", "input_method", "claim", "context", "language", "video", "extraction",
                    "statement_date", "video_published_date")  # fmt: skip
            return {k: data.get(k) for k in keys}
        row = next(r for r in data["results"] if r["case"]["id"] == args.case)
        ext = row.get("extraction") or {}
        if ext.get("status") != "success" or not ext.get("claim"):
            raise SystemExit(f"{args.case}: the saved run has no successful extraction")
        case = row["case"]
        return {
            "id": case["id"],
            "input_method": "video_extraction",
            "claim": ext["claim"],
            "context": ext.get("context"),
            "language": case.get("language", "auto"),
            "video": {
                "url": case["youtube_url"],
                "timestamp_sec": case["timestamp_sec"],
                "window": ext.get("window"),
            },
            "extraction": {
                "run": str(Path(args.from_run).parent.name),
                "model": ext.get("model"),
                "claim_type": ext.get("claim_type"),
                "evidence_from_video": ext.get("evidence_from_video"),
                "is_exact_quote_model_reported": ext.get("is_exact_quote"),
                "quote_verified": False,  # nothing here checks the words against the video
                "speaker_verified": False,
                "latency_ms": ext.get("latency_ms"),
                "usage": ext.get("usage"),
            },
            # Only what the clip itself showed; the upload date is never a statement date.
            "statement_date": ext.get("statement_date"),
            "video_published_date": None,
        }
    if getattr(args, "proposition", None):
        case, _, pid = args.proposition.partition(":")
        path = REFERENCE_DIR / f"{case}.json"
        if not pid or not path.exists():
            raise SystemExit(
                f"--proposition must be CASE:Pn with eval/reference/CASE.json ({path})"
            )
        ref = ReferenceRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
        prop = ref.proposition(pid)
        return {
            "id": args.id or f"{case}-{pid.lower()}",
            # The claim is the reference proposition, word for word: not user- or video-sourced.
            "input_method": "reference_proposition",
            "proposition": {
                "case": case,
                "id": pid,
                "kind": prop.kind,
                "reference_review_status": ref.review.status,
            },
            "claim": prop.text,
            "context": ref.context,
            "language": args.language,
            "video": None,
            "extraction": None,
            "statement_date": getattr(args, "statement_date", None),
            "video_published_date": getattr(args, "video_published_date", None),
        }
    if not args.claim:
        raise SystemExit("give --proposition, --from-run/--case or --claim")
    return {
        "id": args.id or "manual",
        "input_method": "manual",
        "claim": args.claim,
        "context": args.context,
        "language": args.language,
        "video": None,
        "extraction": None,
        "statement_date": getattr(args, "statement_date", None),
        "video_published_date": getattr(args, "video_published_date", None),
    }


def artifact(
    item: dict[str, Any],
    resp: VerifyResponse | None,
    error: AppError | None,
    latency_ms: int,
    meta: dict[str, Any],
    calls: list[dict[str, Any]] | None = None,
    reference: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {**item, "run_meta": meta, "latency_ms": latency_ms}
    if calls is not None:
        out["model_calls"] = calls
        out["model_calls_summary"] = calls_summary(calls)
    if error is not None:
        out["verification"] = {
            "error": error.code,
            "message": error.message,
            "diagnostics": error.diagnostics,
        }
    elif resp is not None:
        out["verification"] = resp.model_dump()
    verdict = resp.verdict if resp is not None else None
    out["reference_score"] = reference_score(reference or item["id"], verdict)
    out["review"] = {
        "status": "unreviewed",
        "reviewer": None,
        "quote_checked_against_video_or_transcript": None,
        "speaker_checked": None,
        "sources_opened": None,
        "verdict_agrees": None,
        "entailment_judgments_agree": None,
        "provenance_complete": None,
        "notes": None,
    }
    return out


REVIEW_CHECKLIST = (
    "Run: `live` is true, the commit is clean (not UNCOMMITTED CHANGES) and is the one under "
    "test, the call count matches the plan, and no call shows an error you did not expect.",
    "Claim: the claim checked is the one intended, word for word; the dates given are right.",
    "Sources: open every URL. The page exists, is the publisher shown, and the excerpt is on the "
    "page (for `page_text`: verbatim; for `grounding_summary` / `search_snippet`: the page says it).",
    "Provenance: each source's publication date matches the page; nothing cited was published "
    "after the claim if the verdict depends on it being on record then.",
    "Speaker: for a claim about what someone said, the words relied on are that person's own "
    "(not a host, reporter or summary), and they are in a source you opened.",
    "Speaker's-words check: for each claim part, do you agree with the model's entails / "
    "contradicts / neutral per passage? Mark disagreements in the table.",
    "Stances: each source's stance (supports / contradicts / ...) is right for that source alone.",
    "Verdict: justified by the sources you checked, at the date the claim was made; the "
    "explanation cites only what the sources say; any safeguard that fired was right to fire.",
    "Reference: compare with the reference record (AI-reviewed, not ground truth); disagreements "
    "go in notes with the reason.",
    "Fill in the review record below and set status to human_verified only if every item passed.",
)


def render_markdown(a: dict[str, Any]) -> str:
    v = a.get("verification") or {}
    ext = a.get("extraction") or {}
    lines = [
        f"# Fact-check review sheet: {a['id']}",
        "",
        "Everything below is **model output or retrieved material** unless a review field says "
        "otherwise. Fill in the review section by hand.",
        "",
        "## Input",
        f"- Input method: **{a['input_method']}**",
    ]
    if a.get("video"):
        lines.append(
            f"- Video: {a['video']['url']} at {a['video']['timestamp_sec']} s "
            f"(window {a['video'].get('window')})"
        )
    lines += [f"- Claim checked: {a['claim']}", f"- Context: {a.get('context')}"]
    if a.get("statement_date") or a.get("video_published_date"):
        lines.append(
            f"- Dates given: said {a.get('statement_date') or 'unknown'}; "
            f"video published {a.get('video_published_date') or 'n/a'}"
        )
    meta = a.get("run_meta") or {}
    git = meta.get("git") or {}
    if meta:
        lines += [
            "",
            "## Run",
            f"- Run at {meta.get('run_at')} · live: {meta.get('live')} · commit "
            f"`{git.get('commit')}`{' (UNCOMMITTED CHANGES)' if git.get('dirty') else ''}",
            f"- Policy `{meta.get('verification_policy_version')}` · model "
            f"`{meta.get('verification_model')}` · fallback `{meta.get('fallback_model')}` · "
            f"retrieval `{meta.get('retrieval_backend')}` · retries {meta.get('max_retries')} · "
            f"call limit {meta.get('daily_call_limit')}",
        ]
    summary = a.get("model_calls_summary")
    if summary:
        lines.append(
            f"- Model calls: {summary['model_calls']} ({', '.join(summary['operations'])}); "
            f"failed {summary['failed_calls']}; tokens {summary['total_tokens']} "
            f"(in {summary['prompt_tokens']}, out {summary['output_tokens']}); model time "
            f"{summary['model_latency_ms']} ms; end-to-end {a.get('latency_ms')} ms"
        )
    if ext:
        lines += [
            f"- Heard in video (model transcription; exact-quote flag "
            f"{ext.get('is_exact_quote_model_reported')} is **model-reported, unverified**): "
            f'"{ext.get("evidence_from_video")}"',
            f"- Extraction model: `{ext.get('model')}` · {ext.get('latency_ms')} ms · "
            f"usage {ext.get('usage')}",
        ]
    lines += ["", "## Result"]
    if "error" in v:
        diag = dict(v.get("diagnostics") or {})
        done = diag.pop("completed", None)
        lines += [
            f"**No verdict: {v['error']}** ({v.get('message')})",
            "",
            f"Diagnostics: `{json.dumps(diag, ensure_ascii=False)}`",
        ]
        if done:
            lines += [
                "",
                "Completed before the failure (model output, unreviewed):",
                f"```json\n{json.dumps(done, indent=2, ensure_ascii=False)}\n```",
            ]
    else:
        lines += [
            f"- Verdict: **{v.get('verdict')}** · claim type: {v.get('claim_type')}",
            f"- Normalized claim: {v.get('normalized_claim')}",
            f"- Explanation (model): {v.get('explanation')}",
            f"- Search queries executed: {v.get('search_queries')}",
            f"- Independent origins: {v.get('independent_source_count')}",
            f"- Guardrail notes: {v.get('guardrail_notes')}",
            f"- Limitations (model): {v.get('limitations')}",
            f"- Models: {v.get('models_used')} · verification latency {v.get('latency_ms')} ms · "
            f"usage {v.get('usage')}",
            "",
            "### Key findings (model)",
        ]
        lines += [
            f"- [{f['stance']}] {f['text']} ({', '.join(f['source_ids'])})"
            for f in v.get("key_findings") or []
        ] or ["- (none)"]
        lines += ["", "### Safeguards that changed the verdict"]
        lines += [
            f"- `{adj['rule']}`: {adj['from_verdict']} -> {adj['to_verdict']}. {adj['reason']}"
            for adj in v.get("verdict_adjustments") or []
        ] or ["- (none: the model's verdict stood)"]
        ac = v.get("attribution_check")
        if ac:
            lines += [
                "",
                f"### Speaker's-words check (model judgments): {ac['status']}"
                + (f" · failure `{ac['failure']}`" if ac.get("failure") else ""),
                "",
                "| passage | source | named speaker? (model) | text |",
                "|---|---|---|---|",
            ]
            lines += [
                f"| {p['id']} | {p['source_id']} | {p['speaker_is_named']} | "
                f"{p['text'].replace('|', '/')} |"
                for p in ac["passages"]
            ]
            ids = [p["id"] for p in ac["passages"]]
            lines += ["", "| claim part (model-written) | " + " | ".join(ids) + " | agree? |"]
            lines += ["|---|" + "---|" * len(ids) + "---|"]
            lines += [
                f"| {part['text'].replace('|', '/')} | "
                + " | ".join(part["relations"].get(i, "-") for i in ids)
                + " | |"
                for part in ac["parts"]
            ]
        lines += [
            "",
            "### Sources",
            "",
            "| id | stance (model) | domain | published | excerpt origin | url | excerpt | opened? | "
            "excerpt on page? | supports the stated stance? |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for s in v.get("sources") or []:
            ex = " / ".join(s.get("excerpts") or []).replace("|", "\\|")
            lines.append(
                f"| {s['id']} | {s.get('stance')} | {s.get('domain')} | "
                f"{s.get('published_date') or 'unknown'} | {s.get('excerpt_origin')} | "
                f"{s['url']} | {ex} | | | |"
            )
        if v.get("sources"):
            lines.append(f"\nRetrieved at {v['sources'][0].get('retrieved_at')}.")
    rs = a.get("reference_score")
    if rs:
        counted = "counted" if rs["counted"] else "PROVISIONAL: reference is not human-verified"
        lines += [
            "",
            "## Against the reference record",
            f"- Reference verdict: {rs['reference_verdict']} (acceptable: {rs['acceptable_verdicts']}; "
            f"review status {rs['reference_review_status']})",
            f"- Outcome: {rs['outcome']} · false confident: {rs.get('false_confident')} · {counted}",
        ]
    calls = a.get("model_calls") or []
    if calls:
        lines += [
            "",
            "## Model calls (raw prompts and outputs are in the .json)",
            "",
            "| # | operation | model served | latency ms | tokens in/out | error |",
            "|---|---|---|---|---|---|",
        ]
        for c in calls:
            u = c.get("usage") or {}
            err = c.get("error") or {}
            lines.append(
                f"| {c['seq']} | {c['operation']} | {c.get('model_served', '-')} | "
                f"{c.get('latency_ms')} | {u.get('prompt_tokens', '-')}/{u.get('output_tokens', '-')}"
                f" | {err.get('code', '')} |"
            )
    lines += ["", "## Review checklist (by hand; the result counts only after this)", ""]
    lines += [f"- [ ] {item}" for item in REVIEW_CHECKLIST]
    lines += ["", "## Review record", ""]
    lines += [f"- {k}: " for k in a["review"]]
    return "\n".join(lines) + "\n"


async def run(
    item: dict[str, Any],
    settings: Settings,
    llm: LLMClient | None = None,
    calls: list[dict[str, Any]] | None = None,
    ledger: CallLedger | None = None,
) -> tuple[VerifyResponse | None, AppError | None, int]:
    if ledger is not None:
        problems = ledger_preconditions(settings, ledger)
        if problems:
            raise SystemExit("not started: " + "; ".join(problems))
        # Never more calls in this run than the experiment has left (and at most 4).
        settings = settings.model_copy(
            update={"gemini_daily_call_limit": min(MAX_CALLS_PER_RUN, ledger.remaining_calls())}
        )
    if llm is None:
        key = settings.gemini_api_key
        if not key:
            raise SystemExit("GEMINI_API_KEY is not set")
        try:
            llm = GeminiClient(
                key.get_secret_value(),
                timeout_sec=settings.request_timeout_sec,
                max_retries=settings.max_retries,
                fallback_model=settings.fallback_model,
                daily_call_limit=settings.gemini_daily_call_limit,
            )
        except AppError as exc:  # e.g. the exposed key: refused before any request
            raise SystemExit(exc.message) from None
    if calls is not None:
        llm = RecordingLLM(llm, calls, ledger, item["id"])
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as http:
        retriever = (
            build_search_retriever(settings, http)
            if settings.retrieval_backend == "search"
            else None
        )
        svc = VerificationService(llm, settings, http, retriever=retriever)
        started = time.perf_counter()
        try:
            resp = await svc.verify(
                VerifyRequest(
                    claim=item["claim"],
                    context=item.get("context"),
                    language=item.get("language", "auto"),
                    statement_date=item.get("statement_date"),
                    video_published_date=item.get("video_published_date"),
                )
            )
            return resp, None, int((time.perf_counter() - started) * 1000)
        except AppError as exc:
            return None, exc, int((time.perf_counter() - started) * 1000)


def ledger_preconditions(settings: Settings, ledger: CallLedger) -> list[str]:
    """Why a budgeted run must not start (empty: it may). Retries and fallback calls would be
    requests the ledger cannot see, and an unknown price makes the cost budget meaningless."""
    problems = []
    if settings.max_retries != 0:
        problems.append("YOUVERIFY_MAX_RETRIES must be 0 (retries are invisible to the ledger)")
    if settings.fallback_model:
        problems.append("YOUVERIFY_FALLBACK_MODEL must be unset (fallback calls are invisible)")
    if None in (ledger.prices.get("input_per_M"), ledger.prices.get("output_per_M")):
        problems.append("give --price-in and --price-out so the cost budget can be enforced")
    if (
        settings.retrieval_backend == "gemini_grounding"
        and ledger.prices.get("search_per_1k") is None
    ):
        problems.append("give --price-search-1k: grounding queries are billed")
    if ledger.remaining_calls() < MAX_CALLS_PER_RUN:
        problems.append(
            f"only {ledger.remaining_calls()} calls left in the experiment budget; a run may "
            f"need {MAX_CALLS_PER_RUN}"
        )
    if ledger.spent_usd() >= ledger.budget_usd:
        problems.append("the experiment cost budget is used up")
    return problems


def run_plan(
    item: dict[str, Any],
    settings: Settings,
    reference: str | None,
    ledger: CallLedger | None = None,
) -> dict[str, Any]:
    """What a run would do, without any call: check it before spending quota."""
    limit, retries = settings.gemini_daily_call_limit, settings.max_retries
    warnings = []
    if not settings.gemini_api_key:
        warnings.append("GEMINI_API_KEY is not set")
    elif key_fingerprint(settings.gemini_api_key.get_secret_value()) in REVOKED_KEY_FINGERPRINTS:
        warnings.append("GEMINI_API_KEY is the exposed key: it is refused; create a new one")
    if retries != 0:
        warnings.append("YOUVERIFY_MAX_RETRIES is not 0: calls are not exactly bounded")
    if ledger is None and (limit is None or limit > 4):
        warnings.append("YOUVERIFY_GEMINI_DAILY_CALL_LIMIT should be 4 (per run, per process)")
    if ledger is not None:
        warnings += [p for p in ledger_preconditions(settings, ledger) if "RETRIES" not in p]
    if settings.fallback_model:
        warnings.append("a fallback model is set: an overloaded call may be retried on it")
    git = git_state()
    if git.get("dirty"):
        warnings.append("uncommitted changes: the run would not be reproducible from a commit")
    return {
        "claim": item["claim"],
        "context": item.get("context"),
        "statement_date": item.get("statement_date"),
        "video_published_date": item.get("video_published_date"),
        "reference": reference,
        "model": settings.verification_model,
        "retrieval_backend": settings.retrieval_backend,
        "calls": "analysis, retrieval, evaluation (3); plus the speaker's-words check (4) when "
        "the claim is about what someone said and reaches a confident verdict",
        "daily_call_limit_per_run": limit,
        "max_retries": retries,
        "gemini_key_set": bool(settings.gemini_api_key),  # never the key or its fingerprint here
        "git": git,
        "verification_policy_version": VERIFICATION_POLICY_VERSION,
        "input_method": item.get("input_method"),
        "experiment_budget": ledger.status() if ledger else None,
        "warnings": warnings,
    }


def summarize(out_dir: Path) -> dict[str, Any]:
    """Measured figures over every run artifact in `out_dir`; counts, never averages alone."""
    runs = []
    for path in sorted(out_dir.glob("*.json")):
        if path.name in ("call-ledger.json", "metrics.json") or path.name.startswith("plan-"):
            continue
        a = json.loads(path.read_text(encoding="utf-8"))
        if "run_meta" not in a:
            continue
        v = a.get("verification") or {}
        calls = a.get("model_calls") or []
        runs.append(
            {
                "file": path.name,
                "id": a["id"],
                "input_method": a.get("input_method"),
                "live": (a.get("run_meta") or {}).get("live"),
                "commit": ((a.get("run_meta") or {}).get("git") or {}).get("commit"),
                "outcome": v.get("error") or v.get("verdict"),
                "safeguards": [x["rule"] for x in v.get("verdict_adjustments") or []],
                "speakers_words_check": (v.get("attribution_check") or {}).get("status"),
                "sources": len(v.get("sources") or []),
                "end_to_end_ms": a.get("latency_ms"),
                "model_calls": a.get("model_calls_summary"),
                "per_call_ms": {c["operation"]: c.get("latency_ms") for c in calls},
                "estimated_cost_usd": (v.get("usage") or {}).get("estimated_cost_usd"),
                "reference_outcome": (a.get("reference_score") or {}).get("outcome"),
                "review_status": (a.get("review") or {}).get("status"),
            }
        )
    ledger_path = out_dir / "call-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else None
    entries = (ledger or {}).get("entries") or []
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "runs": runs,
        "n_runs": len(runs),
        "ledger": {
            "calls": len(entries),
            "failed_or_unfinished": sum(1 for e in entries if e.get("status") != "ok"),
            "estimated_spend_usd": round(sum(e.get("est_cost_usd") or 0 for e in entries), 6),
            "calls_with_unknown_cost": sum(1 for e in entries if e.get("est_cost_usd") is None),
        },
    }


def write_outputs(a: dict[str, Any], key: str, out_dir: Path) -> str:
    """Write the artifact and its review sheet; never a file that contains the API key."""
    text, sheet = json.dumps(a, indent=2, ensure_ascii=False), render_markdown(a)
    if key and (key in text or key in sheet):
        raise SystemExit("refusing to write: the artifact would contain the API key")
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{a['id']}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    (out_dir / f"{stem}.json").write_text(text, encoding="utf-8")
    (out_dir / f"{stem}.md").write_text(sheet, encoding="utf-8")
    return stem


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--from-run",
        help="results.json of a live_validation run, or an eval/factchecks/*.json artifact",
    )
    ap.add_argument("--case", help="case id inside --from-run")
    ap.add_argument("--claim", help="manual claim text")
    ap.add_argument("--context", help="manual context (speaker, date, event)")
    ap.add_argument("--language", default="auto", choices=["auto", "en", "ru"])
    ap.add_argument("--id", help="id for a manual claim")
    ap.add_argument("--statement-date", help="when it was said, if known: YYYY[-MM[-DD]]")
    ap.add_argument("--video-published-date", help="upload date YYYY-MM-DD (an upper bound)")
    ap.add_argument(
        "--plan",
        action="store_true",
        help="print what would run (claim, settings, call budget, commit) and make no calls",
    )
    ap.add_argument(
        "--reference",
        help="score against eval/reference/<case>.json, or one proposition as CASE:P2",
    )
    ap.add_argument(
        "--proposition",
        help="check a reference proposition word for word, as CASE:Pn (scored against it)",
    )
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR, help="where artifacts go (private)")
    ap.add_argument(
        "--budget-calls", type=int, default=8, help="model calls for the whole experiment"
    )
    ap.add_argument(
        "--budget-usd", type=float, default=1.0, help="estimated spend for the whole experiment"
    )
    ap.add_argument(
        "--summarize", action="store_true", help="write metrics.json for --out-dir; no calls"
    )
    ap.add_argument("--price-in", type=float)
    ap.add_argument("--price-out", type=float)
    ap.add_argument("--price-search-1k", type=float)
    args = ap.parse_args()

    out_dir: Path = args.out_dir
    if args.summarize:
        metrics = summarize(out_dir)
        (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        print(json.dumps(metrics, indent=2))
        return 0
    item = load_input(args)
    reference = args.reference or (args.proposition if args.proposition else None)
    updates: dict[str, Any] = {"cache_ttl_sec": 0}
    for flag, field in (
        ("price_in", "price_input_per_million"),
        ("price_out", "price_output_per_million"),
        ("price_search_1k", "price_per_1k_search_queries"),
    ):
        if getattr(args, flag) is not None:
            updates[field] = getattr(args, flag)
    settings = get_settings().model_copy(update=updates)
    configure_logging("WARNING")
    ledger = CallLedger(
        out_dir / "call-ledger.json",
        args.budget_calls,
        args.budget_usd,
        {
            "input_per_M": settings.price_input_per_million,
            "output_per_M": settings.price_output_per_million,
            "search_per_1k": settings.price_per_1k_search_queries,
        },
    )
    if args.plan:
        plan = run_plan(item, settings, reference, ledger)
        # ASCII-escaped: a Windows console or redirect may not use UTF-8.
        print(json.dumps(plan, indent=2))
        return 0

    calls: list[dict[str, Any]] = []
    resp, error, latency = asyncio.run(run(item, settings, calls=calls, ledger=ledger))
    meta = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "live": True,  # a real model client; tests pass their own scripted client to run()
        "git": git_state(),
        "app_version": app_version,
        "verification_policy_version": VERIFICATION_POLICY_VERSION,
        "extraction_prompt_version": EXTRACTION_PROMPT_VERSION,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "verification_model": settings.verification_model,
        "fallback_model": settings.fallback_model,
        "retrieval_backend": settings.retrieval_backend,
        "resolve_source_redirects": settings.resolve_source_redirects,
        "max_retries": settings.max_retries,
        "daily_call_limit": settings.gemini_daily_call_limit,
        "request_timeout_sec": settings.request_timeout_sec,
        "prices": {
            "input_per_M": settings.price_input_per_million,
            "output_per_M": settings.price_output_per_million,
            "search_per_1k": settings.price_per_1k_search_queries,
        },
    }
    meta["experiment_budget_after_run"] = ledger.status()
    a = artifact(item, resp, error, latency, meta, calls, reference)
    key = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else ""
    stem = write_outputs(a, key, out_dir)
    print(f"wrote {out_dir}/{stem}.json and .md", file=sys.stderr)
    return 0 if error is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
