import { describe, expect, it } from "vitest";
import { canSubmitClaim, initialState, reducer, type State } from "../src/shared/state";
import { extraction, VIDEO_ID, verification } from "./fixtures";

const run = (...actions: Parameters<typeof reducer>[1][]): State => actions.reduce(reducer, initialState);

describe("reducer", () => {
  it("goes idle -> extracting -> claim_ready with the extracted claim", () => {
    const s = run({ type: "EXTRACT_START", videoId: VIDEO_ID, timestampSec: 755 }, { type: "EXTRACT_DONE", response: extraction() });
    expect(s.phase).toBe("claim_ready");
    expect(s.draftClaim).toContain("80%");
    expect(s.origin).toMatchObject({ source: "video", videoId: VIDEO_ID, timestampSec: 755, claimTypeHint: "factual" });
  });

  it("falls back to manual entry with a notice when no claim is found", () => {
    const s = run(
      { type: "EXTRACT_START", videoId: VIDEO_ID, timestampSec: 5 },
      { type: "EXTRACT_DONE", response: extraction({ status: "no_claim_found", claim: null, claim_type: null, reason: "Only music." }) },
    );
    expect(s.phase).toBe("claim_ready");
    expect(s.draftClaim).toBe("");
    expect(s.notice).toBe("Only music.");
    expect(s.origin?.source).toBe("manual");
  });

  it("ignores a stale extraction result after reset", () => {
    const s = run({ type: "EXTRACT_START", videoId: VIDEO_ID, timestampSec: 5 }, { type: "RESET" }, { type: "EXTRACT_DONE", response: extraction() });
    expect(s.phase).toBe("idle");
  });

  it("walks through verification stages to complete", () => {
    let s = run({ type: "MANUAL_ENTRY", videoId: null, timestampSec: null }, { type: "EDIT_CLAIM", text: "The moon is made of cheese." });
    s = reducer(s, { type: "VERIFY_START" });
    expect(s).toMatchObject({ phase: "searching", stage: "analyzing" });
    s = reducer(s, { type: "VERIFY_STAGE", stage: "searching" });
    expect(s.phase).toBe("searching");
    s = reducer(s, { type: "VERIFY_STAGE", stage: "evaluating" });
    expect(s.phase).toBe("evaluating");
    s = reducer(s, { type: "VERIFY_DONE", result: verification() });
    expect(s.phase).toBe("complete");
    expect(s.result?.verdict).toBe("contradicted");
  });

  it("refuses to verify an empty or too-short claim", () => {
    const s = run({ type: "MANUAL_ENTRY", videoId: null, timestampSec: null }, { type: "EDIT_CLAIM", text: "  ab " }, { type: "VERIFY_START" });
    expect(s.phase).toBe("claim_ready");
  });

  it("keeps the draft on verification error and allows editing again", () => {
    let s = run({ type: "MANUAL_ENTRY", videoId: null, timestampSec: null }, { type: "EDIT_CLAIM", text: "Water boils at 50C at sea level." }, { type: "VERIFY_START" });
    s = reducer(s, { type: "FAIL", error: { code: "provider_timeout", message: "slow", retryable: true, during: "verification" } });
    expect(s.phase).toBe("error");
    expect(s.draftClaim).toBe("Water boils at 50C at sea level.");
    s = reducer(s, { type: "EDIT_AGAIN" });
    expect(s.phase).toBe("claim_ready");
    expect(s.error).toBeNull();
  });

  it("treats cancellation as a return to the previous step, not an error", () => {
    let s = run({ type: "MANUAL_ENTRY", videoId: null, timestampSec: null }, { type: "EDIT_CLAIM", text: "Water boils at 50C." }, { type: "VERIFY_START" });
    s = reducer(s, { type: "FAIL", error: { code: "cancelled", message: "", retryable: false, during: "verification" } });
    expect(s.phase).toBe("claim_ready");
    s = run({ type: "EXTRACT_START", videoId: VIDEO_ID, timestampSec: 1 }, { type: "FAIL", error: { code: "cancelled", message: "", retryable: false, during: "extraction" } });
    expect(s.phase).toBe("idle");
  });
});

describe("canSubmitClaim", () => {
  it.each([
    ["", false],
    ["abcd", false],
    ["!!!!!!", false],
    ["Water boils at 100C", true],
    ["Вода кипит при 100 градусах", true],
    ["x".repeat(1001), false],
  ])("%j -> %s", (text, ok) => {
    expect(canSubmitClaim(text)).toBe(ok);
  });
});
