/// <reference types="vitest/config" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL(".", import.meta.url));
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

// Copies manifest.json into dist so `dist/` is a loadable unpacked extension.
function manifestPlugin(): Plugin {
  return {
    name: "youverify-manifest",
    generateBundle() {
      this.emitFile({
        type: "asset",
        fileName: "manifest.json",
        source: readFileSync(resolve(root, "manifest.json"), "utf8"),
      });
    },
  };
}

// Builds the side panel (HTML + React) and the module service worker.
// The content script is built separately (vite.content.config.ts) because
// content scripts cannot be ES modules and must be a single classic file.
export default defineConfig({
  plugins: [react(), manifestPlugin()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
    target: "chrome116",
    rollupOptions: {
      input: {
        sidepanel: resolve(root, "sidepanel.html"),
        background: resolve(root, "src/background/index.ts"),
      },
      output: {
        entryFileNames: (chunk) => (chunk.name === "background" ? "background.js" : "assets/[name]-[hash].js"),
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./test/setup.ts"],
    include: ["test/**/*.test.{ts,tsx}"],
  },
});
