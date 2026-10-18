import { useCallback, useEffect, useState } from "react";
import type { ApiClient } from "../shared/api";
import { ApiError } from "../shared/api";
import type { VideoContext } from "../shared/types";
import type { VideoContextSource } from "./videoSource";

const POLL_MS = 2000;

/** Tracks the active tab's video. Polls lightly so the shown position stays roughly current. */
export function useVideoContext(source: VideoContextSource): { ctx: VideoContext | null; refresh: () => Promise<VideoContext> } {
  const [ctx, setCtx] = useState<VideoContext | null>(null);

  const refresh = useCallback(async () => {
    const next = await source.get();
    setCtx(next);
    return next;
  }, [source]);

  useEffect(() => {
    let alive = true;
    const update = () => {
      void source.get().then((next) => alive && setCtx(next));
    };
    update();
    const unsubscribe = source.subscribe(update);
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") update();
    }, POLL_MS);
    return () => {
      alive = false;
      unsubscribe();
      clearInterval(timer);
    };
  }, [source]);

  return { ctx, refresh };
}

export type BackendStatus =
  | { kind: "checking" }
  | { kind: "ok"; model: string }
  | { kind: "no_key" }
  | { kind: "offline"; message: string };

const HEALTH_RECHECK_MS = 10_000;

/** Health check on mount, re-checked periodically until the backend is fully ready. */
export function useBackendStatus(api: ApiClient): BackendStatus {
  const [status, setStatus] = useState<BackendStatus>({ kind: "checking" });
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const check = () => {
      api
        .health()
        .then((h): BackendStatus => (h.gemini_configured ? { kind: "ok", model: h.verification_model } : { kind: "no_key" }))
        .catch((err: unknown): BackendStatus => ({
          kind: "offline",
          message: err instanceof ApiError ? err.message : "Backend unreachable.",
        }))
        .then((next) => {
          if (!alive) return;
          setStatus(next);
          if (next.kind !== "ok") timer = setTimeout(check, HEALTH_RECHECK_MS);
        });
    };
    check();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [api]);
  return status;
}
