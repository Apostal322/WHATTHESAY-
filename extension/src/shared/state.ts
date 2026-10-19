import type { ClaimType, ErrorBody, ExtractResponse, VerifyResponse, VerifyStage } from "./types";

export type Phase =
  | "idle"
  | "extracting"
  | "claim_ready"
  | "searching"
  | "evaluating"
  | "complete"
  | "error";

/**
 * What a check is bound to. A claim taken from a video stays bound to that video (id, title,
 * moment) for its whole life, whatever the user watches next.
 */
export interface ClaimOrigin {
  source: "video" | "manual";
  videoId: string | null;
  timestampSec: number | null;
  context: string | null;
  claimTypeHint: ClaimType | null;
  videoTitle: string | null;
  durationSec: number | null;
  /** The video's upload date: only an upper bound on when the claim was said. */
  videoPublishedDate: string | null;
  /** When it was said, only if the clip itself shows it (from the extraction). */
  statementDate: string | null;
}

/** The video a check starts from, as read from the page. */
export interface VideoRef {
  videoId: string;
  timestampSec: number;
  durationSec?: number | null;
  title?: string | null;
  publishedDate?: string | null;
}

export interface UiError extends ErrorBody {
  /** Which phase the user returns to on "Try again". */
  during: "extraction" | "verification";
}

export interface State {
  phase: Phase;
  stage: VerifyStage | null;
  extraction: ExtractResponse | null;
  draftClaim: string;
  origin: ClaimOrigin | null;
  notice: string | null;
  result: VerifyResponse | null;
  error: UiError | null;
}

export const initialState: State = {
  phase: "idle",
  stage: null,
  extraction: null,
  draftClaim: "",
  origin: null,
  notice: null,
  result: null,
  error: null,
};

export type Action =
  | ({ type: "EXTRACT_START" } & VideoRef)
  | { type: "EXTRACT_DONE"; response: ExtractResponse }
  | { type: "MANUAL_ENTRY"; videoId: string | null; timestampSec: number | null; notice?: string }
  | { type: "EDIT_CLAIM"; text: string }
  | { type: "VERIFY_START" }
  | { type: "VERIFY_STAGE"; stage: VerifyStage }
  | { type: "VERIFY_DONE"; result: VerifyResponse }
  | { type: "FAIL"; error: UiError }
  | { type: "EDIT_AGAIN" }
  | { type: "RESET" }
  /** Bring back an earlier check exactly as it was (it keeps its own video binding). */
  | { type: "RESTORE"; snapshot: State };

export const MIN_CLAIM_LENGTH = 5;
export const MAX_CLAIM_LENGTH = 1000;

export function canSubmitClaim(text: string): boolean {
  const t = text.trim();
  return t.length >= MIN_CLAIM_LENGTH && t.length <= MAX_CLAIM_LENGTH && /[\p{L}\p{N}]/u.test(t);
}

export function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "EXTRACT_START":
      return {
        ...initialState,
        phase: "extracting",
        origin: {
          source: "video",
          videoId: action.videoId,
          timestampSec: action.timestampSec,
          context: null,
          claimTypeHint: null,
          videoTitle: action.title ?? null,
          durationSec: action.durationSec ?? null,
          videoPublishedDate: action.publishedDate ?? null,
          statementDate: null,
        },
      };

    case "EXTRACT_DONE": {
      if (state.phase !== "extracting") return state; // stale response after cancel
      const r = action.response;
      if (state.origin?.videoId != null && r.video_id !== state.origin.videoId) {
        // Never show a result for one video as if it were about another.
        return {
          ...state,
          phase: "error",
          error: {
            code: "internal_error",
            message: "The result did not match the requested video. Try again.",
            retryable: true,
            during: "extraction",
          },
        };
      }
      const found = r.status === "success" && r.claim;
      return {
        ...state,
        phase: "claim_ready",
        extraction: r,
        draftClaim: found ? (r.claim ?? "") : "",
        notice: found ? null : (r.reason ?? "No checkable statement was found near this moment."),
        origin: {
          source: found ? "video" : "manual",
          videoId: r.video_id,
          timestampSec: r.timestamp_sec,
          context: r.context,
          claimTypeHint: r.claim_type,
          videoTitle: state.origin?.videoTitle ?? null,
          durationSec: state.origin?.durationSec ?? null,
          videoPublishedDate: state.origin?.videoPublishedDate ?? null,
          statementDate: found ? (r.statement_date ?? null) : null,
        },
      };
    }

    case "MANUAL_ENTRY":
      return {
        ...initialState,
        phase: "claim_ready",
        notice: action.notice ?? null,
        origin: {
          source: "manual",
          videoId: action.videoId,
          timestampSec: action.timestampSec,
          context: null,
          claimTypeHint: null,
          videoTitle: null,
          durationSec: null,
          videoPublishedDate: null,
          statementDate: null,
        },
      };

    case "EDIT_CLAIM":
      return { ...state, draftClaim: action.text };

    case "VERIFY_START":
      if (!canSubmitClaim(state.draftClaim)) return state;
      return { ...state, phase: "searching", stage: "analyzing", result: null, error: null };

    case "VERIFY_STAGE":
      if (state.phase !== "searching" && state.phase !== "evaluating") return state;
      return { ...state, stage: action.stage, phase: action.stage === "evaluating" ? "evaluating" : "searching" };

    case "VERIFY_DONE":
      if (state.phase !== "searching" && state.phase !== "evaluating") return state;
      return { ...state, phase: "complete", stage: null, result: action.result };

    case "FAIL":
      if (action.error.code === "cancelled") {
        return action.error.during === "verification"
          ? { ...state, phase: "claim_ready", stage: null }
          : { ...initialState };
      }
      return { ...state, phase: "error", stage: null, error: action.error };

    case "EDIT_AGAIN":
      return { ...state, phase: "claim_ready", stage: null, result: null, error: null };

    case "RESET":
      return initialState;

    case "RESTORE":
      return action.snapshot;
  }
}
