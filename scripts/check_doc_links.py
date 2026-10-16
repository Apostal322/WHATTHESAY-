#!/usr/bin/env python3
"""Fail if a tracked Markdown file links to a missing file or a missing heading anchor.

    python scripts/check_doc_links.py

Checks relative links and `src=` image paths, and `#anchors` against GitHub-style heading
slugs. External URLs are not fetched. No dependencies; exit code 1 on any broken link.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_LINK = re.compile(r"\]\(([^)\s]+)\)|src=\"([^\"]+)\"")
_HEADING = re.compile(r"^#{1,6}\s+(.*)$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def slugs(path: Path) -> set[str]:
    out: set[str] = set()
    in_code = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if _FENCE.match(line):
            in_code = not in_code
            continue
        if not in_code and (m := _HEADING.match(line)):
            text = re.sub(r"[^\w\- ]", "", m.group(1).strip().lower())
            out.add(text.replace(" ", "-"))
    return out


def check(md: Path) -> list[str]:
    problems = []
    text = md.read_text(encoding="utf-8")
    for m in _LINK.finditer(text):
        target = m.group(1) or m.group(2)
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path_part, _, anchor = target.partition("#")
        dest = (md.parent / path_part).resolve() if path_part else md
        rel = md.relative_to(REPO)
        if not dest.exists():
            problems.append(f"{rel}: missing file {target}")
        elif anchor and dest.suffix == ".md" and anchor not in slugs(dest):
            problems.append(f"{rel}: missing anchor {target}")
    return problems


def main() -> int:
    files = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.split()
    problems = [p for f in files for p in check(REPO / f)]
    for p in problems:
        print(p, file=sys.stderr)
    print(f"doc links: {len(files)} files, {len(problems)} broken", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
