import type { ClaimType, Stance, UnsupportedReason, Verdict } from "./types";

export function formatTimestamp(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(sec).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** Only http(s) URLs to public-looking hosts are rendered as links. */
export function safeExternalUrl(raw: string): string | null {
  try {
    const url = new URL(raw);
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    if (url.username || url.password) return null;
    const host = url.hostname;
    if (!host.includes(".") || host === "localhost" || /^(127\.|10\.|192\.168\.|169\.254\.)/.test(host)) return null;
    return url.toString();
  } catch {
    return null;
  }
}

export type CitationSegment = { kind: "text"; text: string } | { kind: "cite"; id: string };

/** Split "text [S1] more" into text + citation segments, keeping only known ids. */
export function splitCitations(text: string, knownIds: ReadonlySet<string>): CitationSegment[] {
  const out: CitationSegment[] = [];
  const re = /\[(S\d{1,2})\]/g;
  let last = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    const id = m[1] ?? "";
    if (m.index > last) out.push({ kind: "text", text: text.slice(last, m.index) });
    if (knownIds.has(id)) out.push({ kind: "cite", id });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ kind: "text", text: text.slice(last) });
  return out;
}

export const VERDICT_LABEL: Record<Verdict, string> = {
  supported: "Supported",
  contradicted: "Contradicted",
  mixed: "Mixed evidence",
  insufficient_evidence: "Insufficient evidence",
  not_verifiable: "Not verifiable",
};

export const VERDICT_SUMMARY: Record<Verdict, string> = {
  supported: "Retrieved sources support this claim.",
  contradicted: "Retrieved sources contradict this claim.",
  mixed: "Sources support some parts and contradict others.",
  insufficient_evidence: "The evidence found does not settle this claim.",
  not_verifiable: "This is not an objectively checkable factual claim.",
};

export const CLAIM_TYPE_LABEL: Record<ClaimType, string> = {
  factual: "Factual",
  prediction: "Prediction",
  opinion: "Opinion",
  ambiguous: "Ambiguous",
  non_verifiable: "Not verifiable",
};

export const STANCE_LABEL: Record<Stance, string> = {
  supports: "Supports",
  contradicts: "Contradicts",
  partially_supports: "Partly supports",
  irrelevant: "Not relevant",
};

export const UNSUPPORTED_MESSAGE: Record<UnsupportedReason, string> = {
  not_youtube: "Open a YouTube video to verify a claim.",
  not_watch_page: "Open a standard YouTube video (youtube.com/watch) to verify a claim.",
  shorts: "YouTube Shorts are not supported yet. Open a standard video.",
  no_video: "Waiting for the video player…",
  ad_playing: "An ad is playing. Wait for the video to resume.",
  live: "Live streams are not supported yet.",
  content_script_unavailable: "Reload the YouTube tab so YouVerify can read the video.",
};

/** User-facing guidance for backend error codes. */
export function errorHint(code: string): string | null {
  switch (code) {
    case "missing_credentials":
      return "Add GEMINI_API_KEY to the .env file and restart the backend.";
    case "model_not_found":
      return "Set a valid model in .env (see scripts/list_models.py).";
    case "backend_unreachable":
      return "Start it with: uvicorn app.main:app --port 8000 (in backend/).";
    case "video_unavailable":
      return "You can still type the claim manually.";
    case "rate_limited":
    case "provider_rate_limited":
      return "Wait a minute and try again.";
    case "retrieval_unavailable":
      return "No evidence could be retrieved, so no verdict was given. This is not a judgement on the claim.";
    case "backend_misconfigured":
      return "The backend's search configuration is incomplete. The operator needs to fix it.";
    case "provider_quota_exhausted":
      return "The Gemini API key has used up its quota (e.g. the free-tier daily limit). Try again after it resets or use a key with a higher quota.";
    default:
      return null;
  }
}
