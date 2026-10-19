// End-to-end smoke test: real built extension in Chromium + real backend.
//
// Prerequisites:
//   1. npm run build
//   2. Backend on 127.0.0.1:8000 — for a key-less run use the scripted fixture:
//        cd backend && python -m tests.e2e_server
//
// YouTube is replaced by a local fixture page (request interception), so the
// content script, background messaging and side panel all run for real, but no
// real video is involved. Live YouTube + Gemini must be tested by hand (docs/development.md).
//
// Usage: CHROMIUM_PATH=/path/to/chrome node e2e/smoke.mjs [--screenshots <dir>]
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const dist = join(root, "dist");
const shotArg = process.argv.indexOf("--screenshots");
const shotDir = shotArg > 0 ? process.argv[shotArg + 1] : null;
const executablePath = process.env.CHROMIUM_PATH ?? "/opt/pw-browsers/chromium";

const FIXTURE = `<!doctype html><html><head><title>E2E Fixture Video - YouTube</title></head><body>
<h1 class="ytd-watch-metadata">E2E Fixture Video</h1>
<div id="movie_player"><video class="html5-main-video"></video></div>
<script>document.querySelector("video").currentTime = 755.3;</script>
</body></html>`;

function check(cond, msg) {
  if (!cond) throw new Error(`ASSERTION FAILED: ${msg}`);
  console.log(`  ok - ${msg}`);
}

const context = await chromium.launchPersistentContext(mkdtempSync(join(tmpdir(), "youverify-e2e-")), {
  executablePath,
  headless: true,
  args: [`--disable-extensions-except=${dist}`, `--load-extension=${dist}`],
});

try {
  await context.route("https://www.youtube.com/**", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: FIXTURE }),
  );

  const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent("serviceworker"));
  const extensionId = new URL(worker.url()).host;
  check(/^[a-p]{32}$/.test(extensionId), `extension loaded (id ${extensionId})`);

  const yt = await context.newPage();
  await yt.goto("https://www.youtube.com/watch?v=dQw4w9WgXcQ");

  const panel = await context.newPage();
  await panel.setViewportSize({ width: 380, height: 760 });
  await panel.goto(`chrome-extension://${extensionId}/sidepanel.html`);
  await yt.bringToFront(); // make the YouTube tab the active tab, like a real side panel

  // Content script -> background -> side panel
  const verifyBtn = panel.getByRole("button", { name: /verify claim at 12:35/i });
  await verifyBtn.waitFor({ timeout: 15000 });
  check(true, "side panel reads video id + timestamp 12:35 via content script");
  await panel.getByText("E2E Fixture Video").waitFor();
  check(true, "video title shown");
  await panel.getByText("Ready").waitFor();
  check(true, "backend health reported Ready (CORS from extension origin works)");

  // Extraction
  await verifyBtn.click();
  const textarea = panel.getByRole("textbox", { name: /claim/i });
  await textarea.waitFor({ timeout: 15000 });
  check((await textarea.inputValue()).includes("80%"), "extracted claim shown in editable field");
  check(await panel.getByText(/paraphrase of what was said/i).isVisible(), "paraphrase labelled as paraphrase");
  if (shotDir) await panel.screenshot({ path: join(shotDir, "claim-confirmation.png") });

  // Edit + verify (streamed stages)
  await textarea.fill("AI has already replaced 80% of software engineers in the US.");
  await panel.getByRole("button", { name: /check this claim/i }).click();
  await panel.getByText(/searching for evidence|analyzing the claim/i).first().waitFor({ timeout: 5000 });
  check(true, "streamed progress stage displayed");
  await panel.getByRole("heading", { name: "Contradicted" }).waitFor({ timeout: 20000 });
  check(true, "verdict displayed");

  const links = await panel.locator(".sources a.source-title").evaluateAll((els) => els.map((a) => a.getAttribute("href")));
  check(
    links.length === 2 && links.every((h) => h.startsWith("https://")),
    `only safe, retrieved sources are linked (${links.join(", ")})`,
  );
  check(!(await panel.getByText("[S9]").count()), "unknown citation [S9] stripped");
  check((await panel.locator("a.cite").first().textContent()) === "S1", "known citation rendered as link");
  if (shotDir) await panel.screenshot({ path: join(shotDir, "verdict.png"), fullPage: true });

  // SPA navigation: content script announces, panel refreshes
  await yt.evaluate(() => {
    history.pushState({}, "", "/watch?v=aaaaaaaaaaa");
    document.querySelector("h1").textContent = "Second Video";
    document.dispatchEvent(new Event("yt-navigate-finish"));
  });
  await panel.getByText("Second Video").waitFor({ timeout: 5000 });
  check(true, "SPA navigation picked up");
  // The verdict belongs to the first video: held back, not shown under the second one.
  await panel.getByText("This result is for another video").waitFor({ timeout: 5000 });
  check(
    (await panel.getByRole("heading", { name: "Contradicted" }).count()) === 0,
    "verdict for the previous video held back after navigation",
  );

  // MV3: Chrome may stop the service worker at any time. Stop it, then require the panel to
  // read the video context again (that request goes through a freshly started worker).
  const cdp = await context.newCDPSession(panel);
  const statuses = [];
  let versionId = null;
  cdp.on("ServiceWorker.workerVersionUpdated", (e) => {
    for (const v of e.versions) {
      statuses.push(v.runningStatus);
      if (v.runningStatus === "running") versionId = v.versionId;
    }
  });
  await cdp.send("ServiceWorker.enable");
  for (let i = 0; i < 50 && versionId === null; i++) await new Promise((r) => setTimeout(r, 100));
  await cdp.send("ServiceWorker.stopWorker", { versionId });
  for (let i = 0; i < 50 && !statuses.includes("stopped"); i++) await new Promise((r) => setTimeout(r, 100));
  check(statuses.includes("stopped"), "service worker stopped");
  await yt.evaluate(() => {
    history.pushState({}, "", "/watch?v=bbbbbbbbbbb");
    document.querySelector("h1").textContent = "Third Video";
    document.dispatchEvent(new Event("yt-navigate-finish"));
  });
  await panel.getByText("Third Video").waitFor({ timeout: 8000 });
  check(true, "video context still served after the service worker was stopped");

  console.log("E2E SMOKE PASSED");
} finally {
  await context.close();
}
