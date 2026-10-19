import type { VideoContext } from "../shared/types";

const VIDEO_ID_RE = /^[A-Za-z0-9_-]{11}$/;

/**
 * Read the current video's identity and playback position from the page.
 * Pure with respect to its inputs so it can be tested in jsdom.
 */
export function readVideoContext(loc: Pick<Location, "hostname" | "pathname" | "search">, doc: Document): VideoContext {
  if (loc.hostname !== "www.youtube.com") return { status: "unsupported", reason: "not_youtube" };
  if (loc.pathname.startsWith("/shorts/")) return { status: "unsupported", reason: "shorts" };
  if (loc.pathname !== "/watch") return { status: "unsupported", reason: "not_watch_page" };

  const videoId = new URLSearchParams(loc.search).get("v");
  if (!videoId || !VIDEO_ID_RE.test(videoId)) return { status: "unsupported", reason: "not_watch_page" };

  const player = doc.querySelector<HTMLElement>("#movie_player");
  if (player?.classList.contains("ad-showing")) return { status: "unsupported", reason: "ad_playing" };

  const video = doc.querySelector<HTMLVideoElement>("#movie_player video, video.html5-main-video");
  if (!video || !Number.isFinite(video.currentTime)) return { status: "unsupported", reason: "no_video" };

  // Live streams report an infinite duration.
  if (video.duration === Number.POSITIVE_INFINITY) return { status: "unsupported", reason: "live" };

  const duration = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : null;
  return {
    status: "ok",
    videoId,
    timestampSec: Math.max(0, video.currentTime),
    durationSec: duration,
    title: readTitle(doc),
    publishedDate: readPublishedDate(doc, videoId),
  };
}

/**
 * The upload date from the watch page's microdata. YouTube does not always replace that markup
 * on in-app navigation, so it is used only when its identifier names this video.
 */
function readPublishedDate(doc: Document, videoId: string): string | null {
  const meta = (prop: string) => doc.querySelector<HTMLMetaElement>(`meta[itemprop="${prop}"]`)?.content ?? "";
  if (meta("identifier") !== videoId) return null;
  const date = /^(\d{4}-\d{2}-\d{2})(?:$|T)/.exec(meta("datePublished") || meta("uploadDate"));
  return date?.[1] ?? null;
}

function readTitle(doc: Document): string | null {
  const heading = doc.querySelector("h1.ytd-watch-metadata, h1.title")?.textContent?.trim();
  if (heading) return heading.slice(0, 200);
  const title = doc.title.replace(/\s*-\s*YouTube\s*$/, "").trim();
  return title ? title.slice(0, 200) : null;
}
