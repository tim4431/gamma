// The design-token ratchet (docs/dev/ui-design.md, "Tokens"): the raw
// colours, font sizes, corner radii, shadows, layers and durations left in
// the stylesheets may only go down. tools/designTokens.mjs counts them;
// tests/design-baseline.json records the counts per file.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { BASELINE, KINDS, counts, declarations, offences, scan } from "../tools/designTokens.mjs";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (p) => fs.readFileSync(path.join(FRONTEND, p), "utf8");

test("the scanner tells raw values from tokens", () => {
  const kinds = (prop, value) => offences({ prop, value });
  assert.deepEqual(kinds("color", "#d9822b"), ["color"]);
  assert.deepEqual(kinds("color", "var(--text-muted)"), []);
  assert.deepEqual(kinds("mask-image", "linear-gradient(#000 70%, transparent)"), [], "a mask is alpha");
  assert.deepEqual(kinds("font-size", "var(--fs-sm)"), []);
  assert.deepEqual(kinds("font-size", "calc(var(--fs-md) * var(--text-scale, 1))"), []);
  assert.deepEqual(kinds("font-size", "0.9em"), []);
  assert.deepEqual(kinds("font-size", "calc(13px * var(--ui-font-scale, 1))"), ["size"]);
  assert.deepEqual(kinds("font", "500 var(--fs-2xs) / 18px var(--font-mono)"), [], "the shorthand's line height is not a size");
  assert.deepEqual(kinds("border-radius", "var(--radius-md) var(--radius-md) 0 0"), []);
  assert.deepEqual(kinds("border-radius", "50%"), []);
  assert.deepEqual(kinds("border-radius", "2px"), [], "a hairline bar");
  assert.deepEqual(kinds("border-radius", "10px"), ["radius"]);
  assert.deepEqual(kinds("box-shadow", "var(--shadow-2)"), []);
  assert.deepEqual(kinds("box-shadow", "0 0 0 3px var(--accent-bg)"), [], "a ring is a border");
  assert.deepEqual(kinds("box-shadow", "inset 3px 0 0 var(--accent)"), [], "an inset bar is a border");
  assert.deepEqual(kinds("box-shadow", "0 2px 8px rgba(0, 0, 0, 0.1)"), ["color", "shadow"]);
  assert.deepEqual(kinds("z-index", "var(--z-popover)"), []);
  assert.deepEqual(kinds("z-index", "4"), [], "1-9 orders siblings in one component");
  assert.deepEqual(kinds("z-index", "1100"), ["z"]);
  assert.deepEqual(kinds("transition", "opacity var(--dur-fast) ease"), []);
  assert.deepEqual(kinds("transition", "opacity 0.12s ease"), ["duration"]);
  assert.deepEqual(kinds("animation", "pillSpin 0.9s linear infinite"), [], "a loop keeps its period");
});

test("allow comments exempt a line or a block", () => {
  const css = [
    ".a { color: #111; }",
    ".b { color: #222; } /* ds-allow: why */",
    "/* ds-allow-start: a palette */",
    ".c { color: #333; }",
    "/* ds-allow-end */",
    ".d {",
    "  color: #444;",
    "}",
  ].join("\n");
  const raw = declarations(css).filter((d) => !d.allowed && offences(d).length).map((d) => `${d.line}:${d.value}`);
  assert.deepEqual(raw, ["1:#111", "7:#444"]);
});

// A ratchet, not a bar: a file's count of a kind may fall but never rise, and
// a new stylesheet starts at zero. After lowering counts, `node
// tools/designTokens.mjs --baseline` records the new floor.
test("no stylesheet gains raw design values", () => {
  const base = JSON.parse(fs.readFileSync(BASELINE, "utf8"));
  const found = scan();
  const now = counts(found);
  const worse = [];
  for (const [file, kinds] of Object.entries(now)) {
    for (const kind of KINDS) {
      const n = kinds[kind] || 0, was = base[file]?.[kind] || 0;
      if (n > was) worse.push(`${file}: ${n} raw ${kind} (baseline ${was}):\n    ${found[file][kind].join("\n    ")}`);
    }
  }
  assert.deepEqual(worse, [], "use a token from tokens.css, or mark a justified value with /* ds-allow: why */ (node tools/designTokens.mjs --list)");
});

// The type sizes are declared again on the control reset, so a zoomed
// control's text scales once (tokens.css, "Type sizes"); both lists must
// name the same controls.
test("the type sizes are re-declared on exactly the control-reset selector", () => {
  const list = (css) => {
    const m = css.replace(/\/\*[\s\S]*?\*\//g, "").match(/:where\((button, summary[^)]*)\)\s*\{\s*--ui-font-scale|:root,\s*:where\((button, summary[^)]*)\)\s*\{\s*--fs-/);
    return m && (m[1] || m[2]).split(",").map((s) => s.trim()).sort();
  };
  const app = list(read("src/shared/styles/app.css"));
  const tokens = list(read("src/shared/styles/tokens.css"));
  assert.ok(app && tokens, "both selectors found");
  assert.deepEqual(tokens, app);
});
