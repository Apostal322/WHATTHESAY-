import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL(".", import.meta.url));
import { defineConfig } from "vite";

export default defineConfig({
  build: {
    outDir: "dist",
    emptyOutDir: false,
    sourcemap: true,
    target: "chrome116",
    lib: {
      entry: resolve(root, "src/content/index.ts"),
      formats: ["iife"],
      name: "YouVerifyContent",
      fileName: () => "content.js",
    },
  },
});
