# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's private
vulnerability reporting: **Security → Report a vulnerability** on this repository. Include the
affected component (backend, extension, scripts), steps to reproduce and the impact you expect.
You will get an acknowledgement as soon as a maintainer sees the report; this is a small
volunteer-run research project, so there is no guaranteed response time.

## Scope

In scope: the FastAPI backend, the Chrome extension, and the scripts in this repository, in
particular SSRF in evidence fetching, prompt injection that changes verdicts or leaks data,
API-key exposure, cost-amplification paths, and XSS in the side panel.

Out of scope: third-party services (Google Gemini, search providers, YouTube) and deployments
you operate yourself.

## Supported versions

Only the latest commit on `main` is supported. There are no releases yet.

## Handling secrets

- API keys belong only in the backend's `.env` (git-ignored) or a secret manager, never in the
  extension, logs, screenshots or commits. `scripts/scan_secrets.py` runs in CI.
- If a key is ever pasted into a chat, an issue or a commit, **rotate it**. Removing it from Git
  history does not make an exposed key safe again.

Design details and the threat model: [docs/security-privacy.md](docs/security-privacy.md).
