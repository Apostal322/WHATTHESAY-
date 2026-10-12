import { errorHint } from "../shared/format";
import type { UiError } from "../shared/state";

interface Props {
  error: UiError;
  onRetry: () => void;
  onManual: () => void;
  onReset: () => void;
}

export function ErrorPanel({ error, onRetry, onManual, onReset }: Props) {
  const hint = errorHint(error.code);
  const showRetry = error.retryable || error.code === "client_timeout";
  return (
    <section className="card error" role="alert" aria-labelledby="error-title">
      <h2 id="error-title" className="card-title">
        {error.during === "extraction" ? "Couldn’t extract the claim" : "Couldn’t verify the claim"}
      </h2>
      <p>{error.message}</p>
      {hint && <p className="meta">{hint}</p>}
      <div className="actions">
        {showRetry && (
          <button type="button" className="btn btn-primary" onClick={onRetry}>
            Try again
          </button>
        )}
        <button type="button" className={showRetry ? "btn btn-secondary" : "btn btn-primary"} onClick={onManual}>
          {error.during === "extraction" ? "Enter claim manually" : "Edit claim"}
        </button>
        <button type="button" className="btn btn-ghost" onClick={onReset}>
          Start over
        </button>
      </div>
      <p className="meta code">Error code: {error.code}</p>
    </section>
  );
}
