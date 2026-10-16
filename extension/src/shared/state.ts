import type { ClaimType, ErrorBody, ExtractResponse, VerifyResponse, VerifyStage } from "./types";

export type Phase =
  | "idle"
  | "extracting"
  | "claim_ready"
  | "searching"
  | "evaluating"
  | "complete"
  | "error";

export interface ClaimOrigin {
  source: "video" | "manual";
  videoId: string | null;
  timestampSec: number | null;
  context: string | null;
  claimTypeHint: ClaimType | null;
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
  | { type: "EXTRACT_START"; videoId: string; timestampSec: number }
  | { type: "EXTRACT_DONE"; response: ExtractResponse }
  | { type: "MANUAL_ENTRY"; videoId: string | null; timestampSec: number | null; notice?: string }
  | { type: "EDIT_CLAIM"; text: string }
  | { type: "VERIFY_START" }
  | { type: "VERIFY_STAGE"; stage: VerifyStage }
  | { type: "VERIFY_DONE"; result: VerifyResponse }
  | { type: "FAIL"; error: UiError }
  | { type: "EDIT_AGAIN" }
  | { type: "RESET" };

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
        },
      };

    case "EXTRACT_DONE": {
      if (state.phase !== "extracting") return state; // stale response after cancel
      const r = action.response;
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
  }
}
