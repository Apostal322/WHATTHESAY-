import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../src/shared/api";
import { App } from "../src/sidepanel/App";
import type { VerifyResponse } from "../src/shared/types";
import { extraction, fakeApi, OK_CONTEXT, staticVideoSource, verification } from "./fixtures";

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

/** Like the real client: a pending request rejects with `cancelled` when its signal aborts. */
function abortable<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    signal?.addEventListener("abort", () => reject(new ApiError({ code: "cancelled", message: "Cancelled.", retryable: false })));
    void promise.then(resolve);
  });
}

describe("races between user actions and pending requests", () => {
  it("a double click on Verify starts one extraction, not two (each costs a video model call)", async () => {
    const user = userEvent.setup();
    const extractClaim = vi.fn(() => Promise.resolve(extraction()));
    render(<App api={fakeApi({ extractClaim })} videoSource={staticVideoSource(OK_CONTEXT)} />);
    await user.dblClick(await screen.findByRole("button", { name: /verify claim at 12:35/i }));
    await screen.findByRole("textbox", { name: /claim/i });
    expect(extractClaim).toHaveBeenCalledTimes(1);
  });

  it("a double click stays one extraction even when reading the video position is slow", async () => {
    const user = userEvent.setup();
    const extractClaim = vi.fn(() => Promise.resolve(extraction()));
    const source = staticVideoSource(OK_CONTEXT);
    const slow = { ...source, get: () => new Promise<typeof OK_CONTEXT>((r) => setTimeout(() => r(OK_CONTEXT), 150)) };
    render(<App api={fakeApi({ extractClaim })} videoSource={slow} />);
    await user.dblClick(await screen.findByRole("button", { name: /verify claim at 12:35/i }));
    await screen.findByRole("textbox", { name: /claim/i }, { timeout: 2000 });
    expect(extractClaim).toHaveBeenCalledTimes(1);
  });

  it("a late result from a cancelled verification cannot replace the newer one", async () => {
    const user = userEvent.setup();
    const first = deferred<VerifyResponse>();
    const second = deferred<VerifyResponse>();
    const pending = [first, second];
    const verifyClaim = vi.fn((_req: unknown, _onStage: unknown, signal?: AbortSignal) => abortable(pending.shift()!.promise, signal));
    render(<App api={fakeApi({ verifyClaim })} videoSource={staticVideoSource(OK_CONTEXT)} />);
    await user.click(await screen.findByRole("button", { name: /verify claim at 12:35/i }));

    await user.click(await screen.findByRole("button", { name: /check this claim/i }));
    await user.click(screen.getByRole("button", { name: /cancel/i }));
    await user.click(await screen.findByRole("button", { name: /check this claim/i }));

    await act(async () => first.resolve(verification({ verdict: "supported", explanation: "OLD" })));
    await act(async () => second.resolve(verification({ verdict: "contradicted", explanation: "NEW" })));
    expect(await screen.findByRole("heading", { name: "Contradicted" })).toBeInTheDocument();
    expect(screen.queryByText("OLD")).toBeNull();
  });
});
