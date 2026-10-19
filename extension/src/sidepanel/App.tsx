import { useCallback, useReducer, useRef, useState } from "react";
import { OriginTag, OtherVideoCheck } from "../components/CheckOrigin";
import { ClaimEditor } from "../components/ClaimEditor";
import { ErrorPanel } from "../components/ErrorPanel";
import { Header } from "../components/Header";
import { ProgressSteps } from "../components/ProgressSteps";
import { SourceList } from "../components/SourceList";
import { VerdictCard } from "../components/VerdictCard";
import { VideoBar } from "../components/VideoBar";
import { ApiError, type ApiClient } from "../shared/api";
import { formatTimestamp } from "../shared/format";
import { initialState, reducer, type ClaimOrigin, type State, type UiError, type VideoRef } from "../shared/state";
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

  // One earlier finished check, so starting a new one never loses it.
  const [previous, setPrevious] = useState<State | null>(null);
  const keepIfFinished = () => {
    if (state.phase === "complete") setPrevious(state);
  };

  const extract = useCallback(
    async (video: VideoRef) => {
      const controller = newController();
      dispatch({ type: "EXTRACT_START", ...video });
      try {
        const response = await api.extractClaim(
          {
            video_id: video.videoId,
            timestamp_sec: video.timestampSec,
            duration_sec: video.durationSec ?? null,
            language,
          },
          controller.signal,
        );
        if (!superseded(controller)) dispatch({ type: "EXTRACT_DONE", response });
      } catch (err) {
        if (!superseded(controller)) dispatch({ type: "FAIL", error: toUiError(err, "extraction") });
      }
    },
    [api, language],
  );

  // Set synchronously on click: a second click while the position is still being read must
  // not start a second extraction (each one is a paid video model call).
  const startingRef = useRef(false);
  const startFromVideo = useCallback(async () => {
    if (startingRef.current) return;
    startingRef.current = true;
    try {
      // Re-read at click time so the timestamp is exactly where the user is now.
      const fresh = await refresh();
      if (fresh.status !== "ok") return;
      keepIfFinished();
      await extract({
        videoId: fresh.videoId,
        timestampSec: fresh.timestampSec,
        durationSec: fresh.durationSec,
        title: fresh.title,
        publishedDate: fresh.publishedDate,
      });
    } finally {
      startingRef.current = false;
    }
  }, [extract, refresh, state]);

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
          statement_date: state.origin?.source === "video" ? state.origin.statementDate : null,
          video_published_date: state.origin?.source === "video" ? state.origin.videoPublishedDate : null,
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

  const manual = () => {
    keepIfFinished();
    dispatch({
      type: "MANUAL_ENTRY",
      videoId: ctx?.status === "ok" ? ctx.videoId : null,
      timestampSec: ctx?.status === "ok" ? ctx.timestampSec : null,
    });
  };

  const retry = () => {
    const o = state.origin;
    if (state.error?.during === "extraction" && o?.videoId != null && o.timestampSec != null) {
      // Everything from the check's own video, never from whatever is playing now.
      void extract({
        videoId: o.videoId,
        timestampSec: o.timestampSec,
        durationSec: o.durationSec,
        title: o.videoTitle,
        publishedDate: o.videoPublishedDate,
      });
    } else {
      void verify();
    }
  };

  const reset = () => {
    keepIfFinished();
    abortRef.current?.abort();
    abortRef.current = null;
    dispatch({ type: "RESET" });
  };

  const showPrevious = () => {
    if (!previous) return;
    abortRef.current?.abort();
    abortRef.current = null;
    setPrevious(state.phase === "complete" ? state : null);
    dispatch({ type: "RESTORE", snapshot: previous });
  };

  const videoReady = ctx?.status === "ok";
  const isManual = state.origin?.source === "manual";
  // A check stays bound to the video it came from. While another video is playing, a finished
  // or editable check is held back (not shown as if about this video) until the user asks.
  const origin = state.origin;
  const bound = origin?.source === "video" && origin.videoId != null;
  const elsewhere = bound && ctx?.status === "ok" && origin.videoId !== ctx.videoId;
  // The origin object is the check's identity: the same through its phases, new for a new check.
  const [shownOrigin, setShownOrigin] = useState<ClaimOrigin | null>(null);
  const held = elsewhere && shownOrigin !== origin && ["claim_ready", "complete", "error"].includes(state.phase);
  const visible = (phase: State["phase"]) => state.phase === phase && !held;

  return (
    <div className="app">
      <Header status={backend} language={language} onLanguageChange={setLanguage} />
      <main className="main">
        <VideoBar ctx={ctx} />
        {bound && state.phase !== "idle" && !held && <OriginTag origin={origin} />}
        {elsewhere && !held && state.phase !== "idle" && (
          <p className="notice" role="status">
            This check is for the video you were watching before, not the one now playing.
          </p>
        )}
        {held && (
          <OtherVideoCheck
            origin={origin}
            done={state.phase === "complete"}
            onShow={() => setShownOrigin(origin)}
            onNewCheck={reset}
          />
        )}
        {/* Only when nothing would be lost: no check running, no draft being edited. */}
        {previous && previous !== state && (state.phase === "idle" || state.phase === "complete") && (
          <button type="button" className="btn btn-link" onClick={showPrevious}>
            Show the previous check
            {previous.origin?.videoTitle ? ` (${previous.origin.videoTitle})` : ""}
          </button>
        )}

        {state.phase !== "idle" && state.phase !== "error" && !held && <ProgressSteps phase={state.phase} stage={state.stage} manual={isManual} />}

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

        {visible("claim_ready") && (
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

        {visible("complete") && state.result && (
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

        {visible("error") && state.error && (
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
