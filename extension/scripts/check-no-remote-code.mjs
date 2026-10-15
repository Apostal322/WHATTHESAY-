// Chrome Web Store (MV3): all executable code must ship in the package. Fails the build check if
// the built extension loads scripts from the network or evaluates strings as code.
// Usage (after `npm run build`): node scripts/check-no-remote-code.mjs
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const dist = new URL("../dist/", import.meta.url).pathname;
const files = [];
(function walk(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path);
    else if (/\.(js|html|json)$/.test(name) && !name.endsWith(".map")) files.push(path);
  }
})(dist);

const RULES = [
  [/<script[^>]*\bsrc=["']https?:/i, "remote <script src>"],
  [/\bimport\s*\(\s*["']https?:/, "dynamic import from a URL"],
  [/\bimport\s+[^"'\n]*from\s*["']https?:/, "static import from a URL"],
  [/\bimportScripts\s*\(/, "importScripts"],
  [/\beval\s*\(/, "eval()"],
  [/\bnew\s+Function\s*\(/, "new Function()"],
];

const problems = [];
for (const file of files) {
  const text = readFileSync(file, "utf8");
  for (const [re, what] of RULES) if (re.test(text)) problems.push(`${file.slice(dist.length)}: ${what}`);
}
const manifest = JSON.parse(readFileSync(join(dist, "manifest.json"), "utf8"));
const csp = JSON.stringify(manifest.content_security_policy ?? {});
if (/https?:|unsafe-eval/.test(csp)) problems.push("manifest.json: CSP allows remote or eval'd code");

for (const p of problems) console.error(p);
console.log(`remote-code check: ${files.length} files, ${problems.length} problem(s)`);
process.exit(problems.length ? 1 : 0);
