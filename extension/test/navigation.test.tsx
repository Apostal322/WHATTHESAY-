// A check stays bound to the video it came from (H-23, H-27). These tests move the panel between
// videos the way YouTube's in-app navigation and tab switching do (the video source announces a
// new context), at every stage of a check.
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "../src/sidepanel/App";
import type { ExtractResponse, VerifyResponse, VideoContext } from "../src/shared/types";
import { extraction, fakeApi, OK_CONTEXT, staticVideoSource, verification, VIDEO_ID } from "./fixtures";

const OTHER: VideoContext = {
  status: "ok",
  videoId: "aaaaaaaaaaa",
  timestampSec: 10,
  durationSec: 600,
  title: "Another Video",
  publishedDate: null,
};
const THIRD: VideoContext = { ...OTHER, videoId: "bbbbbbbbbbb", title: "Third Video" } as VideoContext;

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

async function finishCheck(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /verify claim at 12:35/i }));
  await user.click(await screen.findByRole("button", { name: /check this claim/i }));
  await screen.findByRole("heading", { name: "Contradicted" });
}

const verdictShown = () => screen.queryByRole("heading", { name: "Contradicted" });

describe("claim-to-video binding", () => {
  it("does not show a verdict for video A under video B until asked, and then labels it", async () => {
    // Failed before: the verdict stayed on screen under the new video, with only a warning line.
    const user = userEvent.setup();
    const source = staticVideoSource(OK_CONTEXT);
    render(<App api={fakeApi()} videoSource={source} />);
    await finishCheck(user);
    expect(screen.getByTestId("claim-origin")).toHaveTextContent("Claim from The Future of Coding · 12:35");

    act(() => source.set(OTHER));
    expect(await screen.findByText("This result is for another video")).toBeInTheDocument();
    expect(verdictShown()).toBeNull();

    await user.click(screen.getByRole("button", { name: /show it/i }));
    expect(verdictShown()).toBeInTheDocument();
    expect(screen.getByTestId("claim-origin")).toHaveTextContent("The Future of Coding · 12:35");
    expect(screen.getByText(/not the one now playing/i)).toBeInTheDocument();
  });

  it("shows the result normally again on returning to its video", async () => {
    const user = userEvent.setup();
    const source = staticVideoSource(OK_CONTEXT);
    render(<App api={fakeApi()} videoSource={source} />);
    await finishCheck(user);
    act(() => source.set(OTHER));
    await screen.findByText("This result is for another video");
    act(() => source.set(OK_CONTEXT));
    await waitFor(() => expect(verdictShown()).toBeInTheDocument());
    expect(screen.queryByText(/not the one now playing/i)).not.toBeInTheDocument();
  });

  it("keeps a pending verification bound to its video and holds its result back", async () => {
    const user = userEvent.setup();
    const source = staticVideoSource(OK_CONTEXT);
    const pending = deferred<VerifyResponse>();
    const api = fakeApi({ verifyClaim: () => pending.promise });
    render(<App api={api} videoSource={source} />);
    await user.click(await screen.findByRole("button", { name: /verify claim at 12:35/i }));
    await user.click(await screen.findByRole("button", { name: /check this claim/i }));

    act(() => source.set(OTHER)); // navigate while the check runs
    expect(await screen.findByText(/not the one now playing/i)).toBeInTheDocument();
    expect(screen.getByTestId("claim-origin")).toHaveTextContent("The Future of Coding");

    await act(async () => pending.resolve(verification()));
    expect(await screen.findByText("This result is for another video")).toBeInTheDocument();
    expect(verdictShown()).toBeNull();
  });

  it("binds an extraction to the video it started on, through rapid navigation", async () => {
    const user = userEvent.setup();
    const source = staticVideoSource(OK_CONTEXT);
    const pending = deferred<ExtractResponse>();
    const extractClaim = vi.fn(() => pending.promise);
    const verifyClaim = vi.fn(fakeApi().verifyClaim);
    render(<App api={fakeApi({ extractClaim, verifyClaim })} videoSource={source} />);
    await user.click(await screen.findByRole("button", { name: /verify claim at 12:35/i }));

    act(() => source.set(OTHER));
    act(() => source.set(THIRD));
    await act(async () => pending.resolve(extraction()));

    expect(await screen.findByText("This claim is from another video")).toBeInTheDocument();
    expect(extractClaim).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: /show it/i }));
    await user.click(await screen.findByRole("button", { name: /check this claim/i }));
    expect(verifyClaim.mock.calls[0]?.[0]).toMatchObject({ video_id: VIDEO_ID, timestamp_sec: 755 });
  });

  it("treats switching to a tab with another video like navigation, and the same video as the same", async () => {
    const user = userEvent.setup();
    const source = staticVideoSource(OK_CONTEXT);
    render(<App api={fakeApi()} videoSource={source} />);
    await finishCheck(user);
    act(() => source.set({ ...OK_CONTEXT, timestampSec: 900 } as VideoContext)); // same video, other tab
    await waitFor(() => expect(verdictShown()).toBeInTheDocument());
    act(() => source.set(OTHER));
    expect(await screen.findByText("This result is for another video")).toBeInTheDocument();
  });

  it("keeps the result while the page context is briefly unavailable (service worker restart)", async () => {
    const user = userEvent.setup();
    const source = staticVideoSource(OK_CONTEXT);
    render(<App api={fakeApi()} videoSource={source} />);
    await finishCheck(user);
    act(() => source.set({ status: "unsupported", reason: "no_video" }));
    await waitFor(() => expect(screen.getByTestId("video-unsupported")).toBeInTheDocument());
    expect(verdictShown()).toBeInTheDocument();
    expect(screen.getByTestId("claim-origin")).toHaveTextContent("The Future of Coding");
  });

  it("rejects an extraction answer that is for a different video", async () => {
    const user = userEvent.setup();
    const api = fakeApi({ extractClaim: () => Promise.resolve(extraction({ video_id: "zzzzzzzzzzz" })) });
    render(<App api={api} videoSource={staticVideoSource(OK_CONTEXT)} />);
    await user.click(await screen.findByRole("button", { name: /verify claim at 12:35/i }));
    expect((await screen.findAllByText(/did not match the requested video/i)).length).toBeGreaterThan(0);
    expect(screen.queryByRole("textbox", { name: /claim/i })).toBeNull();
  });

  it("retries a failed extraction with its own video's details, not the playing video's", async () => {
    // Failed before: the retry used the duration of whatever video was playing.
    const user = userEvent.setup();
    const source = staticVideoSource(OK_CONTEXT);
    const extractClaim = vi
      .fn()
      .mockRejectedValueOnce(new Error("network"))
      .mockImplementation(fakeApi().extractClaim);
    render(<App api={fakeApi({ extractClaim })} videoSource={source} />);
    await user.click(await screen.findByRole("button", { name: /verify claim at 12:35/i }));
    await screen.findAllByText(/something went wrong/i);

    act(() => source.set(OTHER));
    await user.click(await screen.findByRole("button", { name: /show it/i }));
    await user.click(screen.getByRole("button", { name: /try again/i }));
    expect(extractClaim.mock.calls[1]?.[0]).toMatchObject({ video_id: VIDEO_ID, timestamp_sec: 755.3, duration_sec: 1800 });
  });

  it("can start a new check here and still bring back the previous one, labelled", async () => {
    const user = userEvent.setup();
    const source = staticVideoSource(OK_CONTEXT);
    render(<App api={fakeApi()} videoSource={source} />);
    await finishCheck(user);
    act(() => source.set(OTHER));
    await user.click(await screen.findByRole("button", { name: /new check for this video/i }));
    expect(await screen.findByRole("button", { name: /verify claim at 00:10/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /show the previous check \(the future of coding\)/i }));
    // Restored, but it is still video A's result: held back while video B plays.
    expect(await screen.findByText("This result is for another video")).toBeInTheDocument();
  });

  it("shows when the claim was said only as far as it is known", async () => {
    const user = userEvent.setup();
    const source = staticVideoSource(OK_CONTEXT);
    const api = fakeApi({ extractClaim: () => Promise.resolve(extraction({ statement_date: "1998-05" })) });
    const { unmount } = render(<App api={fakeApi()} videoSource={source} />);
    await user.click(await screen.findByRole("button", { name: /verify claim at 12:35/i }));
    expect(await screen.findByTestId("claim-origin")).toHaveTextContent(
      "Said: date unknown; the video was published 2024-05-01",
    );
    unmount();
    render(<App api={api} videoSource={staticVideoSource(OK_CONTEXT)} />);
    await user.click(await screen.findByRole("button", { name: /verify claim at 12:35/i }));
    expect(await screen.findByTestId("claim-origin")).toHaveTextContent("Said: 1998-05 (shown in the video)");
  });
});
