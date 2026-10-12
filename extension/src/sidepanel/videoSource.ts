import { isExtensionMessage, type GetActiveVideoContextMessage } from "../shared/messages";
import type { VideoContext } from "../shared/types";

/** Abstraction over Chrome messaging so the UI can be tested without `chrome`. */
export interface VideoContextSource {
  get(): Promise<VideoContext>;
  /** Called when the active tab or its YouTube page changes. Returns an unsubscribe fn. */
  subscribe(onChange: () => void): () => void;
}

export const chromeVideoSource: VideoContextSource = {
  async get() {
    try {
      const msg: GetActiveVideoContextMessage = { type: "GET_ACTIVE_VIDEO_CONTEXT" };
      const ctx = (await chrome.runtime.sendMessage(msg)) as VideoContext | undefined;
      return ctx ?? { status: "unsupported", reason: "content_script_unavailable" };
    } catch {
      return { status: "unsupported", reason: "content_script_unavailable" };
    }
  },

  subscribe(onChange) {
    const onMessage = (message: unknown) => {
      if (isExtensionMessage(message) && message.type === "VIDEO_CONTEXT_CHANGED") onChange();
    };
    const onActivated = () => onChange();
    const onUpdated = (_id: number, info: chrome.tabs.OnUpdatedInfo) => {
      if (info.status === "complete") onChange();
    };
    chrome.runtime.onMessage.addListener(onMessage);
    chrome.tabs.onActivated.addListener(onActivated);
    chrome.tabs.onUpdated.addListener(onUpdated);
    chrome.windows?.onFocusChanged.addListener(onActivated);
    return () => {
      chrome.runtime.onMessage.removeListener(onMessage);
      chrome.tabs.onActivated.removeListener(onActivated);
      chrome.tabs.onUpdated.removeListener(onUpdated);
      chrome.windows?.onFocusChanged.removeListener(onActivated);
    };
  },
};
