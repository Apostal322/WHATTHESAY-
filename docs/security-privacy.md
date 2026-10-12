# Security and privacy

## Data flow: what leaves the user's machine

| Step | Sent | To |
|---|---|---|
| Extension → backend | YouTube video ID, playback timestamp, video duration, language preference. For verification: the (edited) claim text and the AI-extracted context. | Your backend (default `127.0.0.1:8000`) |
| Backend → Gemini (extraction) | Canonical public watch URL of the video, clip offsets, prompt | Google Gemini API; Google fetches the video itself |
| Backend → Gemini (verification) | Claim text, context, search queries | Google Gemini API with Google Search grounding |
| Backend → `vertexaisearch.cloud.google.com` | GET on grounding redirect links, **without following redirects**, to read the publisher URL | Google |

**Not collected or sent:** audio recordings, browsing history, other tabs,
cookies, YouTube account data, page contents beyond the video ID, title and
playback position. The extension stores nothing persistently. The backend
keeps an in-memory cache (TTL 1 h by default) and writes structured logs with
no claim text and no secrets.

Review Google's Gemini API data-use terms for your tier. Free-tier inputs may
be used to improve Google's products.

## Threats and mitigations

| Threat | Mitigation |
|---|---|
| API key exposure | The key exists only in the backend `.env` (git-ignored). The extension has no secrets. Logs never include the key. |
| SSRF via user-supplied URLs | The backend never fetches user URLs. YouTube URLs are reduced to a validated 11-character ID and rebuilt as `https://www.youtube.com/watch?v=ID`; hosts, ports, credentials and schemes are checked. The only outbound fetch the backend makes is to the fixed Google grounding-redirect host, and it never follows the redirect. |
| Prompt injection from video speech, user claims or retrieved pages | Untrusted text is wrapped in `<untrusted>` delimiters, and delimiter look-alikes are escaped. System prompts state that it is data. Output is schema-constrained and re-validated. Unknown source IDs and citations are dropped, and verdicts must be backed by per-source stances (deterministic guardrails). The model cannot introduce a URL: sources come only from grounding metadata. |
| Fabricated or unsafe citations | URLs come only from grounding metadata and must pass `is_safe_display_url` (http/https, public host, no credentials, no private IPs). The UI applies a second check and opens links with `rel="noopener noreferrer"`. |
| XSS in the side panel | React text rendering only; no `dangerouslySetInnerHTML`. Citations are parsed into elements, never HTML. Extension pages use the MV3 default CSP. |
| Abuse or cost blow-up | Per-IP rate limit (20/min default), 16 KB body limit, length limits on every field, bounded retries and timeouts, result caching. CORS is limited to `chrome-extension://` origins by default; **restrict it to your extension ID** via `YOUVERIFY_ALLOWED_ORIGIN_REGEX`. The server binds to `127.0.0.1`. |
| Over-broad extension permissions | `sidePanel` only, plus host access to `www.youtube.com` and the local backend. No `tabs`, `scripting`, `storage`, `<all_urls>` or `webRequest`. The content script is read-only and never controls playback. |

## Not in scope for the MVP

- Authentication. The backend is meant to run locally for one user. Before any
  public deployment, add auth (an API token at minimum), TLS, a shared rate-limit store and
  per-user quotas.
- Content Security Policy for the backend's own docs pages (`/docs`). They are
  FastAPI defaults and can be disabled in production.
