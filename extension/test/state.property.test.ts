import { describe, expect, it } from "vitest";
import { initialState, reducer, type Action, type State } from "../src/shared/state";
import { extraction, verification } from "./fixtures";

/** Small seeded PRNG (mulberry32): the sequences are random but reproducible. */
function rng(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const ACTIONS: Action[] = [
  { type: "EXTRACT_START", videoId: "dQw4w9WgXcQ", timestampSec: 10 },
  { type: "EXTRACT_DONE", response: extraction() },
  { type: "EXTRACT_DONE", response: extraction({ status: "no_claim_found", claim: null, reason: "none" }) },
  { type: "MANUAL_ENTRY", videoId: null, timestampSec: null },
  { type: "EDIT_CLAIM", text: "The tower opened in 1889." },
  { type: "EDIT_CLAIM", text: "" },
  { type: "VERIFY_START" },
  { type: "VERIFY_STAGE", stage: "searching" },
  { type: "VERIFY_STAGE", stage: "evaluating" },
  { type: "VERIFY_DONE", result: verification() },
  { type: "FAIL", error: { code: "provider_error", message: "x", retryable: true, during: "verification" } },
  { type: "FAIL", error: { code: "cancelled", message: "x", retryable: false, during: "verification" } },
  { type: "FAIL", error: { code: "cancelled", message: "x", retryable: false, during: "extraction" } },
  { type: "EDIT_AGAIN" },
  { type: "RESET" },
];

function check(prev: State, action: Action, next: State) {
  if (next.phase === "complete") expect(next.result).not.toBeNull();
  if (next.phase === "error") expect(next.error).not.toBeNull();
  if (next.phase === "searching" || next.phase === "evaluating") expect(next.stage).not.toBeNull();
  // A result or a stage update only lands while a verification is running.
  if (action.type === "VERIFY_DONE" || action.type === "VERIFY_STAGE") {
    if (prev.phase !== "searching" && prev.phase !== "evaluating") expect(next).toBe(prev);
  }
  // An extraction result only lands while extracting (a stale one is ignored).
  if (action.type === "EXTRACT_DONE" && prev.phase !== "extracting") expect(next).toBe(prev);
  // Verification never starts from text the user could not submit.
  if (action.type === "VERIFY_START" && next !== prev) expect(prev.draftClaim.trim().length).toBeGreaterThanOrEqual(5);
}

describe("state machine invariants over random action sequences", () => {
  it("holds for 2000 seeded sequences of 30 actions", () => {
    for (let seed = 1; seed <= 2000; seed++) {
      const random = rng(seed);
      let state = initialState;
      for (let i = 0; i < 30; i++) {
        const action = ACTIONS[Math.floor(random() * ACTIONS.length)]!;
        const next = reducer(state, action);
        try {
          check(state, action, next);
        } catch (err) {
          throw new Error(`seed ${seed}, step ${i}, action ${action.type}: ${(err as Error).message}`);
        }
        state = next;
      }
    }
  });
});
