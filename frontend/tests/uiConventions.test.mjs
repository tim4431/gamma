// The UI-convention ratchet (docs/dev/ui-design.md, "Ratchets"): the places
// the code goes round a shared helper or rule of ui-design.md — a gesture
// past press.js, a device read past pointer.js, a file pick past pickFiles,
// an Enter that commits while an input method composes, a shared component's
// class written by hand, a hover reveal that still takes a tap — may only
// go down. tools/uiConventions.mjs counts them; tests/ui-baseline.json
// records the counts per file and rule.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { BASELINE, RULES, RULE_IDS, counts, scan, scanSource } from "../tools/uiConventions.mjs";

// The rules one snippet breaks, in order.
const rules = (code, file = "feature/Thing.jsx") => scanSource(code, file).found.map((f) => f.rule);
const jsx = (body) => `export const A = () => <>\n${body}\n</>;\n`;
const breaks = (body, file) => rules(jsx(body), file);
// The whole of src/, scanned once for the two tests that read it.
let whole;
const tree = () => (whole ||= scan());

test("dropdowns, gestures and pickers: the shared helpers", () => {
  assert.deepEqual(breaks(`<select />`), ["native-select"]);
  assert.deepEqual(rules(`document.createElement("select");`), ["native-select"]);
  assert.deepEqual(breaks(`<MenuSelect />`), []);

  assert.deepEqual(breaks(`<div onContextMenu={open} />`), ["context-menu"]);
  assert.deepEqual(breaks(`<Card onContextMenu={open} />`), ["context-menu"], "a component's prop too");
  assert.deepEqual(rules(`el.addEventListener("contextmenu", fn);`), ["context-menu"]);
  assert.deepEqual(breaks(`<div {...menuPress(open)} />`), []);
  assert.deepEqual(breaks(`<div onContextMenu={(e) => e.preventDefault()} />`), [], "a cancel-only handler");
  assert.deepEqual(breaks(`<div onContextMenu={(e) => { if (held.current) e.preventDefault(); }} />`), []);
  assert.deepEqual(breaks(`<div onContextMenu={press.onContextMenu} />`), []);
  assert.deepEqual(rules(`el.addEventListener("contextmenu", press.onContextMenu);`), []);
  assert.deepEqual(rules(`el.addEventListener("contextmenu", fn);`, "shared/ui/press.js"), [], "the owner");

  assert.deepEqual(breaks(`<div onDoubleClick={() => open()} />`), ["double-click"]);
  assert.deepEqual(breaks(`<div onDoubleClick={twice.onDoubleClick} />`), []);
  assert.deepEqual(breaks(`<div onDoubleClick={stop} />`), []);
  assert.deepEqual(breaks(`<div onDoubleClick={(e) => e.stopPropagation()} />`), []);

  assert.deepEqual(rules(`if (e.pointerType === "touch") go();`), ["pointer-kind"]);
  assert.deepEqual(rules(`const n = navigator.maxTouchPoints;`), ["pointer-kind"]);
  assert.deepEqual(rules(`const c = e?.pointerType;`), ["pointer-kind"]);
  assert.deepEqual(rules(`matchMedia("(pointer: coarse)");`), ["pointer-kind"]);
  // biome-ignore lint/suspicious/noTemplateCurlyInString: a source snippet scanned as text, not a template
  assert.deepEqual(rules("const q = `(any-hover: none) and ${x}`;"), ["pointer-kind"]);
  assert.deepEqual(rules(`fire({ pointerType: "mouse" });`), [], "an object key is no read");
  assert.deepEqual(rules(`matchMedia("(prefers-reduced-motion: reduce)");`), []);
  assert.deepEqual(rules(`if (e.pointerType === "touch") go();`, "shared/lib/pointer.js"), [], "the owner");
  assert.deepEqual(rules(`if (e.pointerType === "touch") go();`, "ink/InkLayer.jsx"), [], "the ink layer is exempt");

  assert.deepEqual(breaks(`<a onTouchStart={f} />`), ["touch-handler"]);
  assert.deepEqual(rules(`el.addEventListener("touchmove", f);`), ["touch-handler"]);
  assert.deepEqual(rules(`el.addEventListener("touchmove", f);`, "shared/lib/viewerZoom.js"), []);
  assert.deepEqual(rules(`el.addEventListener("touchmove", f);`, "ink/inkInput.js"), []);

  assert.deepEqual(breaks(`<input type="file" />`), ["file-input"]);
  assert.deepEqual(rules(`inp.type = "file";`), ["file-input"]);
  assert.deepEqual(rules(`inp.type = "file";`, "shared/lib/pickFiles.js"), []);
  assert.deepEqual(breaks(`<input type="text" />`), []);

  assert.deepEqual(breaks(`<input type="color" />`), ["color-input"]);
  assert.deepEqual(breaks(`<input type="color" />`, "shared/ui/ColorPicker.jsx"), []);

  assert.deepEqual(breaks(`<div onWheel={(e) => e.preventDefault()} />`), ["wheel-prevent"]);
  assert.deepEqual(breaks(`<div onWheel={(e) => e.stopPropagation()} />`), []);
});

test("buttons, icons, menus and the shared components' classes", () => {
  assert.deepEqual(breaks(`<button>×</button>`), ["text-close"]);
  assert.deepEqual(breaks(`<button>{"✕"}</button>`), ["text-close"]);
  assert.deepEqual(breaks(`<button>{rate}×</button>`), []);
  assert.deepEqual(breaks(`<button className="uiClose"><XIcon size={14} /></button>`), []);

  assert.deepEqual(breaks(`<CheckIcon size={12} />`), ["icon-size"]);
  assert.deepEqual(breaks(`<CheckIcon size={15} />`), ["icon-size"]);
  for (const ok of ["9", "10", "14", "16", "20", "48", "null", "n"]) assert.deepEqual(breaks(`<CheckIcon size={${ok}} />`), [], `size ${ok}`);
  assert.deepEqual(breaks(`<XIcon size={48} strokeWidth={1.5} />`), ["icon-stroke"]);
  assert.deepEqual(breaks(`<XIcon size={null} strokeWidth={2} />`), []);

  assert.deepEqual(breaks(`<button className="ctxMenuItem" />`), ["owned-class"]);
  // biome-ignore lint/suspicious/noTemplateCurlyInString: a source snippet scanned as text, not a template
  assert.deepEqual(breaks("<div className={`settingRow ${x}`} />"), ["owned-class"]);
  assert.deepEqual(breaks(`<button className="ctxMenuItem" />`, "shared/ui/Menus.jsx"), [], "the owner");
  assert.deepEqual(breaks(`<button className="ctxMenuItemish" />`), []);
  assert.deepEqual(breaks(`<span data-popover="x" className="popoverAnchor" />`), []);

  assert.deepEqual(breaks(`<div role="menu" />`), ["menu-role"]);
  assert.deepEqual(breaks(`<div role="menu" />`, "shared/ui/Menus.jsx"), []);
});

test("text fields: the input method's Enter and account names", () => {
  assert.deepEqual(breaks(`<input onKeyDown={(e) => { if (e.key === "Enter") go(); }} />`), ["enter-composing"]);
  assert.deepEqual(rules(`function F() {\n  function onKeyDown(e) { if (e.key === "Enter") go(); }\n  return <textarea onKeyDown={onKeyDown} />;\n}`), ["enter-composing"], "a named handler, through its binding");
  assert.deepEqual(rules(`function F() {\n  const onKey = useCallback((e) => { if (e.key === "Enter") go(); }, []);\n  return <input type="search" onKeyDown={onKey} />;\n}`), ["enter-composing"], "a useCallback's function");
  assert.deepEqual(breaks(`<div contentEditable onKeyDown={(e) => { if (e.key === "Enter") go(); }} />`), ["enter-composing"]);
  assert.deepEqual(breaks(`<AutoGrowTextarea onKeyDown={(e) => { if (e.key === "Enter") go(); }} />`), ["enter-composing"], "a shared wrapper hands the key to its own textarea");
  assert.deepEqual(breaks(`<PasswordInput onKeyDown={(e) => { if (e.key === "Enter") go(); }} />`), ["enter-composing"], "a shown password is a text field");
  assert.deepEqual(breaks(`<PasswordInput onKeyDown={(e) => { if (e.key === "Enter" && !composing(e)) go(); }} />`), []);
  assert.deepEqual(breaks(`<CopyField onKeyDown={(e) => { if (e.key === "Enter") go(); }} />`), [], "only an Input or Textarea wrapper counts");
  assert.deepEqual(breaks(`<input onKeyDown={(e) => { if (composing(e)) return; if (e.key === "Enter") go(); }} />`), []);
  assert.deepEqual(breaks(`<input onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) go(); }} />`), ["enter-composing"], "Safari's keydown says 229, not isComposing");
  assert.deepEqual(breaks(`<input onKeyDown={(e) => { if (e.nativeEvent.isComposing || e.keyCode === 229) return; if (e.key === "Enter") go(); }} />`), []);
  assert.deepEqual(breaks(`<textarea onKeyDown={(e) => { if (sendsOnEnter(e)) send(); }} />`), []);
  assert.deepEqual(breaks(`<input type="number" onKeyDown={(e) => { if (e.key === "Enter") go(); }} />`), [], "a number has no input method");
  assert.deepEqual(breaks(`<button onKeyDown={(e) => { if (e.key === "Enter") go(); }} />`), []);
  assert.deepEqual(breaks(`<input onKeyDown={(e) => { /* Enter */ if (e.key === "Tab") go(); }} />`), [], "Enter only in a comment");

  assert.deepEqual(breaks(`<input type="text" value={form.username} />`), ["account-field"]);
  assert.deepEqual(breaks(`<input autoComplete="username" />`), ["account-field"]);
  assert.deepEqual(breaks(`<input type="text" value={form.username} autoCapitalize="none" autoCorrect="off" />`), []);
  assert.deepEqual(breaks(`<input type="email" value={form.username} />`), [], "an e-mail field capitalizes nothing");
});

test("inline styles keep to the scales", () => {
  for (const bad of [`{ zIndex: 1400 }`, `{ fontSize: 12 }`, `{ fontSize: "calc(12px * var(--ui-font-scale))" }`, `{ borderRadius: 8 }`,
    `{ fontFamily: "Georgia" }`, `{ position: "relative", display: "inline-flex" }`, `{ transition: "all 0.2s" }`]) {
    assert.deepEqual(breaks(`<div style={${bad}} />`), ["inline-style"], bad);
  }
  for (const ok of [`{ zIndex: 3 }`, `{ zIndex: "var(--z-popover)" }`, `{ fontSize: "var(--fs-xs)" }`, `{ borderRadius: 2 }`, `{ fontFamily: "var(--font-mono)" }`, `{ left: x }`]) {
    assert.deepEqual(breaks(`<div style={${ok}} />`), [], ok);
  }
});

test("stylesheets: hover reveals, labels, motion, the font shorthand, fields", () => {
  const css = (text) => rules(text, "feature/thing.css");
  assert.deepEqual(css(`.row .btn { opacity: 0; }\n.row:hover .btn { opacity: 1; }`), ["hover-reveal"]);
  assert.deepEqual(css(`.row .btn { opacity: 0; pointer-events: none; }\n.row:hover .btn { opacity: 1; pointer-events: auto; }`), [], "it takes no press while hidden");
  assert.deepEqual(css(`@media (hover: hover) { .row .btn { opacity: 0; } .row:hover .btn { opacity: 1; } }`), [], "only where a pointer hovers");
  assert.deepEqual(css(`.row .btn { opacity: 0; }\n@media (hover: hover) { .row:hover .btn { opacity: 1; } }`), []);
  assert.deepEqual(css(`.btn { opacity: 0; }\n.btn:hover { opacity: 1; }`), [], "the control's own hover");

  assert.deepEqual(css(`.lab { text-transform: uppercase; }`), ["uppercase"]);
  assert.deepEqual(css(`.lab { text-transform: none; }`), []);
  assert.deepEqual(css(`.x { transition: all 0.2s; }`), ["transition-all"]);
  assert.deepEqual(css(`.x { transition: opacity var(--dur-fast); }`), []);
  assert.deepEqual(css(`.x { accent-color: red; }`), ["accent-color"]);
  assert.deepEqual(css(`:root { accent-color: var(--accent); }`), []);

  assert.deepEqual(css(`.b { font: inherit; }`), ["font-inherit"]);
  assert.deepEqual(css(`.b { font: inherit; font-size: var(--fs-sm); }`), [], "its size set after it");
  assert.deepEqual(css(`.fooInput { font: inherit; }`), [], "a field is no zoomed control");
  assert.deepEqual(css(`.x textarea { font: inherit; }`), []);
  assert.deepEqual(css(`.b { font-family: inherit; font-weight: inherit; line-height: inherit; }`), []);

  assert.deepEqual(css(`.f:focus { border-color: var(--accent); }`), ["field-focus"]);
  assert.deepEqual(css(`.f:focus-visible { border-color: var(--accent); }`), []);
  assert.deepEqual(css(`.box:focus-within { border-color: var(--accent); }`), []);
});

test("an allow names its rule, gives its reason and covers what it says", () => {
  const code = [
    "export const A = () => <>",
    "  {/* ui-allow color-input: the tool strip's custom swatch keeps the native picker */}",
    "  <input type=\"color\" />",
    "  <input type=\"color\" /* ui-allow color-input: inside the tag */ />",
    "  <input",
    "    // ui-allow file-input: a line comment inside the tag",
    "    type=\"file\" />",
    "  <input type=\"color\" />",
    "  {/* ui-allow-start icon-size: a block */}",
    "  <CheckIcon size={12} />",
    "  <CheckIcon size={13} />",
    "  {/* ui-allow-end */}",
    "  <CheckIcon size={12} />",
    "  {/* ui-allow file-input: names another rule */}",
    "  <CheckIcon size={11} />",
    "  {/* ui-allow nosuch: x */}",
    "  {/* ui-allow file-input */}",
    "</>;",
  ].join("\n");
  const { found, problems } = scanSource(code, "feature/A.jsx");
  assert.deepEqual(found.map((f) => `${f.line} ${f.rule}`), ["8 color-input", "13 icon-size", "15 icon-size"]);
  assert.equal(problems.length, 3, problems.join("\n"));
  assert.match(problems[0], /^14: ui-allow file-input exempts nothing/);
  assert.match(problems[1], /^16: ui-allow names no rule "nosuch"/);
  assert.match(problems[2], /^17: ui-allow file-input gives no reason/);

  const css = [
    ".lab { text-transform: uppercase; } /* ui-allow uppercase: a brand eyebrow */",
    ".lab2 { text-transform: uppercase; }",
    "/* ui-allow uppercase: a lone comment covers the next declaration */",
    ".lab3 { text-transform: uppercase; }",
  ].join("\n");
  const styles = scanSource(css, "feature/b.css");
  assert.deepEqual(styles.found.map((f) => f.line), [2]);
  assert.deepEqual(styles.problems, []);
  assert.match(scanSource("/* ui-allow-start uppercase: open */\n.a { color: red; }", "x.css").problems[0], /has no ui-allow-end/);
});

// Every allow in src/ is one the test above would accept, and every rule has
// a fix and a section of docs/dev/ui-design.md to point to: a heading's
// anchor as GitHub writes it.
test("the allows in src/ name a rule, give a reason and exempt something", () => {
  assert.deepEqual(tree().problems, []);
  const doc = fs.readFileSync(new URL("../../docs/dev/ui-design.md", import.meta.url), "utf8");
  const anchors = new Set([...doc.matchAll(/^#{1,6}\s+(.+)$/gm)]
    .map(([, h]) => "#" + h.trim().toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu, "").replace(/\s/g, "-")));
  for (const [id, rule] of Object.entries(RULES)) {
    assert.ok(rule.fix, `${id} has a fix`);
    assert.ok(anchors.has(rule.doc), `${id}'s section ${rule.doc} is a heading of ui-design.md`);
  }
});

// A ratchet, not a bar: a file's count of a rule may fall but never rise,
// and a new file starts at zero. After lowering counts,
// `node tools/uiConventions.mjs --baseline` records the new floor.
test("no source file gains UI-convention breaks", () => {
  const base = JSON.parse(fs.readFileSync(BASELINE, "utf8"));
  const unknown = Object.entries(base).flatMap(([file, r]) => Object.keys(r).filter((id) => !RULES[id]).map((id) => `${file}: ${id}`));
  assert.deepEqual(unknown, [], "the baseline names rules tools/uiConventions.mjs does not have");
  const { found } = tree();
  const now = counts(found);
  const worse = [];
  for (const [file, byRule] of Object.entries(now)) {
    for (const id of RULE_IDS) {
      const n = byRule[id] || 0, was = base[file]?.[id] || 0;
      if (n > was) {
        worse.push(`${file}: ${n} ${id} (baseline ${was}): ${RULES[id].fix}\n    ${found[file][id].join("\n    ")}\n    (node tools/uiConventions.mjs --list ${id})`);
      }
    }
  }
  assert.deepEqual(worse, [], "follow the rule, or mark a deliberate exception with a `ui-allow <rule>: why` comment (docs/dev/ui-design.md, Ratchets)");
});
