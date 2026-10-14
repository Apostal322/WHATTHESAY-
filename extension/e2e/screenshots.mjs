// README screenshots: the real built extension in Chromium, against the DEMO backend.
//
// Prerequisites:
//   1. npm run build
//   2. cd backend && python -m tests.demo_server   (scripted fake model, synthetic DEMO data)
//
// YouTube is replaced by a local page titled "DEMO"; no real video, model or search is used.
// The UI, API, streaming and the backend's deterministic safeguards are the real code.
//
// Usage: CHROMIUM_PATH=/path/to/chrome node e2e/screenshots.mjs [outDir]   (default ../docs/images)
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const dist = join(root, "dist");
const outDir = resolve(process.argv[2] ?? join(root, "..", "docs", "images"));
const executablePath = process.env.CHROMIUM_PATH ?? "/opt/pw-browsers/chromium";

const PAGE = `<!doctype html><html><head><title>DEMO: Riverton council meeting (synthetic) - YouTube</title></head><body>
<h1 class="ytd-watch-metadata">DEMO: Riverton council meeting (synthetic)</h1>
<div id="movie_player"><video class="html5-main-video"></video></div>
<script>document.querySelector("video").currentTime = 755.3;</script>
</body></html>`;

const context = await chromium.launchPersistentContext(mkdtempSync(join(tmpdir(), "youverify-shots-")), {
  executablePath,
  headless: true,
  args: [`--disable-extensions-except=${dist}`, `--load-extension=${dist}`],
});

try {
  await context.route("https://www.youtube.com/**", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: PAGE }),
  );
  const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent("serviceworker"));
  const extensionId = new URL(worker.url()).host;

  const yt = await context.newPage();
  await yt.goto("https://www.youtube.com/watch?v=dQw4w9WgXcQ");
  const panel = await context.newPage();
  await panel.setViewportSize({ width: 380, height: 760 });
  await panel.goto(`chrome-extension://${extensionId}/sidepanel.html`);
  await yt.bringToFront();

  // 1. Claim extracted from the video, shown for confirmation.
  const verifyBtn = panel.getByRole("button", { name: /verify claim at 12:35/i });
  await verifyBtn.waitFor({ timeout: 15000 });
  await panel.getByText("Ready").waitFor();
  await verifyBtn.click();
  await panel.getByRole("textbox", { name: /claim/i }).waitFor({ timeout: 15000 });
  await panel.screenshot({ path: join(outDir, "demo-1-confirm.png") });

  // 2. The model says "supported" from an outlet's characterization; the safeguard overrides it.
  await panel.getByRole("button", { name: /check this claim/i }).click();
  await panel.getByRole("heading", { name: "Insufficient evidence" }).waitFor({ timeout: 20000 });
  await panel.getByRole("note", { name: "Safeguard applied" }).waitFor();
  await panel.screenshot({ path: join(outDir, "demo-2-safeguard.png"), fullPage: true });

  // 3. A factual claim backed by two independent sources.
  await panel.getByRole("button", { name: /new check/i }).click();
  await panel.getByRole("button", { name: /type a claim manually/i }).click();
  await panel.getByRole("textbox", { name: /claim/i }).fill("The Riverton river bridge opened to traffic in 2019.");
  await panel.getByRole("button", { name: /check this claim/i }).click();
  await panel.getByRole("heading", { name: "Supported" }).waitFor({ timeout: 20000 });
  await panel.screenshot({ path: join(outDir, "demo-3-supported.png"), fullPage: true });

  console.log(`screenshots written to ${outDir}`);
} finally {
  await context.close();
}
