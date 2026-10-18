# Contributing to YouVerify AI

Thanks for helping. This is a research prototype maintained by a small team; please read the
[project status](README.md#project-status-what-is-real) first, so a change does not claim more
than the evidence supports.

## Who owns what

| Area | Owner role |
|---|---|
| LLM integration, claim extraction, retrieval, evidence verification, evaluation, application logic | AI/ML engineer |
| Docker, CI/CD, deployment, HTTPS, secrets, monitoring, shared rate limits and budgets | DevOps engineer ([handoff](docs/quality/devops-handoff.md)) |

## Development environment

Python 3.11+, Node.js 22.12+, Chrome 116+. Setup: [README](README.md#installation) and
[development guide](docs/development.md). No API key is needed for the tests or the demo backend.

## Branches and pull requests

- Branch from `main`: `feat/<topic>`, `fix/<topic>`, `docs/<topic>`, `ops/<topic>`.
- One logical change per pull request; describe what changed, how it was tested, and what was
  **not** tested (mocked vs live).
- Every pull request needs green CI and one review before merge. Never force-push `main`.
- Do not commit `.env`, keys, model outputs you have not reviewed, or private data.

## Checks (the same as CI)

```bash
# backend/
pytest -q
ruff check . ../scripts && ruff format --check . ../scripts
mypy app
python ../scripts/scan_secrets.py --staged
python ../scripts/check_doc_links.py

# extension/
npm test && npm run typecheck && npm run build && npm run check:remote-code
npm run e2e   # needs a backend on :8000, e.g. python -m tests.e2e_server
```

## Code and evidence standards

- **Bug fixes come with a regression test that fails before the fix.**
- Model output is untrusted: validate it with a schema and post-check it; never let the model
  introduce a URL or a citation.
- A change to prompts or verdict safeguards bumps `VERIFICATION_POLICY_VERSION` and says how it
  was evaluated. Prompt changes without a live evaluation are labelled as such.
- Never report accuracy without human-reviewed labels ([evaluation protocol](docs/evaluation.md)).
  Keep mocked results, AI-reviewed references and live runs clearly apart.
- Screenshots and examples with fake data must say so visibly.

## Security

Report vulnerabilities privately: see [SECURITY.md](SECURITY.md).
