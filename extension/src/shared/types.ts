// Mirrors backend/app/models/schemas.py. Keep in sync when the API changes.

export type ClaimType = "factual" | "prediction" | "opinion" | "ambiguous" | "non_verifiable";
export type Verdict = "supported" | "contradicted" | "mixed" | "insufficient_evidence" | "not_verifiable";
export type Stance = "supports" | "contradicts" | "partially_supports" | "unclear" | "irrelevant";
export type Language = "auto" | "en" | "ru";
export type VerifyStage = "analyzing" | "searching" | "evaluating";

export interface TokenUsage {
  prompt_tokens: number;
  output_tokens: number;
  total_tokens: number;
  video_tokens: number | null;
  audio_tokens: number | null;
  search_queries: number;
  estimated_cost_usd: number | null;
}

export interface ExtractRequest {
  video_id: string;
  timestamp_sec: number;
  duration_sec?: number | null;
  language: Language;
}

export interface ExtractResponse {
  status: "success" | "no_claim_found";
  video_id: string;
  timestamp_sec: number;
  window: { start_sec: number; end_sec: number };
  claim: string | null;
  claim_type: ClaimType | null;
  context: string | null;
  evidence_from_video: string | null;
  is_exact_quote: boolean;
  requires_user_confirmation: true;
  language: string | null;
  reason: string | null;
  model: string;
  latency_ms: number;
  cached: boolean;
  usage: TokenUsage;
}

export interface VerifyRequest {
  claim: string;
  context?: string | null;
  claim_type_hint?: ClaimType | null;
  video_id?: string | null;
  timestamp_sec?: number | null;
  statement_date?: string | null;
  language: Language;
}

export type ExcerptOrigin = "grounding_summary" | "page_text" | "search_snippet";

export interface SourceRef {
  id: string;
  title: string | null;
  url: string;
  domain: string | null;
  publisher: string | null;
  published_date: string | null;
  retrieved_at: string;
  excerpts: string[];
  /** Absent from older backends, which always returned grounding summaries. */
  excerpt_origin?: ExcerptOrigin;
  stance: Stance | null;
  assessment_note: string | null;
}

export interface KeyFinding {
  text: string;
  stance: Stance;
  source_ids: string[];
}

/** A deterministic safeguard that changed the model's verdict (unlike a guardrail note, a warning). */
export interface VerdictAdjustment {
  rule:
    | "verdict_without_backing_source"
    | "attribution_without_direct_evidence"
    | "exact_quote_not_confirmed"
    | "verdict_contradicts_own_findings"
    | "attribution_not_entailed";
  from_verdict: Verdict;
  to_verdict: Verdict;
  reason: string;
}

export interface VerifyResponse {
  claim: string;
  normalized_claim: string;
  claim_type: ClaimType;
  verdict: Verdict;
  explanation: string;
  key_findings: KeyFinding[];
  sources: SourceRef[];
  limitations: string[];
  search_queries: string[];
  independent_source_count: number;
  guardrail_notes: string[];
  /** Absent from older backends. */
  verdict_adjustments?: VerdictAdjustment[];
  /** Model that served each step (analysis, retrieval, evaluation); absent from older backends. */
  models_used?: Record<string, string>;
  model: string;
  latency_ms: number;
  cached: boolean;
  usage: TokenUsage;
}

export interface ErrorBody {
  code: string;
  message: string;
  retryable: boolean;
}

export interface HealthResponse {
  status: "ok";
  version: string;
  gemini_configured: boolean;
  extraction_model: string;
  verification_model: string;
  /** Absent from older backends. */
  retrieval_backend?: "gemini_grounding" | "search";
}

/** What the content script reports about the current tab. */
export type VideoContext =
  | {
      status: "ok";
      videoId: string;
      timestampSec: number;
      durationSec: number | null;
      title: string | null;
      /** Upload date (YYYY-MM-DD), only when the page metadata is for this video. */
      publishedDate: string | null;
    }
  | {
      status: "unsupported";
      reason: UnsupportedReason;
    };

export type UnsupportedReason =
  | "not_youtube"
  | "not_watch_page"
  | "shorts"
  | "no_video"
  | "ad_playing"
  | "live"
  | "content_script_unavailable";
