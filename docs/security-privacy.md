# Security and privacy

## Data flow: what leaves the user's machine

| Step | Sent | To |
|---|---|---|
| Extension → backend | YouTube video ID, playback timestamp, video duration, language preference. For verification: the (edited) claim text and the AI-extracted context. | Your backend (default `127.0.0.1:8000`) |
| Backend → Gemini (extraction) | Canonical public watch URL of the video, clip offsets, prompt | Google Gemini API; Google fetches the video itself |
| Backend → Gemini (verification) | Claim text, context, search queries | Google Gemini API with Google Search grounding |
| Backend → `vertexaisearch.cloud.google.com` | GET on grounding redirect links, **without following redirects**, to read the publisher URL | Google |
| Backend → search provider (only with `YOUVERIFY_RETRIEVAL_BACKEND=search`) | The search queries planned for the claim (derived from the claim text) | Your private SearXNG instance, which forwards them to its upstream engines (Google, Bing, DuckDuckGo…), and/or the Brave Search API |
| Backend → publisher sites (search mode only) | Plain GET of top-ranked result pages, no cookies, `YouVerifyBot` user agent | The sites in the search results |

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
| API key exposure | Keys (`GEMINI_API_KEY`, `BRAVE_SEARCH_API_KEY`) exist only in the backend environment (`.env` is git-ignored). The extension has no secrets. Keys travel only in request headers, never URLs. Provider error messages pass through `redact_secrets` before logging. |
| SSRF via user-supplied URLs | The backend never fetches user URLs. YouTube URLs are reduced to a validated 11-character ID and rebuilt as `https://www.youtube.com/watch?v=ID`; hosts, ports, credentials and schemes are checked. In grounding mode, the only other outbound fetch is to the fixed Google grounding-redirect host, and it never follows the redirect. In search mode the backend fetches result pages from third-party search results. `fetch_page` accepts only http(s) URLs to public hostnames. It resolves each host once, requires **every** address to be public, and connects to that exact address (DNS pinning; TLS is still verified for the hostname via SNI), which closes DNS rebinding. Every redirect hop (at most 3) is resolved, checked and pinned again. It requests uncompressed content, caps the raw body at 1.5 MB, decompresses with an output limit (no gzip bombs), rejects other encodings, accepts only text/HTML, and runs under an 8 s timeout. Deployment: also block private ranges at the network egress (defence in depth); if outbound traffic uses an HTTP proxy, the proxy resolves names itself and must enforce the same policy. The developer-only `scripts/live_validation.py --fetch-sources` page check does not pin DNS; run it only on trusted machines. |
| Prompt injection from video speech, user claims or retrieved pages | Untrusted text is wrapped in `<untrusted>` delimiters, and delimiter look-alikes are escaped. System prompts state that it is data. Output is schema-constrained and re-validated. Unknown source IDs and citations are dropped, and verdicts must be backed by per-source stances (deterministic guardrails). The model cannot introduce a URL: sources come only from grounding metadata or search results. Fetched page text is untrusted like everything else. |
| Fabricated or unsafe citations | URLs come only from grounding metadata or search results and must pass `is_safe_display_url` (http/https, public host, no credentials, no private IPs). In search mode, page excerpts must occur verbatim in the fetched page, and each source is labelled with its excerpt origin. The UI applies a second check and opens links with `rel="noopener noreferrer"`. |
| XSS in the side panel | React text rendering only; no `dangerouslySetInnerHTML`. Citations are parsed into elements, never HTML. Extension pages use the MV3 default CSP. |
| Abuse or cost blow-up | Paid search is refused unless a price and a daily USD cap are configured, and spend is reserved before each request (refunded on failure); an automatic paid fallback needs explicit opt-in. Per-IP rate limit (20/min default), 16 KB body limit, length limits on every field, bounded retries and timeouts, result caching. CORS is limited to `chrome-extension://` origins by default; **restrict it to your extension ID** via `YOUVERIFY_ALLOWED_ORIGIN_REGEX`. The server binds to `127.0.0.1`. |
| Over-broad extension permissions | `sidePanel` only, plus host access to `www.youtube.com` and the local backend. No `tabs`, `scripting`, `storage`, `<all_urls>` or `webRequest`. The content script is read-only and never controls playback. |

## Not in scope for the MVP

- Authentication. The backend is meant to run locally for one user. Before any
  public deployment, add auth (an API token at minimum), TLS, a shared rate-limit store and
  per-user quotas.
- Content Security Policy for the backend's own docs pages (`/docs`). They are
  FastAPI defaults and can be disabled in production.
