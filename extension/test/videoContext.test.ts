import { beforeEach, describe, expect, it } from "vitest";
import { readVideoContext } from "../src/content/videoContext";

const loc = (path: string, search = "") => ({ hostname: "www.youtube.com", pathname: path, search });

function mountPlayer({ currentTime = 755.4, duration = 1800, ad = false } = {}) {
  document.body.innerHTML = `
    <h1 class="ytd-watch-metadata"> The Future of Coding </h1>
    <div id="movie_player" class="${ad ? "ad-showing" : ""}"><video class="html5-main-video"></video></div>`;
  const video = document.querySelector("video")!;
  Object.defineProperty(video, "currentTime", { value: currentTime, configurable: true });
  Object.defineProperty(video, "duration", { value: duration, configurable: true });
}

describe("readVideoContext", () => {
  beforeEach(() => {
    document.head.innerHTML = "";
    document.body.innerHTML = "";
  });

  it("reads id, time, duration and title on a watch page", () => {
    mountPlayer();
    expect(readVideoContext(loc("/watch", "?v=dQw4w9WgXcQ&t=10s"), document)).toEqual({
      status: "ok",
      videoId: "dQw4w9WgXcQ",
      timestampSec: 755.4,
      durationSec: 1800,
      title: "The Future of Coding",
      publishedDate: null,
    });
  });

  const microdata = (id: string, published: string) => {
    document.head.innerHTML = `<meta itemprop="identifier" content="${id}"><meta itemprop="datePublished" content="${published}">`;
  };

  it.each([
    ["2024-09-10", "2024-09-10"],
    ["2024-09-10T18:00:00-07:00", "2024-09-10"],
    ["10 Sept 2024", null],
  ])("reads the upload date %s from this video's microdata", (published, expected) => {
    mountPlayer();
    microdata("dQw4w9WgXcQ", published);
    const ctx = readVideoContext(loc("/watch", "?v=dQw4w9WgXcQ"), document);
    expect(ctx.status === "ok" && ctx.publishedDate).toBe(expected ?? null);
  });

  it("ignores microdata left over from the previous video after in-app navigation", () => {
    mountPlayer();
    microdata("aaaaaaaaaaa", "2019-01-01");
    const ctx = readVideoContext(loc("/watch", "?v=dQw4w9WgXcQ"), document);
    expect(ctx.status === "ok" && ctx.publishedDate).toBeNull();
  });

  it.each([
    [{ hostname: "example.com", pathname: "/watch", search: "?v=dQw4w9WgXcQ" }, "not_youtube"],
    [loc("/shorts/dQw4w9WgXcQ"), "shorts"],
    [loc("/"), "not_watch_page"],
    [loc("/watch", "?v=bad"), "not_watch_page"],
  ])("rejects unsupported pages (%j)", (where, reason) => {
    mountPlayer();
    expect(readVideoContext(where, document)).toEqual({ status: "unsupported", reason });
  });

  it("reports when an ad is playing", () => {
    mountPlayer({ ad: true });
    expect(readVideoContext(loc("/watch", "?v=dQw4w9WgXcQ"), document)).toEqual({ status: "unsupported", reason: "ad_playing" });
  });

  it("reports live streams", () => {
    mountPlayer({ duration: Number.POSITIVE_INFINITY });
    expect(readVideoContext(loc("/watch", "?v=dQw4w9WgXcQ"), document)).toEqual({ status: "unsupported", reason: "live" });
  });

  it("reports a missing player", () => {
    expect(readVideoContext(loc("/watch", "?v=dQw4w9WgXcQ"), document)).toEqual({ status: "unsupported", reason: "no_video" });
  });
});
