// Backend base URL. Override at build time: VITE_BACKEND_URL=http://127.0.0.1:9000 npm run build
// (and add the origin to host_permissions in manifest.json).
export const BACKEND_URL: string = (import.meta.env.VITE_BACKEND_URL as string | undefined) ?? "http://127.0.0.1:8000";

export const EXTRACT_TIMEOUT_MS = 150_000;
export const VERIFY_TIMEOUT_MS = 300_000;
export const HEALTH_TIMEOUT_MS = 4_000;
