import { safeExternalUrl, STANCE_LABEL } from "../shared/format";
import type { SourceRef } from "../shared/types";

export function SourceList({ sources }: { sources: SourceRef[] }) {
  if (sources.length === 0) return null;
  return (
    <section className="card" aria-labelledby="sources-title">
      <h2 id="sources-title" className="card-title">
        Sources
      </h2>
      <p className="meta">Retrieved via Google Search. Excerpts are AI summaries attributed to each source, not quotes. Open the source to confirm.</p>
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
