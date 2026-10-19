import type { ApiClient } from "../src/shared/api";
import type { ExtractResponse, VerifyResponse, VideoContext } from "../src/shared/types";
import type { VideoContextSource } from "../src/sidepanel/videoSource";

export const VIDEO_ID = "dQw4w9WgXcQ";

const usage = { prompt_tokens: 1, output_tokens: 1, total_tokens: 2, video_tokens: null, audio_tokens: null, search_queries: 0, estimated_cost_usd: null };

export function extraction(overrides: Partial<ExtractResponse> = {}): ExtractResponse {
  return {
    status: "success",
    video_id: VIDEO_ID,
    timestamp_sec: 755,
    window: { start_sec: 710, end_sec: 765 },
    claim: "Artificial intelligence has already replaced 80% of software engineers.",
    claim_type: "factual",
    context: "Discussing the tech job market.",
    evidence_from_video: "AI already replaced like 80 percent of software engineers",
    is_exact_quote: false,
    requires_user_confirmation: true,
    language: "en",
    reason: null,
    model: "fake",
    latency_ms: 1000,
    cached: false,
    usage,
    ...overrides,
  };
}

export function verification(overrides: Partial<VerifyResponse> = {}): VerifyResponse {
  return {
    claim: "x",
    normalized_claim: "AI has replaced 80% of software engineers.",
    claim_type: "factual",
    verdict: "contradicted",
    explanation: "Official statistics show developer employment growing [S1].",
    key_findings: [{ text: "Employment projected to grow.", stance: "contradicts", source_ids: ["S1"] }],
    sources: [
      {
        id: "S1",
        title: "Software Developers : Occupational Outlook Handbook",
        url: "https://www.bls.gov/ooh/computer-and-information-technology/software-developers.htm",
        domain: "bls.gov",
        publisher: null,
        published_date: null,
        retrieved_at: "2026-10-12T20:00:00+00:00",
        excerpts: ["Employment of software developers is projected to grow."],
        stance: "contradicts",
        assessment_note: "US data; contradicts the 80% figure.",
      },
      {
        id: "S2",
        title: "Bad link",
        url: "javascript:alert(1)",
        domain: null,
        publisher: null,
        published_date: null,
        retrieved_at: "2026-10-12T20:00:00+00:00",
        excerpts: ["<img src=x onerror=alert(1)>"],
        stance: "irrelevant",
        assessment_note: null,
      },
    ],
    limitations: ["US data only."],
    search_queries: ["software developer employment"],
    independent_source_count: 1,
    guardrail_notes: [],
    model: "fake",
    latency_ms: 4200,
    cached: false,
    usage,
    ...overrides,
  };
}

export function staticVideoSource(ctx: VideoContext): VideoContextSource & { set(next: VideoContext): void } {
  let current = ctx;
  return {
    get: () => Promise.resolve(current),
    subscribe: () => () => undefined,
    set(next) {
      current = next;
    },
  };
}

export function fakeApi(overrides: Partial<ApiClient> = {}): ApiClient {
  return {
    health: () => Promise.resolve({ status: "ok", version: "0.1.0", gemini_configured: true, extraction_model: "m", verification_model: "m" }),
    extractClaim: () => Promise.resolve(extraction()),
    verifyClaim: async (_req, onStage) => {
      onStage("analyzing");
      onStage("searching");
      onStage("evaluating");
      return verification();
    },
    ...overrides,
  };
}

export const OK_CONTEXT: VideoContext = { status: "ok", videoId: VIDEO_ID, timestampSec: 755.3, durationSec: 1800, title: "The Future of Coding", publishedDate: "2024-05-01" };
