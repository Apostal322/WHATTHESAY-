// Content script for youtube.com. Read-only: it never modifies the page or
// controls playback. It answers context requests and announces SPA navigation.
import { isExtensionMessage, type VideoContextChangedMessage } from "../shared/messages";
import { readVideoContext } from "./videoContext";

chrome.runtime.onMessage.addListener((message: unknown, _sender, sendResponse) => {
  if (isExtensionMessage(message) && message.type === "GET_VIDEO_CONTEXT") {
    sendResponse(readVideoContext(window.location, document));
  }
  return false;
});

function announceChange(): void {
  const msg: VideoContextChangedMessage = { type: "VIDEO_CONTEXT_CHANGED" };
  // Rejects when no extension page is listening (side panel closed); that is fine.
  chrome.runtime.sendMessage(msg).catch(() => undefined);
}

// YouTube is a single-page app: navigation does not reload content scripts.
// `yt-navigate-finish` is dispatched by YouTube after each in-app navigation;
// popstate covers back/forward. Both are observed passively.
document.addEventListener("yt-navigate-finish", announceChange);
window.addEventListener("popstate", announceChange);
