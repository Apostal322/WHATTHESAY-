import { describe, expect, it, vi } from "vitest";
import { ApiError, createApiClient, parseNdjson } from "../src/shared/api";
import { safeExternalUrl, splitCitations } from "../src/shared/format";
import { verification } from "./fixtures";

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
}

describe("parseNdjson", () => {
  it("handles events split across chunk boundaries", async () => {
    const out: unknown[] = [];
    for await (const evt of parseNdjson(streamOf('{"a":', '1}\n{"b"', ":2}\n\n", '{"c":3}'))) out.push(evt);
    expect(out).toEqual([{ a: 1 }, { b: 2 }, { c: 3 }]);
  });
});

describe("api client", () => {
  it("streams stages and returns the result", async () => {
    const body = [
      JSON.stringify({ event: "stage", stage: "analyzing" }),
      JSON.stringify({ event: "stage", stage: "searching" }),
      JSON.stringify({ event: "result", data: verification() }),
    ].join("\n");
    const fetchMock = vi.fn().mockResolvedValue(new Response(streamOf(body), { status: 200 }));
    const stages: string[] = [];
    const result = await createApiClient("http://x", fetchMock).verifyClaim({ claim: "abc def", language: "auto" }, (s) => stages.push(s));
    expect(stages).toEqual(["analyzing", "searching"]);
    expect(result.verdict).toBe("contradicted");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://x/v1/claims/verify/stream");
    expect(JSON.parse(init.body as string)).toEqual({ claim: "abc def", language: "auto" });
  });

  it("surfaces in-band stream errors as ApiError", async () => {
    const body = JSON.stringify({ event: "error", error: { code: "provider_timeout", message: "slow", retryable: true } });
    const fetchMock = vi.fn().mockResolvedValue(new Response(streamOf(body), { status: 200 }));
    await expect(createApiClient("http://x", fetchMock).verifyClaim({ claim: "abc def", language: "auto" }, () => undefined)).rejects.toMatchObject({
      code: "provider_timeout",
      retryable: true,
    });
  });

  it("maps structured HTTP errors", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "video_unavailable", message: "Private video.", retryable: false } }), { status: 422 }),
    );
    const err = await createApiClient("http://x", fetchMock)
      .extractClaim({ video_id: "dQw4w9WgXcQ", timestamp_sec: 1, language: "auto" })
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ code: "video_unavailable", message: "Private video." });
  });

  it("maps network failures to backend_unreachable", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(createApiClient("http://x", fetchMock).health()).rejects.toMatchObject({ code: "backend_unreachable" });
  });

  it("maps caller aborts to cancelled", async () => {
    const fetchMock = vi.fn((_url: string, init: RequestInit) => {
      return new Promise<Response>((_resolve, reject) => {
        init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      });
    });
    const controller = new AbortController();
    const p = createApiClient("http://x", fetchMock as unknown as typeof fetch).extractClaim(
      { video_id: "dQw4w9WgXcQ", timestamp_sec: 1, language: "auto" },
      controller.signal,
    );
    controller.abort();
    await expect(p).rejects.toMatchObject({ code: "cancelled" });
  });
});

describe("display helpers", () => {
  it("only allows safe external URLs", () => {
    expect(safeExternalUrl("https://www.bls.gov/x")).toBe("https://www.bls.gov/x");
    expect(safeExternalUrl("javascript:alert(1)")).toBeNull();
    expect(safeExternalUrl("http://localhost:8000")).toBeNull();
    expect(safeExternalUrl("http://192.168.1.1/")).toBeNull();
    expect(safeExternalUrl("not a url")).toBeNull();
  });

  it("splits citations and drops unknown ids", () => {
    expect(splitCitations("A [S1] B [S9].", new Set(["S1"]))).toEqual([
      { kind: "text", text: "A " },
      { kind: "cite", id: "S1" },
      { kind: "text", text: " B " },
      { kind: "text", text: "." },
    ]);
  });
});
