import { BACKEND_URL, EXTRACT_TIMEOUT_MS, HEALTH_TIMEOUT_MS, VERIFY_TIMEOUT_MS } from "./config";
import type {
  ErrorBody,
  ExtractRequest,
  ExtractResponse,
  HealthResponse,
  VerifyRequest,
  VerifyResponse,
  VerifyStage,
} from "./types";

export class ApiError extends Error {
  readonly code: string;
  readonly retryable: boolean;

  constructor(body: ErrorBody) {
    super(body.message);
    this.name = "ApiError";
    this.code = body.code;
    this.retryable = body.retryable;
  }
}

export interface ApiClient {
  health(): Promise<HealthResponse>;
  extractClaim(req: ExtractRequest, signal?: AbortSignal): Promise<ExtractResponse>;
  verifyClaim(
    req: VerifyRequest,
    onStage: (stage: VerifyStage) => void,
    signal?: AbortSignal,
  ): Promise<VerifyResponse>;
}

function isErrorBody(value: unknown): value is ErrorBody {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as ErrorBody).code === "string" &&
    typeof (value as ErrorBody).message === "string"
  );
}

/** Combine a caller's abort signal with a timeout. */
function withTimeout(timeoutMs: number, outer?: AbortSignal): { signal: AbortSignal; cancel: () => void } {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new DOMException("timeout", "TimeoutError")), timeoutMs);
  const onAbort = () => controller.abort(outer?.reason);
  outer?.addEventListener("abort", onAbort, { once: true });
  return {
    signal: controller.signal,
    cancel: () => {
      clearTimeout(timer);
      outer?.removeEventListener("abort", onAbort);
    },
  };
}

function toApiError(err: unknown, baseUrl: string): ApiError {
  if (err instanceof ApiError) return err;
  // Match on name, not instanceof: abort reasons may come from another realm.
  const name = typeof err === "object" && err !== null && "name" in err ? String(err.name) : "";
  if (name === "AbortError" || name === "TimeoutError") {
    return name === "TimeoutError"
      ? new ApiError({ code: "client_timeout", message: "The request took too long.", retryable: true })
      : new ApiError({ code: "cancelled", message: "Cancelled.", retryable: false });
  }
  return new ApiError({
    code: "backend_unreachable",
    message: `Cannot reach the YouVerify backend at ${baseUrl}. Is it running?`,
    retryable: true,
  });
}

async function readError(resp: Response): Promise<ApiError> {
  try {
    const body: unknown = await resp.json();
    const error = (body as { error?: unknown }).error;
    if (isErrorBody(error)) return new ApiError(error);
  } catch {
    /* fall through */
  }
  return new ApiError({ code: `http_${resp.status}`, message: `Backend error (HTTP ${resp.status}).`, retryable: resp.status >= 500 });
}

type StreamEvent =
  | { event: "stage"; stage: VerifyStage }
  | { event: "result"; data: VerifyResponse }
  | { event: "error"; error: ErrorBody };

/** Parse an NDJSON byte stream, tolerating arbitrary chunk boundaries. */
export async function* parseNdjson(body: ReadableStream<Uint8Array>): AsyncGenerator<unknown> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    let newline: number;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line) yield JSON.parse(line);
    }
    if (done) break;
  }
  if (buffer.trim()) yield JSON.parse(buffer);
}

export function createApiClient(baseUrl: string = BACKEND_URL, fetchImpl: typeof fetch = (...a) => fetch(...a)): ApiClient {
  async function postJson<T>(path: string, payload: unknown, timeoutMs: number, signal?: AbortSignal): Promise<T> {
    const t = withTimeout(timeoutMs, signal);
    try {
      const resp = await fetchImpl(`${baseUrl}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: t.signal,
      });
      if (!resp.ok) throw await readError(resp);
      return (await resp.json()) as T;
    } catch (err) {
      throw toApiError(t.signal.aborted ? (t.signal.reason ?? err) : err, baseUrl);
    } finally {
      t.cancel();
    }
  }

  return {
    async health() {
      const t = withTimeout(HEALTH_TIMEOUT_MS);
      try {
        const resp = await fetchImpl(`${baseUrl}/health`, { signal: t.signal });
        if (!resp.ok) throw await readError(resp);
        return (await resp.json()) as HealthResponse;
      } catch (err) {
        throw toApiError(err, baseUrl);
      } finally {
        t.cancel();
      }
    },

    extractClaim(req, signal) {
      return postJson<ExtractResponse>("/v1/claims/extract", req, EXTRACT_TIMEOUT_MS, signal);
    },

    async verifyClaim(req, onStage, signal) {
      const t = withTimeout(VERIFY_TIMEOUT_MS, signal);
      try {
        const resp = await fetchImpl(`${baseUrl}/v1/claims/verify/stream`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/x-ndjson" },
          body: JSON.stringify(req),
          signal: t.signal,
        });
        if (!resp.ok) throw await readError(resp);
        if (!resp.body) throw new ApiError({ code: "bad_response", message: "Empty response from backend.", retryable: true });

        for await (const raw of parseNdjson(resp.body)) {
          const evt = raw as StreamEvent;
          if (evt.event === "stage") onStage(evt.stage);
          else if (evt.event === "result") return evt.data;
          else if (evt.event === "error") throw new ApiError(evt.error);
        }
        throw new ApiError({ code: "bad_response", message: "The verification stream ended without a result.", retryable: true });
      } catch (err) {
        throw toApiError(t.signal.aborted ? (t.signal.reason ?? err) : err, baseUrl);
      } finally {
        t.cancel();
      }
    },
  };
}
