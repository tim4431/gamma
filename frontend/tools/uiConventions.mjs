// Counts the places the code goes round a UI convention of
// docs/dev/ui-design.md — a gesture that skips press.js, a device read that
// skips pointer.js, a file pick that skips pickFiles, an Enter that commits
// while an input method composes — for the ratchet in
// tests/uiConventions.test.mjs.
//
//   node tools/uiConventions.mjs                    # totals per rule and per file
//   node tools/uiConventions.mjs --list [rule]      # every break, file:line text [rule]
//   node tools/uiConventions.mjs --baseline         # rewrite tests/ui-baseline.json
//
// It reads every .js, .jsx and .css file under src/ (tokens.css aside): the
// scripts through @babel/parser, the stylesheets through designTokens.mjs's
// declarations(). RULES below gives each rule's fix, the ui-design.md
// section it comes from, the module it points to (`owners`, never counted)
// and the folders the doc itself exempts (`except`, with the doc's reason).
// What counts:
//   native-select    a JSX <select>, or createElement("select")
//   context-menu     onContextMenu, or a "contextmenu" listener, whose handler
//                    is neither a press helper's nor one that only cancels
//   double-click     the same for onDoubleClick and "dblclick"
//   pointer-kind     a read of .pointerType or .maxTouchPoints, or a string
//                    naming (any-)pointer: coarse|fine or (any-)hover: hover|none
//   touch-handler    an onTouch* on a DOM element, or a "touch…" listener
//   file-input       <input type="file">, or x.type = "file"
//   color-input      <input type="color">
//   text-close       a <button> whose only content is ×, ✕ or ✖
//   icon-size        <…Icon size={n}>, n not 9, 10, 14, 16, 20 or 48
//   icon-stroke      strokeWidth on an icon not sized by CSS (size={null})
//   owned-class      a className word OWNED gives another file's component
//   menu-role        role="menu"
//   enter-composing  a text field's onKeyDown or onKeyPress (inline, or a
//                    local function) that tests "Enter" and never asks composing;
//                    a component named …Input or …Textarea counts as a text field
//   account-field    a username's text input without autoCapitalize="none"
//                    and autoCorrect="off"
//   inline-style     a style={{…}} z-index over 9, written font size, radius
//                    over 2, font family, transition of all, or popoverAnchor
//   wheel-prevent    an onWheel that calls preventDefault
//   hover-reveal     opacity: 0 outside (hover: hover) and without
//                    pointer-events: none, undone by an ancestor's :hover
//   uppercase        text-transform: uppercase
//   transition-all   a transition or transition-property that lists all
//   font-inherit     font: inherit with no font-size after it, off a field
//   field-focus      a :focus rule (not -visible or -within) that sets a border
//   accent-color     accent-color off :root and html
//
// A deliberate exception says why in a comment: `ui-allow <rule>: why`. In a
// script it may be a // line, a /* */ inside a tag or a {/* */}, in a
// stylesheet a /* */. It covers its own line, and, standing alone on its
// line, the next line of code too. A break counts for every line of its
// tag, so a comment on any line of a multi-line <input …> covers it.
// `ui-allow-start <rule>: why` …
// `ui-allow-end` covers every line between. The rule's name is required, so
// an allow never silences another rule, and so is the reason; an allow that
// names no rule, gives no reason or exempts nothing is reported.
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { declarations } from "./designTokens.mjs";

const require = createRequire(import.meta.url);
const parser = require("@babel/parser");
const traverse = require("@babel/traverse").default;

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SRC = path.join(FRONTEND, "src");
export const BASELINE = path.join(FRONTEND, "tests", "ui-baseline.json");
const TOKENS = "shared/styles/tokens.css";
const DOC = "docs/dev/ui-design.md";

const INK = { "ink/": "the ink layer, where a pen draws and a finger scrolls" };

// id → { fix, doc (the ui-design.md section), owners (files never counted),
// except ({ folder prefix: the doc's reason }) }.
export const RULES = {
  "native-select": { fix: "a dropdown is MenuSelect or ActionMenu (shared/ui/Menus.jsx), never a native <select>", doc: "#one-control-set-everywhere" },
  "context-menu": { fix: "spread menuPress(open) from shared/ui/press.js where onContextMenu would go", doc: "#gestures-shareduipressjs", owners: ["shared/ui/press.js"] },
  "double-click": { fix: "spread doublePress(run) from shared/ui/press.js, never onDoubleClick alone", doc: "#gestures-shareduipressjs", owners: ["shared/ui/press.js"] },
  "pointer-kind": { fix: "ask shared/lib/pointer.js (lastPointer, touchTyping, touchScreen), or let CSS ask", doc: "#device-questions-sharedlibpointerjs", owners: ["shared/lib/pointer.js", "shared/ui/press.js", "shared/ui/useDragOrder.js", "shared/lib/viewerZoom.js"], except: INK },
  "touch-handler": { fix: "a press is a pointer event, through shared/ui/press.js", doc: "#gestures-shareduipressjs", owners: ["shared/ui/press.js", "shared/lib/viewerZoom.js"], except: INK },
  "file-input": { fix: "open the picker with pickFiles (shared/lib/pickFiles.js)", doc: "#device-questions-sharedlibpointerjs", owners: ["shared/lib/pickFiles.js"] },
  "color-input": { fix: "a colour is a ColorPicker (shared/ui/ColorPicker.jsx)", doc: "#one-control-set-everywhere", owners: ["shared/ui/ColorPicker.jsx"] },
  "text-close": { fix: "a close button is a uiClose with an XIcon, never a text ×", doc: "#icons" },
  "icon-size": { fix: "an icon is 14 inline, 16 in a button, 20 in a header (48 an illustration, 9-10 in a badge)", doc: "#icons", owners: ["shared/ui/Icons.jsx"] },
  "icon-stroke": { fix: "pass strokeWidth only to an icon sized by CSS (size={null})", doc: "#icons", owners: ["shared/ui/Icons.jsx"] },
  "owned-class": { fix: "render the shared component that owns the class (OWNED in tools/uiConventions.mjs)", doc: "#one-control-set-everywhere" },
  "menu-role": { fix: "a menu is a ContextMenu or a MenuScope (shared/ui/Menus.jsx)", doc: "#menus-and-submenus", owners: ["shared/ui/Menus.jsx"] },
  "enter-composing": { fix: "return early while an input method composes: if (composing(e)) return; (shared/lib/hotkeys.js)", doc: "#adding-a-control" },
  "account-field": { fix: "a username field has autoCapitalize=\"none\" and autoCorrect=\"off\"", doc: "#adding-a-control" },
  "inline-style": { fix: "a class with tokens, not an inline zIndex, fontSize, borderRadius, fontFamily or popoverAnchor", doc: "#scales" },
  "wheel-prevent": { fix: "React's onWheel is passive: preventDefault needs a native listener (useTextScale, viewerZoom.js)", doc: "#interface-size-and-text-size" },
  "hover-reveal": { fix: "hide it under @media (hover: hover), or pair opacity: 0 with pointer-events: none", doc: "#sizes-and-reveals-css-asks-capability" },
  "uppercase": { fix: "a label keeps its own case: no text-transform: uppercase", doc: "#section-labels" },
  "transition-all": { fix: "a transition names the properties it animates, never all", doc: "#motion" },
  "font-inherit": { fix: "a control writes font-family: inherit (and weight, line height), not font: inherit", doc: "#interface-size-and-text-size" },
  "field-focus": { fix: "a field joins the field list beside the focus ring in app.css instead of a :focus border", doc: "#keyboard-focus" },
  "accent-color": { fix: "native controls take the accent from the root's accent-color", doc: "#buttons" },
};
export const RULE_IDS = Object.keys(RULES);

// The classes a shared component renders: written by hand anywhere else,
// they are a copy of the component.
export const OWNED = {
  "shared/ui/Menus.jsx": ["ctxMenu", "ctxMenuItem", "ctxMenuLabel", "ctxMenuDivider", "ctxSubMenu", "rowMenuBtn", "uiSelectBtn", "uiSelectMenu"],
  "shared/ui/Surfaces.jsx": ["reportOverlay", "popover"],
  "settings/SettingsKit.jsx": ["settingRow", "setRow", "setSection", "setSectionLabel", "setDangerBox"],
  "shared/ui/KeyCaps.jsx": ["keyCap", "keyCaps"],
};

const ICON_SIZES = new Set([9, 10, 14, 16, 20, 48]);
const TEXT_TYPES = new Set(["text", "search", "url", "email", "password", "tel"]);
const CLOSE_GLYPH = /^\s*[×✕✖]\s*$/;
const POINTER_MEDIA = /\((?:any-)?pointer\s*:\s*(?:coarse|fine)\)|\((?:any-)?hover\s*:\s*(?:hover|none)\)/;

export function sourceFiles(dir = SRC) {
  const out = [];
  (function walk(d) {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (/\.(jsx?|css)$/.test(e.name) && rel(p, dir) !== TOKENS) out.push(p);
    }
  })(dir);
  return out.sort();
}
const rel = (f, dir = SRC) => path.relative(dir, f).split(path.sep).join("/");

// ---------------------------------------------------------------- allows

// The allow comments of one file, from its comments ({start, end, value}
// offsets), as entries {rule, line, lines (covered), used}, and what is
// wrong with them.
function readAllows(code, comments) {
  const starts = [0];
  for (let i = 0; i < code.length; i++) if (code[i] === "\n") starts.push(i + 1);
  const lineAt = (off) => { let lo = 0, hi = starts.length - 1; while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (starts[mid] <= off) lo = mid; else hi = mid - 1; } return lo + 1; };
  // The code with every comment blanked: what a line holds besides comments.
  const parts = [];
  let from = 0;
  for (const c of comments) { parts.push(code.slice(from, c.start), code.slice(c.start, c.end).replace(/[^\n]/g, " ")); from = c.end; }
  const lines = (parts.join("") + code.slice(from)).split("\n");
  const codeLine = (n) => /[^\s{}]/.test(lines[n - 1] ?? "");
  const entries = [], problems = [], open = [];
  for (const c of comments) {
    const m = c.value.match(/\bui-allow(-start|-end)?\b(.*)$/s);
    if (!m) continue;
    const line = lineAt(c.start), endLine = lineAt(c.end - 1);
    const kind = m[1] || "", rest = m[2].replace(/\s+/g, " ").trim();
    const [, rule = "", reason = ""] = rest.match(/^([\w-]*)\s*(?::\s*(.*))?$/) || [];
    if (kind === "-end") {
      const i = rule ? open.findLastIndex((o) => o.rule === rule) : open.length - 1;
      if (i < 0) { problems.push(`${line}: ui-allow-end with no ui-allow-start${rule ? ` ${rule}` : ""}`); continue; }
      const [o] = open.splice(i, 1);
      for (let n = o.line; n <= endLine; n++) o.lines.add(n);
      continue;
    }
    if (!rule || !RULES[rule]) { problems.push(`${line}: ui-allow${kind} names no rule${rule ? ` "${rule}"` : ""} (${RULE_IDS.join(", ")})`); continue; }
    if (!reason.trim()) { problems.push(`${line}: ui-allow${kind} ${rule} gives no reason ("ui-allow${kind} ${rule}: why")`); continue; }
    const entry = { rule, line, lines: new Set(), used: false };
    entries.push(entry);
    if (kind === "-start") { open.push(entry); continue; }
    for (let n = line; n <= endLine; n++) entry.lines.add(n);
    const before = (lines[line - 1] ?? "").slice(0, c.start - starts[line - 1]);
    const after = (lines[endLine - 1] ?? "").slice(c.end - starts[endLine - 1]);
    if (!/[^\s{}]/.test(before) && !/[^\s{}]/.test(after)) {
      // alone on its line: the next line of code too
      let n = endLine + 1;
      while (n <= lines.length && !codeLine(n)) n++;
      if (n <= lines.length) entry.lines.add(n);
    }
  }
  for (const o of open) problems.push(`${o.line}: ui-allow-start ${o.rule} has no ui-allow-end`);
  return { entries, problems };
}

// ---------------------------------------------------------------- scripts

const isNode = (v) => v && typeof v === "object" && typeof v.type === "string";
const SKIP = new Set(["loc", "start", "end", "extra", "leadingComments", "trailingComments", "innerComments", "range"]);
// Every node under `node` (itself included).
function each(node, fn) {
  if (!isNode(node)) return;
  fn(node);
  for (const k in node) {
    if (SKIP.has(k)) continue;
    const v = node[k];
    if (Array.isArray(v)) { for (const x of v) each(x, fn); } else if (isNode(v)) each(v, fn);
  }
}
const some = (node, test) => { let hit = false; each(node, (n) => { if (!hit && test(n)) hit = true; }); return hit; };
const strings = (node) => { const out = []; each(node, (n) => { if (n.type === "StringLiteral") out.push(n.value); else if (n.type === "TemplateElement") out.push(n.value.cooked ?? ""); }); return out; };
const isFn = (n) => !!n && (n.type === "ArrowFunctionExpression" || n.type === "FunctionExpression" || n.type === "FunctionDeclaration");
const propName = (n) => (n && !n.computed && n.property?.type === "Identifier" ? n.property.name : n?.computed && n.property?.type === "StringLiteral" ? n.property.value : "");
const isMember = (n) => n && (n.type === "MemberExpression" || n.type === "OptionalMemberExpression");
const calleeName = (n) => (n.callee.type === "Identifier" ? n.callee.name : isMember(n.callee) ? propName(n.callee) : "");
const isCall = (n) => n.type === "CallExpression" || n.type === "OptionalCallExpression";

const tagName = (open) => {
  const name = (n) => (n.type === "JSXIdentifier" ? n.name : n.type === "JSXMemberExpression" ? `${name(n.object)}.${n.property.name}` : n.type === "JSXNamespacedName" ? `${n.namespace.name}:${n.name.name}` : "");
  return name(open.name);
};
const isDom = (name) => /^[a-z][\w-]*$/.test(name);
const attrOf = (open, name) => open.attributes.find((a) => a.type === "JSXAttribute" && a.name.type === "JSXIdentifier" && a.name.name === name);
// An attribute's value as code: the expression in {…}, or the string.
const valueOf = (a) => (!a?.value ? null : a.value.type === "JSXExpressionContainer" ? (a.value.expression.type === "JSXEmptyExpression" ? null : a.value.expression) : a.value);
// An attribute's value when written out: "x", {"x"}, {`x`}, {3}; true when
// bare; undefined when it is computed.
function literal(a) {
  if (!a) return undefined;
  if (!a.value) return true;
  const v = valueOf(a);
  if (!v) return undefined;
  if (v.type === "StringLiteral" || v.type === "NumericLiteral" || v.type === "BooleanLiteral") return v.value;
  if (v.type === "TemplateLiteral" && !v.expressions.length) return v.quasis[0].value.cooked;
  if (v.type === "NullLiteral") return null;
  return undefined;
}
// An <input>'s type: written out, or the first text type a computed one can
// take ("text" when it has none of its own).
function inputType(open) {
  const a = attrOf(open, "type");
  if (!a) return "text";
  const v = literal(a);
  if (typeof v === "string") return v;
  const all = strings(valueOf(a));
  return all.find((s) => TEXT_TYPES.has(s)) || all[0] || "";
}
// A handler that only stops the native gesture: (e) => e.preventDefault(),
// an `if` around that, or a function named for it (stop, prevent…).
function cancelOnly(n) {
  if (!n) return false;
  if (n.type === "Identifier") return /^(stop|prevent|swallow|noop|cancel)/i.test(n.name);
  if (!isFn(n)) return false;
  const stops = (e) => !!e && isCall(e) && /^(preventDefault|stopPropagation|stopImmediatePropagation)$/.test(calleeName(e));
  const stmt = (s) => (s.type === "ExpressionStatement" && (stops(s.expression) || (s.expression.type === "LogicalExpression" && stops(s.expression.right))))
    || (s.type === "IfStatement" && !s.alternate && (s.consequent.type === "BlockStatement" ? s.consequent.body.every(stmt) : stmt(s.consequent)))
    || s.type === "EmptyStatement";
  if (n.body.type !== "BlockStatement") return stops(n.body) || (n.body.type === "LogicalExpression" && stops(n.body.right));
  return n.body.body.length > 0 && n.body.body.every(stmt);
}
// The function a handler names: a local function or const (a useCallback's
// function too), else the expression as written.
function handler(p, expr) {
  if (expr?.type !== "Identifier") return expr;
  const b = p.scope.getBinding(expr.name);
  const n = b?.path.node;
  if (!n) return null;
  if (n.type === "FunctionDeclaration") return n;
  if (n.type === "VariableDeclarator") {
    if (isFn(n.init)) return n.init;
    if (n.init && isCall(n.init)) return n.init.arguments.find(isFn) || null;
  }
  return null;
}
// An Enter handler that waits for the input method: composing(e), or the
// chat's sendsOnEnter / sendsOnShiftEnter, which ask it. isComposing alone
// is not enough: Safari's keydown for the Enter that confirms a word has it
// false and says keyCode 229.
const waitsForIme = (n) => some(n, (x) => x.type === "Identifier" && /^(composing|sendsOnEnter|sendsOnShiftEnter)$/.test(x.name))
  || (some(n, (x) => x.type === "Identifier" && x.name === "isComposing") && some(n, (x) => x.type === "NumericLiteral" && x.value === 229));
const testsEnter = (n) => some(n, (x) => x.type === "StringLiteral" && x.value === "Enter");

function scanScript(code, file, hit) {
  const ast = parser.parse(code, { sourceType: "module", plugins: ["jsx"] });
  const text = (n) => code.slice(n.start, n.end).replace(/\s+/g, " ").slice(0, 120);
  traverse(ast, {
    JSXOpeningElement(p) {
      const open = p.node, name = tagName(open), dom = isDom(name);
      const span = [open.loc.start.line, open.loc.end.line];
      const at = (rule, n, what = text(n)) => hit(rule, n.loc.start.line, span, what);
      if (name === "select") at("native-select", open);
      for (const [attr, rule] of [["onContextMenu", "context-menu"], ["onDoubleClick", "double-click"]]) {
        const a = attrOf(open, attr), v = valueOf(a);
        if (!v || (isMember(v) && propName(v) === attr)) continue;
        if (!cancelOnly(v) && !cancelOnly(handler(p, v))) at(rule, a);
      }
      if (dom) for (const ev of ["onTouchStart", "onTouchMove", "onTouchEnd", "onTouchCancel"]) {
        const a = attrOf(open, ev);
        if (a) at("touch-handler", a);
      }
      const wheel = attrOf(open, "onWheel");
      if (wheel && some(handler(p, valueOf(wheel)), (x) => isCall(x) && calleeName(x) === "preventDefault")) at("wheel-prevent", wheel);
      if (name === "input") {
        const type = inputType(open);
        if (type === "file") at("file-input", open);
        if (type === "color") at("color-input", open);
        if (type === "text") {
          const auto = literal(attrOf(open, "autoComplete"));
          const value = valueOf(attrOf(open, "value"));
          const account = (typeof auto === "string" && /\busername\b/.test(auto)) || (value && /username/i.test(code.slice(value.start, value.end)));
          const cap = literal(attrOf(open, "autoCapitalize")), cor = literal(attrOf(open, "autoCorrect"));
          if (account && !((cap === "none" || cap === "off") && cor === "off")) at("account-field", open);
        }
      }
      if (literal(attrOf(open, "role")) === "menu") at("menu-role", open);
      // A text field's Enter.
      const ce = attrOf(open, "contentEditable");
      // The shared wrappers (AutoGrowTextarea, PasswordInput, PaperMentionInput…)
      // hand their key handlers to a text field of their own.
      const editable = (name === "input" && TEXT_TYPES.has(inputType(open))) || name === "textarea"
        || /^[A-Z]\w*(?:Input|Textarea)$/.test(name)
        || (dom && ce && literal(ce) !== false && literal(ce) !== "false");
      if (editable) for (const key of ["onKeyDown", "onKeyPress"]) {
        const a = attrOf(open, key), fn = handler(p, valueOf(a));
        if (fn && testsEnter(fn) && !waitsForIme(fn)) at("enter-composing", a);
      }
      if (name === "button") {
        const kids = p.parent.children.filter((c) => !(c.type === "JSXText" && !c.value.trim()));
        const only = kids.length === 1 ? kids[0] : null;
        if (only && ((only.type === "JSXText" && CLOSE_GLYPH.test(only.value))
          || (only.type === "JSXExpressionContainer" && only.expression.type === "StringLiteral" && CLOSE_GLYPH.test(only.expression.value)))) at("text-close", open);
      }
      if (/^[A-Z]\w*Icon$/.test(name)) {
        const size = valueOf(attrOf(open, "size"));
        const sizes = !size ? [] : size.type === "NumericLiteral" ? [size] : size.type === "ConditionalExpression" ? [size.consequent, size.alternate].filter((x) => x.type === "NumericLiteral") : [];
        for (const s of sizes) if (!ICON_SIZES.has(s.value)) at("icon-size", open, `<${name} size={${s.value}}>`);
        if (attrOf(open, "strokeWidth") && size?.type !== "NullLiteral") at("icon-stroke", open);
      }
      const cls = attrOf(open, "className");
      if (cls?.value) {
        const words = new Set(strings(cls.value).join(" ").split(/\s+/));
        for (const [owner, list] of Object.entries(OWNED)) {
          if (owner === file) continue;
          for (const c of list) if (words.has(c)) at("owned-class", cls, `${c} (${owner}): ${text(cls)}`);
        }
      }
      const style = valueOf(attrOf(open, "style"));
      if (style?.type === "ObjectExpression") inlineStyle(style, (n, what) => hit("inline-style", n.loc.start.line, span, what || text(n)));
    },
    CallExpression(p) {
      const n = p.node, name = calleeName(n), [a0, a1] = n.arguments;
      if (a0?.type !== "StringLiteral") return;
      const span = [n.loc.start.line, n.loc.end.line];
      if (name === "createElement" && a0.value === "select") hit("native-select", n.loc.start.line, span, text(n));
      if (name !== "addEventListener") return;
      const ev = a0.value;
      for (const [native, prop, rule] of [["contextmenu", "onContextMenu", "context-menu"], ["dblclick", "onDoubleClick", "double-click"]]) {
        if (ev !== native || (isMember(a1) && propName(a1) === prop) || cancelOnly(a1) || cancelOnly(handler(p, a1))) continue;
        hit(rule, n.loc.start.line, span, text(n));
      }
      if (/^touch/.test(ev)) hit("touch-handler", n.loc.start.line, span, text(n));
    },
    AssignmentExpression(p) {
      const n = p.node;
      if (isMember(n.left) && propName(n.left) === "type" && n.right.type === "StringLiteral" && n.right.value === "file") hit("file-input", n.loc.start.line, [n.loc.start.line, n.loc.end.line], text(n));
    },
    "MemberExpression|OptionalMemberExpression"(p) {
      const n = p.node, prop = propName(n);
      if (prop !== "pointerType" && prop !== "maxTouchPoints") return;
      if (p.parent.type === "AssignmentExpression" && p.parent.left === n) return;
      const outer = p.parentPath.node;
      hit("pointer-kind", n.loc.start.line, [n.loc.start.line, n.loc.end.line], text(outer.type === "ExpressionStatement" ? n : outer));
    },
    StringLiteral(p) {
      if (POINTER_MEDIA.test(p.node.value)) hit("pointer-kind", p.node.loc.start.line, [p.node.loc.start.line, p.node.loc.end.line], text(p.node));
    },
    TemplateLiteral(p) {
      if (POINTER_MEDIA.test(p.node.quasis.map((q) => q.value.cooked ?? "").join(""))) hit("pointer-kind", p.node.loc.start.line, [p.node.loc.start.line, p.node.loc.end.line], text(p.node));
    },
  });
  return ast.comments.map((c) => ({ start: c.start, end: c.end, value: c.value }));
}

// A style={{…}} object's raw design values: what tokens.css has a token for.
function inlineStyle(obj, report) {
  const props = {};
  for (const pr of obj.properties) {
    if (pr.type !== "ObjectProperty") continue;
    const key = pr.key.type === "Identifier" ? pr.key.name : pr.key.value;
    const v = pr.value;
    const num = v.type === "NumericLiteral" ? v.value : null;
    // biome-ignore lint/suspicious/noTemplateCurlyInString: joins a template literal's parts around a placeholder marker
    const str = v.type === "StringLiteral" ? v.value : v.type === "TemplateLiteral" ? v.quasis.map((q) => q.value.cooked ?? "").join("${}") : null;
    props[key] = str ?? num;
    if (key === "zIndex" && ((num ?? (/^\d+$/.test(str ?? "") ? +str : 0)) > 9)) report(pr);
    if (key === "fontSize" && (num !== null || /\d(px|pt|rem)\b/.test((str ?? "").replace(/var\(--fs-[\w-]+\)/g, "")))) report(pr);
    if (key === "borderRadius" && ((num !== null && num > 2) || [...(str ?? "").replace(/var\(--radius-[\w-]+\)/g, "").matchAll(/(?:^|[\s(])([\d.]+)px\b/g)].some(([, x]) => +x > 2))) report(pr);
    if (key === "fontFamily" && str !== null && !/^(?:var\(--font-(?:sans|mono)\)|inherit)$/.test(str.trim())) report(pr);
    if ((key === "transition" || key === "transitionProperty") && str !== null && /(?:^|,)\s*all\b/.test(str)) report(pr);
  }
  if (props.position === "relative" && /inline-flex/.test(String(props.display ?? ""))) report(obj, "position: relative; display: inline-flex (the popoverAnchor class)");
}

// ---------------------------------------------------------------- stylesheets

const splitTop = (s, sep) => {
  const out = []; let depth = 0, cur = "";
  for (const ch of s) {
    if (ch === "(" || ch === "[") depth++;
    if (ch === ")" || ch === "]") depth--;
    if (!depth && sep.test(ch)) { if (cur.trim()) out.push(cur.trim()); cur = ""; } else cur += ch;
  }
  return cur.trim() ? out.concat(cur.trim()) : out;
};
const selectors = (sel) => splitTop(sel, /,/);
// A complex selector's compounds, the combinators dropped: ".a:hover > .b" → [".a:hover", ".b"].
const compounds = (sel) => splitTop(sel.replace(/\s*([>~+])\s*/g, " "), /\s/);
const classesOf = (compound) => [...compound.replace(/:(?:not|has|is|where)\([^)]*\)/g, "").matchAll(/\.([\w-]+)/g)].map((m) => m[1]);
const underHover = (media) => media.some((m) => /\(\s*hover\s*:\s*hover\s*\)/.test(m));
const inputLike = (sel) => { const last = compounds(sel).at(-1) || ""; return /(?:^|[^\w-])(?:input|textarea)\b/.test(last) || /\.[\w-]*(?:Input|Textarea)\b/.test(last); };

function scanStyles(css, hit) {
  const decls = declarations(css);
  const line = (d) => [d.line, d.line];
  const say = (d) => `${d.selector} { ${d.prop}: ${d.value} }`.slice(0, 140);
  for (const [i, d] of decls.entries()) {
    if (d.prop === "text-transform" && /\buppercase\b/.test(d.value)) hit("uppercase", d.line, line(d), say(d));
    if ((d.prop === "transition" || d.prop === "transition-property") && splitTop(d.value, /,/).some((x) => /^all\b/.test(x))) hit("transition-all", d.line, line(d), say(d));
    if (d.prop === "accent-color" && !selectors(d.selector).every((s) => /^(?::root|html)$/.test(s))) hit("accent-color", d.line, line(d), say(d));
    if ((d.prop === "border" || d.prop === "border-color") && selectors(d.selector).some((s) => /:focus(?![-\w])/.test(s))) hit("field-focus", d.line, line(d), say(d));
    if (d.prop === "font" && /^inherit(?:\s*!important)?$/.test(d.value)
      && !decls.slice(i + 1).some((e) => e.rule === d.rule && e.prop === "font-size")
      && !selectors(d.selector).every(inputLike)) hit("font-inherit", d.line, line(d), say(d));
  }
  // A control hidden at rest and shown by its row's :hover, where nothing
  // hovers too: hidden by opacity alone it still takes a tap.
  const rules = new Map();
  for (const d of decls) {
    const r = rules.get(d.rule) || rules.set(d.rule, { selector: d.selector, media: d.media, decls: [] }).get(d.rule);
    r.decls.push(d);
  }
  const passThrough = new Set();
  for (const r of rules.values()) if (r.decls.some((d) => d.prop === "pointer-events" && d.value === "none")) passThrough.add(`${r.media.join(" ")}|${r.selector}`);
  const hidden = new Map(); // class → [{ decl, ok }]
  for (const r of rules.values()) {
    const op = r.decls.findLast((d) => d.prop === "opacity");
    if (!op || parseFloat(op.value) !== 0) continue;
    const ok = underHover(r.media) || passThrough.has(`${r.media.join(" ")}|${r.selector}`);
    for (const s of selectors(r.selector)) {
      const last = compounds(s).at(-1) || "";
      if (/:(?:hover|focus)/.test(last)) continue;
      for (const c of classesOf(last)) (hidden.get(c) || hidden.set(c, []).get(c)).push({ decl: op, ok });
    }
  }
  const reported = new Set();
  for (const r of rules.values()) {
    const op = r.decls.findLast((d) => d.prop === "opacity");
    if (!op || !(parseFloat(op.value) > 0) || underHover(r.media)) continue;
    for (const s of selectors(r.selector)) {
      const cs = compounds(s);
      if (cs.length < 2 || !cs.slice(0, -1).some((c) => /:hover\b/.test(c))) continue;
      for (const c of classesOf(cs.at(-1))) {
        for (const h of hidden.get(c) || []) {
          if (h.ok || reported.has(h.decl)) continue;
          reported.add(h.decl);
          hit("hover-reveal", h.decl.line, line(h.decl), `${say(h.decl)}, shown by ${s}`);
        }
      }
    }
  }
  const comments = [];
  for (const m of css.matchAll(/\/\*[\s\S]*?\*\//g)) comments.push({ start: m.index, end: m.index + m[0].length, value: m[0].slice(2, -2) });
  return comments;
}

// ---------------------------------------------------------------- scan

// One file's breaks, the allows taken out: { found: [{rule, line, text}],
// problems: ["line: what is wrong with an allow"] }. `file` is its path
// under src/, which decides the owners and the exempt folders.
export function scanSource(code, file) {
  const raw = [];
  const hit = (rule, line, span, text) => raw.push({ rule, line, span, text });
  const comments = file.endsWith(".css") ? scanStyles(code, hit) : scanScript(code, file, hit);
  const { entries, problems } = readAllows(code, comments);
  const found = [];
  for (const f of raw) {
    const rule = RULES[f.rule];
    if (rule.owners?.includes(file) || Object.keys(rule.except || {}).some((dir) => file.startsWith(dir))) continue;
    const allows = entries.filter((e) => e.rule === f.rule && [...e.lines].some((n) => n >= f.span[0] && n <= f.span[1]));
    for (const e of allows) e.used = true;
    if (!allows.length) found.push({ rule: f.rule, line: f.line, text: f.text });
  }
  for (const e of entries) if (!e.used) problems.push(`${e.line}: ui-allow ${e.rule} exempts nothing`);
  found.sort((a, b) => a.line - b.line);
  return { found, problems: problems.sort((a, b) => parseInt(a) - parseInt(b)) };
}

// { found: { file: { rule: ["line: text", …] } }, problems: ["file:line: …"] }
// over every source file.
export function scan(files = sourceFiles(), dir = SRC) {
  const found = {}, problems = [];
  for (const f of files) {
    const file = rel(f, dir);
    let res;
    try {
      res = scanSource(fs.readFileSync(f, "utf8"), file);
    } catch (e) {
      problems.push(`${file}: ${e.message}`);
      continue;
    }
    for (const b of res.found) ((found[file] ||= {})[b.rule] ||= []).push(`${b.line}: ${b.text}`);
    for (const p of res.problems) problems.push(`${file}:${p}`);
  }
  return { found, problems };
}

// Counts per file and rule, the shape of the baseline.
export function counts(found = scan().found) {
  const out = {};
  for (const [file, rules] of Object.entries(found).sort(([a], [b]) => (a < b ? -1 : 1))) {
    out[file] = Object.fromEntries(RULE_IDS.filter((r) => rules[r]).map((r) => [r, rules[r].length]));
  }
  return out;
}

export const totals = (c) => Object.fromEntries(RULE_IDS.map((r) => [r, Object.values(c).reduce((n, f) => n + (f[r] || 0), 0)]).filter(([, n]) => n));

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const { found, problems } = scan();
  const c = counts(found);
  if (args[0] === "--baseline") {
    fs.writeFileSync(BASELINE, JSON.stringify(c, null, 2) + "\n");
    console.log(`wrote ${path.relative(FRONTEND, BASELINE)}`, totals(c));
  } else if (args[0] === "--list") {
    const only = args[1];
    if (only && !RULES[only]) { console.error(`no rule "${only}": ${RULE_IDS.join(", ")}`); process.exit(2); }
    for (const [file, rules] of Object.entries(found)) {
      for (const [r, list] of Object.entries(rules)) if (!only || r === only) for (const x of list) console.log(`${file}:${x}  [${r}]`);
    }
    if (only) console.log(`\n${only}: ${RULES[only].fix} (${DOC}${RULES[only].doc})`);
  } else {
    console.log(totals(c));
    for (const [file, rules] of Object.entries(c)) console.log(`  ${file}`, rules);
  }
  for (const p of problems) console.error(`allow: ${p}`);
}
