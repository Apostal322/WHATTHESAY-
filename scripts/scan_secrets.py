#!/usr/bin/env python3
"""Fail if credentials are about to be committed. Cross-platform; no dependencies.

    python scripts/scan_secrets.py            # every tracked file (also used in CI)
    python scripts/scan_secrets.py --staged   # only lines added in `git add`-ed changes

Detects Google API keys (AIza... and the newer AQ.... format), Google OAuth access tokens,
private-key blocks, non-empty secret assignments for this project's variables, and a tracked
`.env`. Findings are printed redacted; the secret itself is never echoed. A line that holds a
deliberately fake test value can opt out with the marker `secret-scan: allow`.
Exit code 1 if anything is found.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ALLOW_MARKER = "secret-scan: allow"
PATTERNS: dict[str, re.Pattern[str]] = {
    "google_api_key": re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    "google_auth_key": re.compile(r"\bAQ\.[0-9A-Za-z_\-]{20,}"),
    "google_oauth_token": re.compile(r"\bya29\.[0-9A-Za-z_\-.]{20,}"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "secret_assignment": re.compile(
        r"^\s*(?:\$env:)?(GEMINI_API_KEY|BRAVE_SEARCH_API_KEY)\s*=\s*[\"']?(?!Read-Host|\$|\(|\[)([^\s\"'#<]{8,})"
    ),
}


def redact(value: str) -> str:
    return value[:4] + "…" + f"[{len(value)} chars]"


def scan_text(path: str, lines: list[tuple[int, str]]) -> list[str]:
    findings = []
    for lineno, line in lines:
        if ALLOW_MARKER in line:
            continue
        for kind, pattern in PATTERNS.items():
            for match in pattern.finditer(line):
                value = match.group(match.lastindex or 0)
                findings.append(f"{path}:{lineno}: {kind} {redact(value)}")
    return findings


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def tracked_findings() -> list[str]:
    findings = []
    for rel in _git("ls-files", "-z").split("\0"):
        if not rel:
            continue
        if Path(rel).name == ".env":
            findings.append(f"{rel}: tracked .env file (must stay untracked)")
            continue
        try:
            text = (REPO / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue  # binary or deleted in the worktree
        findings += scan_text(rel, list(enumerate(text.splitlines(), start=1)))
    return findings


def staged_findings() -> list[str]:
    findings = []
    names = [n for n in _git("diff", "--cached", "--name-only", "-z").split("\0") if n]
    findings += [f"{n}: staged .env file" for n in names if Path(n).name == ".env"]
    current, lineno, added = "", 0, []
    for line in _git("diff", "--cached", "-U0", "--no-color").splitlines():
        if line.startswith("+++ "):
            findings += scan_text(current, added)
            current, added = line[6:] if line.startswith("+++ b/") else line[4:], []
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            lineno = int(m.group(1)) if m else 0
        elif line.startswith("+"):
            added.append((lineno, line[1:]))
            lineno += 1
    findings += scan_text(current, added)
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--staged", action="store_true", help="scan only staged additions")
    args = ap.parse_args()
    findings = staged_findings() if args.staged else tracked_findings()
    for f in findings:
        print(f, file=sys.stderr)
    scope = "staged changes" if args.staged else "tracked files"
    print(f"secret scan ({scope}): {len(findings)} finding(s)", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
