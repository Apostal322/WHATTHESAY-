import { formatTimestamp } from "../shared/format";
import type { ClaimOrigin } from "../shared/state";

function where(origin: ClaimOrigin): string {
  const title = origin.videoTitle ?? origin.videoId ?? "a video";
  return origin.timestampSec != null ? `${title} · ${formatTimestamp(origin.timestampSec)}` : title;
}

function when(origin: ClaimOrigin): string {
  if (origin.statementDate) return `Said: ${origin.statementDate} (shown in the video)`;
  if (origin.videoPublishedDate) return `Said: date unknown; the video was published ${origin.videoPublishedDate}`;
  return "Said: date unknown";
}

/** Which video (and moment) this claim was taken from, shown on every step of the check. */
export function OriginTag({ origin }: { origin: ClaimOrigin }) {
  return (
    <div className="origin-tag" data-testid="claim-origin">
      <span className="origin-label">Claim from</span> <span className="origin-where">{where(origin)}</span>
      <span className="meta"> · {when(origin)}</span>
    </div>
  );
}

interface ElsewhereProps {
  origin: ClaimOrigin;
  done: boolean;
  onShow: () => void;
  onNewCheck: () => void;
}

/** A finished (or editable) check for another video: kept, but not shown as if about this one. */
export function OtherVideoCheck({ origin, done, onShow, onNewCheck }: ElsewhereProps) {
  return (
    <section className="card" aria-labelledby="other-video-title">
      <h2 id="other-video-title" className="card-title">
        {done ? "This result is for another video" : "This claim is from another video"}
      </h2>
      <p className="meta">
        {where(origin)}. The video now playing is a different one, so the {done ? "verdict" : "claim"} is hidden here.
      </p>
      <div className="actions">
        <button type="button" className="btn btn-secondary" onClick={onShow}>
          Show it (labelled with its video)
        </button>
        <button type="button" className="btn btn-ghost" onClick={onNewCheck}>
          New check for this video
        </button>
      </div>
    </section>
  );
}
