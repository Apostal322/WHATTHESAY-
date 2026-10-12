import { useId } from "react";
import { CLAIM_TYPE_LABEL, formatTimestamp } from "../shared/format";
import { canSubmitClaim, MAX_CLAIM_LENGTH } from "../shared/state";
import type { ExtractResponse } from "../shared/types";

interface Props {
  value: string;
  extraction: ExtractResponse | null;
  notice: string | null;
  onChange: (text: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
}

export function ClaimEditor({ value, extraction, notice, onChange, onSubmit, onCancel }: Props) {
  const id = useId();
  const fromVideo = extraction?.status === "success";
  const edited = fromVideo && value.trim() !== (extraction.claim ?? "").trim();
  const valid = canSubmitClaim(value);

  return (
    <section className="card" aria-labelledby={`${id}-title`}>
      <div className="card-head">
        <h2 id={`${id}-title`} className="card-title">
          {fromVideo ? "Is this the statement?" : "Enter the claim to check"}
        </h2>
        {fromVideo && extraction.claim_type && (
          <span className={`badge badge-type-${extraction.claim_type}`}>{CLAIM_TYPE_LABEL[extraction.claim_type]}</span>
        )}
      </div>

      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}

      {fromVideo && (
        <p className="meta">
          Around {formatTimestamp(extraction.timestamp_sec)} · AI-extracted, may be the wrong sentence. Edit if needed.
        </p>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) onSubmit();
        }}
      >
        <label htmlFor={`${id}-claim`} className="visually-hidden">
          Claim
        </label>
        <textarea
          id={`${id}-claim`}
          className="claim-input"
          value={value}
          maxLength={MAX_CLAIM_LENGTH}
          rows={4}
          placeholder="e.g. AI has already replaced 80% of software engineers."
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && valid) {
              e.preventDefault();
              onSubmit();
            }
          }}
          autoFocus
        />
        <div className="input-foot">
          <span className="meta">{edited ? "Edited" : " "}</span>
          <span className="meta">
            {value.length}/{MAX_CLAIM_LENGTH}
          </span>
        </div>

        {fromVideo && extraction.evidence_from_video && (
          <blockquote className="speech">
            <span className="speech-label">{extraction.is_exact_quote ? "AI transcription (may contain errors)" : "Paraphrase of what was said"}</span>
            {extraction.is_exact_quote ? `“${extraction.evidence_from_video}”` : extraction.evidence_from_video}
          </blockquote>
        )}
        {fromVideo && extraction.context && (
          <details className="context">
            <summary>Context</summary>
            <p>{extraction.context}</p>
          </details>
        )}

        <div className="actions">
          <button type="submit" className="btn btn-primary" disabled={!valid}>
            Check this claim
          </button>
          <button type="button" className="btn btn-ghost" onClick={onCancel}>
            Start over
          </button>
        </div>
        <p className="hint">Ctrl+Enter to submit</p>
      </form>
    </section>
  );
}
