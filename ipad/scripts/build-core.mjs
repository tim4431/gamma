// Bundle ipad/core/entry.js — the web app's ink, notebook and replica
// modules — into one script for the app's JavaScriptCore:
// GammaIPad/Resources/gamma-core.js (generated; not in git).
//
//   node ipad/scripts/build-core.mjs            # from the repository root
//
// esbuild comes with the frontend's dependencies (`npm ci` in frontend/).
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const require = createRequire(path.join(ROOT, "frontend", "package.json"));
const { build } = require("esbuild");

const out = path.join(ROOT, "ipad", "GammaIPad", "Resources", "gamma-core.js");
await build({
  entryPoints: [path.join(ROOT, "ipad", "core", "entry.js")],
  bundle: true,
  format: "iife",
  // JavaScriptCore on iPadOS 17: ES2022 (BigInt, optional chaining, ??=, Array#at)
  target: "es2022",
  platform: "neutral",
  mainFields: ["module", "main"],
  nodePaths: [path.join(ROOT, "frontend", "node_modules")],
  outfile: out,
  legalComments: "inline",
  logLevel: "warning",
});
console.log(`wrote ${path.relative(ROOT, out)}`);
