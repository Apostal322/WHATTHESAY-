import { useCallback, useReducer, useRef, useState } from "react";
import { ClaimEditor } from "../components/ClaimEditor";
import { ErrorPanel } from "../components/ErrorPanel";
import { Header } from "../components/Header";
import { ProgressSteps } from "../components/ProgressSteps";
import { SourceList } from "../components/SourceList";
import { VerdictCard } from "../components/VerdictCard";
import { VideoBar } from "../components/VideoBar";
import { ApiError, type ApiClient } from "../shared/api";
import { formatTimestamp } from "../shared/format";
import { initialState, reducer, type UiError } from "../shared/state";
import type { Language } from "../shared/types";
import { useBackendStatus, useVideoContext } from "./hooks";
import type { VideoContextSource } from "./videoSource";

interface Props {
  api: ApiClient;
  videoSource: VideoContextSource;
}

function toUiError(err: unknown, during: UiError["during"]): UiError {
  if (err instanceof ApiError) return { code: err.code, message: err.message, retryable: err.retryable, during };
  return { code: "internal_error", message: "Something went wrong.", retryable: true, during };
}

export function App({ api, videoSource }: Props) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [language, setLanguage] = useState<Language>("auto");
  const { ctx, refresh } = useVideoContext(videoSource);
  const backend = useBackendStatus(api);
  const abortRef = useRef<AbortController | null>(null);

  const newController = () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    return controller;
  };
  // A request whose controller was replaced (new request or reset) must not touch state.
  const superseded = (controller: AbortController) => abortRef.current !== controller;

  const extract = useCallback(
    async (videoId: string, timestampSec: number, durationSec: number | null) => {
      const controller = newController();
      dispatch({ type: "EXTRACT_START", videoId, timestampSec });
      try {
        const response = await api.extractClaim(
          { video_id: videoId, timestamp_sec: timestampSec, duration_sec: durationSec, language },
          controller.signal,
        );
        if (!superseded(controller)) dispatch({ type: "EXTRACT_DONE", response });
      } catch (err) {
        if (!superseded(controller)) dispatch({ type: "FAIL", error: toUiError(err, "extraction") });
      }
    },
    [api, language],
  );

  const startFromVideo = useCallback(async () => {
    // Re-read at click time so the timestamp is exactly where the user is now.
    const fresh = await refresh();
    if (fresh.status !== "ok") return;
    await extract(fresh.videoId, fresh.timestampSec, fresh.durationSec);
  }, [extract, refresh]);

  const verify = useCallback(async () => {
    const controller = newController();
    dispatch({ type: "VERIFY_START" });
    try {
      const result = await api.verifyClaim(
        {
          claim: state.draftClaim.trim(),
          context: state.origin?.source === "video" ? state.origin.context : null,
          claim_type_hint: state.origin?.source === "video" ? state.origin.claimTypeHint : null,
          video_id: state.origin?.videoId ?? null,
          timestamp_sec: state.origin?.timestampSec ?? null,
          language,
        },
        (stage) => !superseded(controller) && dispatch({ type: "VERIFY_STAGE", stage }),
        controller.signal,
      );
      if (!superseded(controller)) dispatch({ type: "VERIFY_DONE", result });
    } catch (err) {
      if (!superseded(controller)) dispatch({ type: "FAIL", error: toUiError(err, "verification") });
    }
  }, [api, language, state.draftClaim, state.origin]);

  const cancel = () => abortRef.current?.abort();

  const manual = () =>
    dispatch({
      type: "MANUAL_ENTRY",
      videoId: ctx?.status === "ok" ? ctx.videoId : null,
      timestampSec: ctx?.status === "ok" ? ctx.timestampSec : null,
    });

  const retry = () => {
    if (state.error?.during === "extraction" && state.origin?.videoId != null && state.origin.timestampSec != null) {
      void extract(state.origin.videoId, state.origin.timestampSec, ctx?.status === "ok" ? ctx.durationSec : null);
    } else {
      void verify();
    }
  };

  const reset = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    dispatch({ type: "RESET" });
  };

  const videoReady = ctx?.status === "ok";
  const isManual = state.origin?.source === "manual";

  return (
    <div className="app">
      <Header status={backend} language={language} onLanguageChange={setLanguage} />
      <main className="main">
        <VideoBar ctx={ctx} />

        {state.phase !== "idle" && state.phase !== "error" && <ProgressSteps phase={state.phase} stage={state.stage} manual={isManual} />}

        {state.phase === "idle" && (
          <section className="card intro">
            <p>When you hear a statement you want to check, press the button. The video keeps playing.</p>
            <button
              type="button"
              className="btn btn-primary btn-block"
              disabled={!videoReady}
              onClick={() => void startFromVideo()}
            >
              Verify claim{videoReady ? ` at ${formatTimestamp(ctx.timestampSec)}` : ""}
            </button>
            <button type="button" className="btn btn-link" onClick={manual}>
              Or type a claim manually
            </button>
            {backend.kind === "no_key" && (
              <p className="notice">The backend has no GEMINI_API_KEY. Add it to .env and restart the backend.</p>
            )}
            {backend.kind === "offline" && <p className="notice">{backend.message}</p>}
          </section>
        )}

        {state.phase === "extracting" && (
          <section className="card">
            <p className="meta">
              Looking at the video around {state.origin?.timestampSec != null ? formatTimestamp(state.origin.timestampSec) : "this moment"}.
              This can take 10–40 seconds.
            </p>
            <button type="button" className="btn btn-ghost" onClick={cancel}>
              Cancel
            </button>
          </section>
        )}

        {state.phase === "claim_ready" && (
          <ClaimEditor
            value={state.draftClaim}
            extraction={state.extraction}
            notice={state.notice}
            onChange={(text) => dispatch({ type: "EDIT_CLAIM", text })}
            onSubmit={() => void verify()}
            onCancel={reset}
          />
        )}

        {(state.phase === "searching" || state.phase === "evaluating") && (
          <section className="card">
            <p className="claim-quote">“{state.draftClaim.trim()}”</p>
            <button type="button" className="btn btn-ghost" onClick={cancel}>
              Cancel
            </button>
          </section>
        )}

        {state.phase === "complete" && state.result && (
          <>
            <VerdictCard result={state.result} />
            <SourceList sources={state.result.sources} />
            <div className="actions">
              <button type="button" className="btn btn-secondary" onClick={() => dispatch({ type: "EDIT_AGAIN" })}>
                Edit claim
              </button>
              <button type="button" className="btn btn-ghost" onClick={reset}>
                New check
              </button>
            </div>
          </>
        )}

        {state.phase === "error" && state.error && (
          <ErrorPanel
            error={state.error}
            onRetry={retry}
            onManual={() => (state.error?.during === "verification" ? dispatch({ type: "EDIT_AGAIN" }) : manual())}
            onReset={reset}
          />
        )}
      </main>
      <footer className="footer">
        AI-assisted and can be wrong. Read the sources before relying on a verdict.
      </footer>
    </div>
  );
}
