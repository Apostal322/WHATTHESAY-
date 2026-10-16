// Service worker: opens the side panel from the toolbar button and brokers
// video-context requests from the side panel to the active tab's content script.
import { isExtensionMessage, type GetVideoContextMessage } from "../shared/messages";
import type { VideoContext } from "../shared/types";

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch((err: unknown) => {
    console.error("YouVerify: failed to set side panel behavior", err);
  });
});

export async function getActiveVideoContext(): Promise<VideoContext> {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  // tab.url is only visible for hosts in host_permissions (youtube.com), by design.
  if (!tab?.id || !tab.url) return { status: "unsupported", reason: "not_youtube" };

  let url: URL;
  try {
    url = new URL(tab.url);
  } catch {
    return { status: "unsupported", reason: "not_youtube" };
  }
  if (url.hostname !== "www.youtube.com") return { status: "unsupported", reason: "not_youtube" };

  try {
    const msg: GetVideoContextMessage = { type: "GET_VIDEO_CONTEXT" };
    const response = (await chrome.tabs.sendMessage(tab.id, msg)) as VideoContext | undefined;
    return response ?? { status: "unsupported", reason: "content_script_unavailable" };
  } catch {
    // Tab was open before the extension was installed/reloaded.
    return { status: "unsupported", reason: "content_script_unavailable" };
  }
}

chrome.runtime.onMessage.addListener((message: unknown, _sender, sendResponse) => {
  if (isExtensionMessage(message) && message.type === "GET_ACTIVE_VIDEO_CONTEXT") {
    void getActiveVideoContext().then(sendResponse);
    return true; // async response
  }
  return false;
});
