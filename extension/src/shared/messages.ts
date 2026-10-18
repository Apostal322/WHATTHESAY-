import type { VideoContext } from "./types";

// Side panel -> background
export interface GetActiveVideoContextMessage {
  type: "GET_ACTIVE_VIDEO_CONTEXT";
}

// Background -> content script
export interface GetVideoContextMessage {
  type: "GET_VIDEO_CONTEXT";
}

// Content script -> extension pages (broadcast on YouTube SPA navigation)
export interface VideoContextChangedMessage {
  type: "VIDEO_CONTEXT_CHANGED";
}

export type ExtensionMessage = GetActiveVideoContextMessage | GetVideoContextMessage | VideoContextChangedMessage;

export type VideoContextResponse = VideoContext;

export function isExtensionMessage(value: unknown): value is ExtensionMessage {
  if (typeof value !== "object" || value === null) return false;
  const type = (value as { type?: unknown }).type;
  return type === "GET_ACTIVE_VIDEO_CONTEXT" || type === "GET_VIDEO_CONTEXT" || type === "VIDEO_CONTEXT_CHANGED";
}
