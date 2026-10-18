// LIVE browser test on a real youtube.com page. Nothing is mocked.
//
// Prerequisites: npm run build; real backend on 127.0.0.1:8000 with GEMINI_API_KEY;
// network access to youtube.com from this machine.
//
//   node e2e/live.mjs --url "https://www.youtube.com/watch?v=VIDEO_ID" --at 755 [--headed] [--out dir]
//
// Seeks the real player to --at, pauses it, clicks "Verify claim" in the side
// panel page, prints the extracted claim, submits it unedited, and prints the
// verdict and the source URLs. Screenshots are saved to --out (default: e2e-live-out).
import { mkdirSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const arg = (name, fallback = null) => {
  const i = process.argv.indexOf(name);
  return i > 0 ? process.argv[i + 1] : fallback;
};
const url = arg("--url");
const at = Number(arg("--at"));
const outDir = resolve(arg("--out", "e2e-live-out"));
const headed = process.argv.includes("--headed");
if (!url || !Number.isFinite(at)) {
  console.error('usage: node e2e/live.mjs --url "https://www.youtube.com/watch?v=ID" --at SECONDS [--headed]');
  process.exit(2);
}

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const dist = join(root, "dist");
mkdirSync(outDir, { recursive: true });

const context = await chromium.launchPersistentContext(mkdtempSync(join(tmpdir(), "youverify-live-")), {
  executablePath: process.env.CHROMIUM_PATH ?? "/opt/pw-browsers/chromium",
  headless: !headed,
  args: [`--disable-extensions-except=${dist}`, `--load-extension=${dist}`, "--autoplay-policy=no-user-gesture-required"],
});

let exitCode = 0;
try {
  const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent("serviceworker"));
  const extensionId = new URL(worker.url()).host;

  const yt = await context.newPage();
  await yt.goto(url, { waitUntil: "domcontentloaded" });
  // Consent interstitials (EU) block the player; accept if present.
  const consent = yt.getByRole("button", { name: /accept all|reject all|принять все/i }).first();
  if (await consent.isVisible({ timeout: 5000 }).catch(() => false)) await consent.click();

  await yt.waitForSelector("#movie_player video", { timeout: 30000 });
  // Wait out pre-roll ads, then seek and pause on the real player.
  await yt.waitForFunction(() => !document.querySelector("#movie_player")?.classList.contains("ad-showing"), null, { timeout: 120000 });
  await yt.evaluate(async (t) => {
    const v = document.querySelector("#movie_player video");
    v.currentTime = t;
    await new Promise((r) => v.addEventListener("seeked", r, { once: true }));
    v.pause();
  }, at);
  console.log(`player at ${await yt.evaluate(() => document.querySelector("#movie_player video").currentTime)} s`);

  const panel = await context.newPage();
  await panel.setViewportSize({ width: 380, height: 900 });
  await panel.goto(`chrome-extension://${extensionId}/sidepanel.html`);
  await yt.bringToFront();

  const verify = panel.getByRole("button", { name: /verify claim at/i });
  await verify.waitFor({ timeout: 20000 });
  console.log(`panel button: "${await verify.textContent()}"`);
  const t0 = Date.now();
  await verify.click();

  const textarea = panel.getByRole("textbox", { name: /claim/i });
  const failure = panel.getByRole("alert");
  await Promise.race([textarea.waitFor({ timeout: 180000 }), failure.waitFor({ timeout: 180000 })]);
  await panel.screenshot({ path: join(outDir, "1-extraction.png"), fullPage: true });
  if (await failure.isVisible()) {
    console.log(`EXTRACTION FAILED (${Date.now() - t0} ms): ${await failure.innerText()}`);
    exitCode = 1;
  } else {
    console.log(`extracted in ${Date.now() - t0} ms: ${JSON.stringify(await textarea.inputValue())}`);
    const t1 = Date.now();
    await panel.getByRole("button", { name: /check this claim/i }).click();
    const verdict = panel.locator("#verdict-title");
    await Promise.race([verdict.waitFor({ timeout: 300000 }), failure.waitFor({ timeout: 300000 })]);
    await panel.screenshot({ path: join(outDir, "2-verdict.png"), fullPage: true });
    if (await failure.isVisible()) {
      console.log(`VERIFICATION FAILED (${Date.now() - t1} ms): ${await failure.innerText()}`);
      exitCode = 1;
    } else {
      console.log(`verdict in ${Date.now() - t1} ms: ${await verdict.textContent()}`);
      const links = await panel.locator(".sources a.source-title").evaluateAll((els) => els.map((a) => a.href));
      console.log(`sources (${links.length}):\n  ${links.join("\n  ")}`);
    }
  }
  console.log(`screenshots in ${outDir}`);
} catch (err) {
  console.error("LIVE E2E ERROR:", err);
  exitCode = 1;
} finally {
  await context.close();
}
process.exit(exitCode);
