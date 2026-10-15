import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { VerdictCard } from "../src/components/VerdictCard";
import { verification } from "./fixtures";

const CHANGE_NOTE =
  "Verdict changed from 'supported' to 'insufficient_evidence': The claim is about what was said by Jane Doe.";

describe("VerdictCard safeguards vs warnings", () => {
  it("shows a verdict change as a safeguard, apart from the limitations, and only once", () => {
    const result = verification({
      verdict: "insufficient_evidence",
      guardrail_notes: [CHANGE_NOTE, "One excerpt could not be matched to its source."],
      verdict_adjustments: [
        {
          rule: "attribution_without_direct_evidence",
          from_verdict: "supported",
          to_verdict: "insufficient_evidence",
          reason: "The claim is about what was said by Jane Doe.",
        },
      ],
    });
    render(<VerdictCard result={result} />);

    const safeguard = screen.getByRole("note", { name: "Safeguard applied" });
    expect(within(safeguard).getByText(/Model said “Supported”, shown as “Insufficient evidence”/)).toBeInTheDocument();
    expect(screen.getByText("One excerpt could not be matched to its source.")).toBeInTheDocument();
    expect(screen.queryByText(CHANGE_NOTE)).not.toBeInTheDocument();
  });

  it("keeps an older backend's notes as warnings when it sends no adjustments", () => {
    render(<VerdictCard result={verification({ guardrail_notes: [CHANGE_NOTE] })} />);
    expect(screen.queryByRole("note", { name: "Safeguard applied" })).not.toBeInTheDocument();
    expect(screen.getByText(CHANGE_NOTE)).toBeInTheDocument();
  });

  it("moves focus to the verdict so it is announced when the result arrives", () => {
    render(<VerdictCard result={verification()} />);
    expect(document.activeElement).toBe(screen.getByRole("heading", { level: 2 }));
  });
});
