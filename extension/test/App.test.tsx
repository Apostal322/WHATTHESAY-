import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../src/shared/api";
import { App } from "../src/sidepanel/App";
import { extraction, fakeApi, OK_CONTEXT, staticVideoSource, VIDEO_ID } from "./fixtures";

describe("App", () => {
  it("runs the full flow: extract, edit, verify, show verdict with safe sources", async () => {
    const user = userEvent.setup();
    const verifyClaim = vi.fn(fakeApi().verifyClaim);
    const extractClaim = vi.fn(fakeApi().extractClaim);
    render(<App api={fakeApi({ verifyClaim, extractClaim })} videoSource={staticVideoSource(OK_CONTEXT)} />);

    const verifyBtn = await screen.findByRole("button", { name: /verify claim at 12:35/i });
    await user.click(verifyBtn);

    expect(extractClaim).toHaveBeenCalledWith(
      { video_id: VIDEO_ID, timestamp_sec: 755.3, duration_sec: 1800, language: "auto" },
      expect.any(AbortSignal),
    );

    const textarea = await screen.findByRole("textbox", { name: /claim/i });
    expect(textarea).toHaveValue(extraction().claim);
    expect(screen.getByText(/paraphrase of what was said/i)).toBeInTheDocument();

    await user.clear(textarea);
    await user.type(textarea, "AI replaced 80% of US software engineers in 2025.");
    expect(screen.getByText("Edited")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /check this claim/i }));

    expect(verifyClaim).toHaveBeenCalledWith(
      expect.objectContaining({
        claim: "AI replaced 80% of US software engineers in 2025.",
        video_id: VIDEO_ID,
        timestamp_sec: 755,
        claim_type_hint: "factual",
        context: "Discussing the tech job market.",
      }),
      expect.any(Function),
      expect.any(AbortSignal),
    );

    expect(await screen.findByRole("heading", { name: "Contradicted" })).toBeInTheDocument();
    // The user's own (edited) wording is shown, with the backend's interpretation alongside.
    expect(screen.getByText("“x”")).toBeInTheDocument();
    expect(screen.getByText(/interpreted as: AI has replaced 80%/i)).toBeInTheDocument();
    const sources = screen.getByRole("region", { name: /sources/i });
    const link = within(sources).getByRole("link", { name: /occupational outlook/i });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    // javascript: URL is rendered as plain text, and HTML in excerpts is not interpreted.
    expect(within(sources).queryByRole("link", { name: /bad link/i })).toBeNull();
    expect(within(sources).getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("disables verification and explains when not on a supported video", async () => {
    render(<App api={fakeApi()} videoSource={staticVideoSource({ status: "unsupported", reason: "shorts" })} />);
    expect(await screen.findByText(/shorts are not supported/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /verify claim/i })).toBeDisabled();
  });

  it("offers manual entry when the video is unavailable to the model", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      extractClaim: () => Promise.reject(new ApiError({ code: "video_unavailable", message: "Gemini could not access this video.", retryable: false })),
    });
    render(<App api={api} videoSource={staticVideoSource(OK_CONTEXT)} />);
    await user.click(await screen.findByRole("button", { name: /verify claim at/i }));

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(/could not access this video/i)).toBeInTheDocument();
    expect(within(alert).queryByRole("button", { name: /try again/i })).toBeNull();
    await user.click(within(alert).getByRole("button", { name: /enter claim manually/i }));
    expect(await screen.findByRole("textbox", { name: /claim/i })).toHaveValue("");
  });

  it("supports a fully manual claim without any video", async () => {
    const user = userEvent.setup();
    const verifyClaim = vi.fn(fakeApi().verifyClaim);
    render(<App api={fakeApi({ verifyClaim })} videoSource={staticVideoSource({ status: "unsupported", reason: "not_youtube" })} />);
    await user.click(await screen.findByRole("button", { name: /type a claim manually/i }));
    const textarea = screen.getByRole("textbox", { name: /claim/i });
    const submit = screen.getByRole("button", { name: /check this claim/i });
    expect(submit).toBeDisabled();
    await user.type(textarea, "The Great Wall is visible from the Moon.{Control>}{Enter}{/Control}");
    await screen.findByRole("heading", { name: "Contradicted" });
    expect(verifyClaim.mock.calls[0]?.[0]).toMatchObject({ claim: "The Great Wall is visible from the Moon.", video_id: null, context: null });
  });

  it("shows a retryable verification error and retries with the same claim", async () => {
    const user = userEvent.setup();
    const verifyClaim = vi
      .fn()
      .mockRejectedValueOnce(new ApiError({ code: "provider_timeout", message: "The AI provider did not respond in time.", retryable: true }))
      .mockImplementationOnce(fakeApi().verifyClaim);
    render(<App api={fakeApi({ verifyClaim })} videoSource={staticVideoSource(OK_CONTEXT)} />);
    await user.click(await screen.findByRole("button", { name: /type a claim manually/i }));
    await user.type(screen.getByRole("textbox", { name: /claim/i }), "Water boils at 50C at sea level.");
    await user.click(screen.getByRole("button", { name: /check this claim/i }));

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(/did not respond in time/i)).toBeInTheDocument();
    await user.click(within(alert).getByRole("button", { name: /try again/i }));
    await screen.findByRole("heading", { name: "Contradicted" });
    expect(verifyClaim).toHaveBeenCalledTimes(2);
    expect(verifyClaim.mock.calls[1]?.[0]).toMatchObject({ claim: "Water boils at 50C at sea level." });
  });

  it("shows backend configuration problems up front", async () => {
    const api = fakeApi({
      health: () => Promise.resolve({ status: "ok", version: "0", gemini_configured: false, extraction_model: "m", verification_model: "m" }),
    });
    render(<App api={api} videoSource={staticVideoSource(OK_CONTEXT)} />);
    await waitFor(() => expect(screen.getByText(/no GEMINI_API_KEY/i)).toBeInTheDocument());
  });
});
