import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SourceList } from "../src/components/SourceList";
import type { SourceRef } from "../src/shared/types";

const base: SourceRef = {
  id: "S1",
  title: "BLS",
  url: "https://www.bls.gov/ooh/x",
  domain: "bls.gov",
  publisher: null,
  published_date: null,
  retrieved_at: "2026-10-13T00:00:00Z",
  excerpts: ["Employment of software developers is projected to grow."],
  stance: "contradicts",
  assessment_note: null,
};

describe("SourceList excerpt origin", () => {
  it("keeps the summary disclaimer for grounding sources (and older backends)", () => {
    render(<SourceList sources={[base]} />);
    expect(screen.getByText(/excerpts are ai summaries/i)).toBeInTheDocument();
    expect(screen.queryByText(/quoted from the page/i)).toBeNull();
  });

  it("labels page quotes and snippets individually instead of calling them summaries", () => {
    render(
      <SourceList
        sources={[
          { ...base, excerpt_origin: "page_text" },
          { ...base, id: "S2", url: "https://who.int/x", excerpt_origin: "search_snippet" },
        ]}
      />,
    );
    expect(screen.queryByText(/excerpts are ai summaries/i)).toBeNull();
    expect(screen.getByText("Quoted from the page")).toBeInTheDocument();
    expect(screen.getByText(/search snippet; the page was not read/i)).toBeInTheDocument();
  });
});
