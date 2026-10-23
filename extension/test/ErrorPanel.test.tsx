import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ErrorPanel } from "../src/components/ErrorPanel";
import type { UiError } from "../src/shared/state";

const noop = () => undefined;
const show = (error: UiError) => render(<ErrorPanel error={error} onRetry={noop} onManual={noop} onReset={noop} />);

describe("ErrorPanel speaks plainly and never presents a failure as a verdict", () => {
  it("a search outage says so, keeps the claim, and is not a judgement on it", () => {
    show({ code: "retrieval_unavailable", message: "Evidence search is temporarily unavailable. Try again shortly.", retryable: true, during: "verification" });
    expect(screen.getByText("Evidence search is temporarily unavailable. Your claim is saved. Please try again later.")).toBeInTheDocument();
    expect(screen.getByText(/not a judgement on the claim/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("an exhausted quota is explained without provider jargon in the headline", () => {
    show({ code: "provider_quota_exhausted", message: "The Gemini API quota for this key has been exceeded.", retryable: false, during: "verification" });
    const headline = screen.getByText(/checking capacity has been used up/i);
    expect(headline.textContent).not.toMatch(/gemini|quota|api/i);
    expect(screen.queryByRole("button", { name: /try again/i })).toBeNull(); // not retryable now
  });

  it("keeps the backend's exact message and code under Technical details", () => {
    show({ code: "provider_error", message: "The AI provider had a server error. Try again.", retryable: true, during: "extraction" });
    expect(screen.getByText("Technical details")).toBeInTheDocument();
    expect(screen.getByText("The AI provider had a server error. Try again.")).toBeInTheDocument();
    expect(screen.getByText("Error code: provider_error")).toBeInTheDocument();
    // Nothing was extracted yet, so nothing claims to be saved.
    expect(screen.getByText(/busy or did not respond/i).textContent).not.toMatch(/saved/);
  });

  it("an unknown error falls back to the backend message", () => {
    show({ code: "something_new", message: "Specific backend explanation.", retryable: false, during: "verification" });
    expect(screen.getAllByText("Specific backend explanation.").length).toBeGreaterThan(0);
  });
});
