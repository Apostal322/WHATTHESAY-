import { safeExternalUrl, STANCE_LABEL } from "../shared/format";
import type { ExcerptOrigin, SourceRef } from "../shared/types";

const ORIGIN_LABEL: Record<ExcerptOrigin, string> = {
  grounding_summary: "AI summary attributed to this source, not a quote",
  page_text: "Quoted from the page",
  search_snippet: "Search snippet; the page was not read",
};

const originOf = (s: SourceRef): ExcerptOrigin => s.excerpt_origin ?? "grounding_summary";

export function SourceList({ sources }: { sources: SourceRef[] }) {
  if (sources.length === 0) return null;
  const allSummaries = sources.every((s) => originOf(s) === "grounding_summary");
  return (
    <section className="card" aria-labelledby="sources-title">
      <h2 id="sources-title" className="card-title">
        Sources
      </h2>
      <p className="meta">
        {allSummaries
          ? "Retrieved via Google Search. Excerpts are AI summaries attributed to each source, not quotes. Open the source to confirm."
          : "Each excerpt is labelled with where it comes from. Open the source to confirm."}
      </p>
      <ol className="sources">
        {sources.map((s) => {
          const href = safeExternalUrl(s.url);
          const label = s.title || s.domain || "Untitled source";
          return (
            <li key={s.id} id={`source-${s.id}`} className="source" tabIndex={-1}>
              <div className="source-head">
                <span className="source-id">{s.id}</span>
                {href ? (
                  <a className="source-title" href={href} target="_blank" rel="noopener noreferrer">
                    {label}
                  </a>
                ) : (
                  <span className="source-title">{label}</span>
                )}
                {s.stance && <span className={`stance-tag stance-${s.stance}`}>{STANCE_LABEL[s.stance]}</span>}
              </div>
              {s.domain && <div className="meta">{s.domain}</div>}
              {!allSummaries && s.excerpts.length > 0 && <div className="meta">{ORIGIN_LABEL[originOf(s)]}</div>}
              {s.excerpts.map((ex, i) => (
                <p key={i} className="excerpt">
                  {ex}
                </p>
              ))}
              {s.assessment_note && <p className="meta note">{s.assessment_note}</p>}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
