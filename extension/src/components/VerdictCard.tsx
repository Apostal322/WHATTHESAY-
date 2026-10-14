import { CLAIM_TYPE_LABEL, STANCE_LABEL, VERDICT_LABEL, VERDICT_SUMMARY } from "../shared/format";
import type { VerifyResponse } from "../shared/types";
import { CitedText } from "./CitedText";

export function VerdictCard({ result }: { result: VerifyResponse }) {
  const ids = new Set(result.sources.map((s) => s.id));
  const adjustments = result.verdict_adjustments ?? [];
  // The backend also states each verdict change as a guardrail note for older clients; shown
  // once here, in the safeguard notice.
  const warnings = adjustments.length
    ? result.guardrail_notes.filter((n) => !n.startsWith("Verdict changed from '"))
    : result.guardrail_notes;
  return (
    <section className={`card verdict verdict-${result.verdict}`} aria-labelledby="verdict-title">
      <div className="verdict-head">
        <h2 id="verdict-title" className="verdict-label">
          {VERDICT_LABEL[result.verdict]}
        </h2>
        <span className="badge">{CLAIM_TYPE_LABEL[result.claim_type]}</span>
      </div>
      <p className="verdict-summary">{VERDICT_SUMMARY[result.verdict]}</p>

      {adjustments.length > 0 && (
        <div className="safeguard" role="note" aria-label="Safeguard applied">
          <strong>Safeguard applied: the model's verdict was not accepted.</strong>
          <ul>
            {adjustments.map((a, i) => (
              <li key={i}>
                Model said “{VERDICT_LABEL[a.from_verdict]}”, shown as “{VERDICT_LABEL[a.to_verdict]}”: {a.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="claim-quote">“{result.claim}”</p>
      {result.normalized_claim.trim() !== result.claim.trim() && (
        <p className="meta">Interpreted as: {result.normalized_claim}</p>
      )}

      {result.explanation && (
        <p className="explanation">
          <CitedText text={result.explanation} knownIds={ids} />
        </p>
      )}

      {result.key_findings.length > 0 && (
        <>
          <h3 className="section-title">Key findings</h3>
          <ul className="findings">
            {result.key_findings.map((f, i) => (
              <li key={i} className={`finding stance-${f.stance}`}>
                <span className="stance-tag">{STANCE_LABEL[f.stance]}</span>
                <span>
                  <CitedText text={f.text} knownIds={ids} />{" "}
                  {f.source_ids.map((id) => (
                    <a key={id} className="cite" href={`#source-${id}`} aria-label={`Source ${id}`}>
                      {id}
                    </a>
                  ))}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      {(result.limitations.length > 0 || warnings.length > 0) && (
        <>
          <h3 className="section-title">Limitations</h3>
          <ul className="limitations">
            {warnings.map((n, i) => (
              <li key={`g${i}`}>{n}</li>
            ))}
            {result.limitations.map((n, i) => (
              <li key={`l${i}`}>{n}</li>
            ))}
          </ul>
        </>
      )}

      <p className="meta">
        {result.sources.length} source{result.sources.length === 1 ? "" : "s"} · {result.independent_source_count} independent domain
        {result.independent_source_count === 1 ? "" : "s"} · {(result.latency_ms / 1000).toFixed(1)}s{result.cached ? " · cached" : ""}
      </p>
    </section>
  );
}
