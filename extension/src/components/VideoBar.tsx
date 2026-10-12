import { formatTimestamp, UNSUPPORTED_MESSAGE } from "../shared/format";
import type { VideoContext } from "../shared/types";

export function VideoBar({ ctx }: { ctx: VideoContext | null }) {
  if (!ctx) {
    return <div className="video-bar muted">Looking for a YouTube video…</div>;
  }
  if (ctx.status !== "ok") {
    return (
      <div className="video-bar muted" data-testid="video-unsupported">
        {UNSUPPORTED_MESSAGE[ctx.reason]}
      </div>
    );
  }
  return (
    <div className="video-bar" data-testid="video-ok">
      <span className="video-title" title={ctx.title ?? undefined}>
        {ctx.title ?? ctx.videoId}
      </span>
      <span className="video-time" aria-label="Current playback position">
        {formatTimestamp(ctx.timestampSec)}
      </span>
    </div>
  );
}
