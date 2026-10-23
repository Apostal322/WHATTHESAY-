#!/usr/bin/env python3
"""Show what each annotation case still needs, and export the human-verified ones.

    python scripts/annotation_status.py                       # per-case checklist
    python scripts/annotation_status.py --export eval/dataset.human.jsonl

Only cases a person marked human_verified (with reviewer and date) are exported as ground
truth for scripts/run_eval.py. Machine proposals are never exported. See docs/annotation-guide.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.evaluation.annotation import AnnotationCase

DEFAULT = REPO / "eval" / "annotation" / "cases.jsonl"


def load(path: Path) -> list[AnnotationCase]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [AnnotationCase.model_validate(json.loads(line)) for line in lines if line.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("cases", nargs="?", type=Path, default=DEFAULT)
    ap.add_argument("--export", type=Path, help="write human-verified cases as an eval dataset")
    args = ap.parse_args()
    cases = load(args.cases)
    verified = [c for c in cases if c.human.status == "human_verified"]
    for c in cases:
        gaps = c.human.missing()
        state = c.human.status if not gaps or c.human.status != "unreviewed" else "to do"
        print(f"{c.id:18} {state:15} missing: {', '.join(gaps) or '-'}")
    print(f"\n{len(verified)} of {len(cases)} cases human-verified.")
    if args.export:
        items = [c.to_eval_item() for c in verified]
        args.export.write_text(
            "".join(i.model_dump_json() + "\n" for i in items if i is not None), encoding="utf-8"
        )
        print(f"wrote {len(verified)} items to {args.export}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
