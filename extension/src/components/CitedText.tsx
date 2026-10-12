import { Fragment } from "react";
import { splitCitations } from "../shared/format";

/** Renders text with [S#] markers as in-panel links. Text is never parsed as HTML. */
export function CitedText({ text, knownIds }: { text: string; knownIds: ReadonlySet<string> }) {
  return (
    <>
      {splitCitations(text, knownIds).map((seg, i) =>
        seg.kind === "text" ? (
          <Fragment key={i}>{seg.text}</Fragment>
        ) : (
          <a key={i} className="cite" href={`#source-${seg.id}`} aria-label={`Source ${seg.id}`}>
            {seg.id}
          </a>
        ),
      )}
    </>
  );
}
