import type { Phase } from "../shared/state";
import type { VerifyStage } from "../shared/types";

const STEPS = ["Extract", "Confirm", "Search", "Evaluate"] as const;

function activeIndex(phase: Phase): number {
  switch (phase) {
    case "extracting":
      return 0;
    case "claim_ready":
      return 1;
    case "searching":
      return 2;
    case "evaluating":
      return 3;
    case "complete":
      return 4;
    default:
      return -1;
  }
}

const STAGE_TEXT: Record<VerifyStage, string> = {
  analyzing: "Analyzing the claim…",
  searching: "Searching for evidence…",
  evaluating: "Evaluating evidence…",
};

export function ProgressSteps({ phase, stage, manual }: { phase: Phase; stage: VerifyStage | null; manual: boolean }) {
  const current = activeIndex(phase);
  const busy = phase === "extracting" || phase === "searching" || phase === "evaluating";
  const text = phase === "extracting" ? "Finding the statement in the video…" : stage ? STAGE_TEXT[stage] : null;

  return (
    <div className="progress">
      <ol className="steps" aria-label="Verification progress">
        {STEPS.map((label, i) => {
          const skipped = manual && i === 0;
          const state = skipped ? "skipped" : i < current ? "done" : i === current ? "current" : "todo";
          return (
            <li key={label} className={`step step-${state}`} aria-current={state === "current" ? "step" : undefined}>
              {label}
            </li>
          );
        })}
      </ol>
      {busy && text && (
        <p className="progress-text" role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" />
          {text}
        </p>
      )}
    </div>
  );
}
