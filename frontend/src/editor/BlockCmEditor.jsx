// The block editor: a single CodeMirror 6 instance (only the block being
// edited mounts one) that mimics the old textarea's API so blockTree keeps
// its key handling, [[ref]] popup and LaTeX autocomplete untouched, and adds
// Notion/Obsidian-style live rendering: a closed $...$ / $$...$$ span (or a
// [[block-ref]]) whose text the caret is NOT touching renders in place;
// moving the caret into it (arrow keys, or clicking the rendered chip)
// expands it back to source.
import React, { useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef } from "react";
import { Annotation, Compartment, EditorState, Prec, StateEffect, StateField } from "@codemirror/state";
import {
  Decoration, EditorView, WidgetType, keymap,
  placeholder as cmPlaceholder,
} from "@codemirror/view";
import { standardKeymap } from "@codemirror/commands";
import { findMathAtCursor, renderKatex } from "./LatexEditor";
import { emptyLeftPair, escapedAt, leftDelimiterEdit, rightDelimiterAt } from "./latexInput";
import { latexErrors, visibleLatexErrors } from "./latexLint";
import { calloutType } from "./callouts";
import { fenceInnerAt, highlightCode, makeCopyButton, scanFences } from "./codeHighlight";
import { scanColorSpans, scanImageSyntax, scanMarks, toggleMark } from "./mdMarks";
import { parseTable, scanImages, scanTables } from "./mdScan";
import { scanMathSpans } from "./markCommands";
import { assetUrl } from "../shared/lib/utils";

// Clicking a rendered widget drops the caret just inside it, which un-renders
// the span (the caret now touches it) so the source is editable in place.
function placeCaretInside(view, node, offsetFromStart) {
  const pos = view.posAtDOM(node);
  view.dispatch({ selection: { anchor: pos + offsetFromStart } });
  view.focus();
}

class MathWidget extends WidgetType {
  constructor(tex, display) {
    super();
    this.tex = tex;
    this.display = display;
  }
  eq(other) { return other.tex === this.tex && other.display === this.display; }
  toDOM(view) {
    const span = document.createElement("span");
    span.className = "cmMathWidget" + (this.display ? " cmMathDisplay" : "");
    const html = renderKatex(this.tex, this.display);
    if (html) span.innerHTML = html;
    else span.textContent = this.tex;
    span.addEventListener("mousedown", (e) => {
      e.preventDefault();
      placeCaretInside(view, span, this.display ? 2 : 1);
    });
    return span;
  }
  // Default ignoreEvent() → true: CM leaves the mousedown to our listener.
}

class RefChipWidget extends WidgetType {
  constructor(label, embed) {
    super();
    this.label = label;
    this.embed = embed; // ![[id]] transclusion — chip in the editor, card when rendered
  }
  eq(other) { return other.label === this.label && other.embed === this.embed; }
  toDOM(view) {
    const span = document.createElement("span");
    span.className = "blockRefChip cmRefChip" + (this.embed ? " cmEmbedChip" : "");
    span.textContent = (this.embed ? "⧉ " : "") + this.label;
    span.addEventListener("mousedown", (e) => {
      e.preventDefault();
      placeCaretInside(view, span, this.embed ? 3 : 2);
    });
    return span;
  }
}

// "- [ ] " task marker → a real checkbox; clicking it flips the char in the
// source (which autosaves through the normal onChange path).
class TaskCheckboxWidget extends WidgetType {
  constructor(checked, checkOffset) {
    super();
    this.checked = checked;
    this.checkOffset = checkOffset;
  }
  eq(other) { return other.checked === this.checked && other.checkOffset === this.checkOffset; }
  toDOM(view) {
    const input = document.createElement("input");
    input.type = "checkbox";
    input.className = "mdTaskCheckbox";
    input.checked = this.checked;
    input.addEventListener("mousedown", (e) => e.preventDefault());
    input.addEventListener("click", (e) => {
      e.preventDefault();
      const pos = view.posAtDOM(input) + this.checkOffset;
      view.dispatch({ changes: { from: pos, to: pos + 1, insert: this.checked ? " " : "x" } });
    });
    return input;
  }
}

class BulletWidget extends WidgetType {
  eq() { return true; }
  toDOM(view) {
    const span = document.createElement("span");
    span.className = "cmBulletDot";
    span.textContent = "•";
    span.addEventListener("mousedown", (e) => {
      e.preventDefault();
      placeCaretInside(view, span, 1);
    });
    return span;
  }
}

// A closed ``` fence the caret isn't touching renders as a highlighted code
// card (same in-place idiom as math). Clicking drops the caret at the start
// of the code so the fence expands back to source.
class CodeBlockWidget extends WidgetType {
  constructor(code, lang, caretOffset) {
    super();
    this.code = code;
    this.lang = lang;
    this.caretOffset = caretOffset;
  }
  eq(other) { return other.code === this.code && other.lang === this.lang; }
  toDOM(view) {
    const span = document.createElement("span");
    span.className = "cmCodeWidget";
    const pre = document.createElement("pre");
    const codeEl = document.createElement("code");
    codeEl.className = "hljs";
    codeEl.innerHTML = highlightCode(this.code, this.lang);
    pre.appendChild(codeEl);
    if (this.lang) {
      const badge = document.createElement("span");
      badge.className = "codeLangBadge";
      badge.textContent = this.lang;
      span.appendChild(badge);
    }
    span.appendChild(makeCopyButton(() => this.code));
    span.appendChild(pre);
    span.addEventListener("mousedown", (e) => {
      e.preventDefault();
      placeCaretInside(view, span, this.caretOffset);
    });
    return span;
  }
}

class HrWidget extends WidgetType {
  eq() { return true; }
  toDOM(view) {
    const span = document.createElement("span");
    span.className = "cmHrLine";
    span.addEventListener("mousedown", (e) => {
      e.preventDefault();
      placeCaretInside(view, span, 0);
    });
    return span;
  }
}

// An `![alt](url)` the caret isn't touching shows the picture (sized like the
// rendered view, alt as its caption). A picture is an object, not text: the
// caret stepping onto its line or resting at either end keeps the picture
// (only a selection reaching INSIDE the span shows the source), a click puts
// the caret after it, and a right-click drops the caret into the alt text so
// the source expands — the editor's counterpart of the rendered view's
// "Edit markdown source". The upload URL gets the workspace / share token
// like the rendered view's <img> — the browser fetches it without the API
// header.
// The object behaviour the image and table widgets share (the editor's
// counterpart of the rendered view's MdObject frame): a click puts the caret
// after the object, a right-click drops it inside so the source expands
// ("Edit markdown source"), and the widget is a drag source — ctx.objectDrag
// (BlockRow's object action) publishes it for App's block drop handlers, so
// a picture or table can be dragged to another block while its own block is
// being edited. The mousedown is left to the browser: preventing it would
// cancel the native drag.
function objectWidget(view, el, { kind, idx, length, ctx }) {
  el.dataset.kind = kind;
  el.dataset.idx = idx;
  el.draggable = idx >= 0;
  el.addEventListener("click", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const pos = view.posAtDOM(el);
    view.dispatch({ selection: { anchor: pos + length } });
    view.focus();
  });
  el.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    placeCaretInside(view, el, 1);
  });
  el.addEventListener("dragstart", (e) => {
    e.stopPropagation();
    if (idx < 0 || !ctx.objectDrag) { e.preventDefault(); return; }
    ctx.objectDrag(kind, idx, "start", e);
  });
  el.addEventListener("dragend", (e) => ctx.objectDrag?.(kind, idx, "end", e));
  return el;
}

class ImageWidget extends WidgetType {
  constructor(url, alt, width, idx, length, ctx) {
    super();
    this.url = url;
    this.alt = alt;
    this.width = width;
    this.idx = idx;
    this.length = length;
    this.ctx = ctx;
  }
  eq(other) {
    return other.url === this.url && other.alt === this.alt && other.width === this.width
      && other.idx === this.idx && other.length === this.length;
  }
  toDOM(view) {
    const span = document.createElement("span");
    span.className = "cmImgWidget";
    const img = document.createElement("img");
    img.className = "mdImg";
    img.src = assetUrl(this.url);
    img.alt = this.alt;
    if (this.width) img.width = this.width;
    img.draggable = false;
    // The line grows when the picture arrives: re-measure so the caret and
    // the row's layout follow.
    img.addEventListener("load", () => view.requestMeasure());
    span.appendChild(img);
    if (this.alt) {
      const cap = document.createElement("span");
      cap.className = "mdImgCaption";
      cap.textContent = this.alt;
      span.appendChild(cap);
    }
    return objectWidget(view, span, { kind: "image", idx: this.idx, length: this.length, ctx: this.ctx });
  }
}

// A GFM table as a plain read-only table (cells as their raw text), the same
// object rules as the picture: it stays a table while the caret steps past
// it, right-click shows the source, and it can be dragged to another block.
// Cells are edited in the rendered view, never as raw text (mdTools).
class TableWidget extends WidgetType {
  constructor(source, idx, ctx) {
    super();
    this.source = source;
    this.idx = idx;
    this.ctx = ctx;
  }
  eq(other) { return other.source === this.source && other.idx === this.idx; }
  toDOM(view) {
    const box = document.createElement("div");
    box.className = "cmTableWidget";
    const model = parseTable(this.source);
    const table = document.createElement("table");
    const cell = (tag, text, c) => {
      const td = document.createElement(tag);
      td.textContent = (text || "").replace(/\\\|/g, "|");
      if (model.aligns[c]) td.style.textAlign = model.aligns[c];
      return td;
    };
    const thead = document.createElement("thead");
    const hr = document.createElement("tr");
    model.header.forEach((h, c) => hr.appendChild(cell("th", h, c)));
    thead.appendChild(hr);
    table.appendChild(thead);
    const tbody = document.createElement("tbody");
    for (const row of model.body) {
      const tr = document.createElement("tr");
      model.header.forEach((_, c) => tr.appendChild(cell("td", row[c], c)));
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    box.appendChild(table);
    return objectWidget(view, box, { kind: "table", idx: this.idx, length: this.source.length, ctx: this.ctx });
  }
  ignoreEvent() { return true; }
}

// Live inline rendering (Obsidian-style): math as KaTeX, [[id]] refs as
// chips, and markdown constructs — heading/quote prefixes hidden with the
// line styled, **bold** / *italic* / `code` / ~~strike~~ / [text](url) shown
// formatted with their delimiters hidden. Any construct the selection
// touches stays raw source (boundaries inclusive, so stepping the caret onto
// it expands it) — except the objects (pictures, tables), which only a
// selection reaching inside expands. ctx is read lazily: `labels` so freshly
// resolved ref labels show up on the next rebuild, `objectDrag` the row's
// drag hook for the object widgets.
function buildInlineDecos(state, ctx) {
  const text = state.doc.toString();
  const sel = state.selection.main;
  const ranges = [];
  // Recognized spans claim their range even while shown raw, so e.g. a `**`
  // inside a math span or inline code never doubles as bold.
  const claimed = [];
  const overlapsClaimed = (from, to) => claimed.some(([a, b]) => from < b && to > a);
  const touched = (from, to) => sel.from <= to && sel.to >= from;

  // ``` fences claim their range FIRST — a "$" or "**" inside code is code.
  // A closed, untouched fence renders as a highlighted card; a touched or
  // still-open one stays raw and gets mono/tinted line styling below.
  const fences = text.includes("```") ? scanFences(text) : [];
  const rawFences = [];
  for (const f of fences) {
    claimed.push([f.from, f.to]);
    if (f.closed && !touched(f.from, f.to)) {
      const code = f.innerTo > f.innerFrom ? text.slice(f.innerFrom, f.innerTo - 1) : "";
      ranges.push(Decoration.replace({
        widget: new CodeBlockWidget(code, f.lang, f.innerFrom - f.from),
      }).range(f.from, f.to));
    } else {
      rawFences.push(f);
    }
  }

  // GFM tables (outside fences / display math, which scanTables skips):
  // a table widget unless the selection reaches inside it. Claimed so the
  // pipes and dashes never read as marks. Quoted tables stay raw text.
  if (text.includes("|")) {
    scanTables(text).forEach((tb, idx) => {
      if (!tb.editable || overlapsClaimed(tb.from, tb.to)) return;
      claimed.push([tb.from, tb.to]);
      if (sel.from < tb.to && sel.to > tb.from) return;
      ranges.push(Decoration.replace({
        widget: new TableWidget(text.slice(tb.from, tb.to), idx, ctx),
      }).range(tb.from, tb.to));
    });
  }

  // Raw (caret-touched) math spans collect here for the bracket rainbow pass.
  const rawMath = [];
  const mathSpans = text.includes("$") ? scanMathSpans(text) : [];
  for (const s of mathSpans) {
    if (overlapsClaimed(s.from, s.to)) continue;
    claimed.push([s.from, s.to]);
    const dlen = s.display ? 2 : 1;
    if (touched(s.from, s.to)) {
      rawMath.push({ from: s.from + dlen, to: s.to - dlen, display: s.display });
      ranges.push(Decoration.mark({ class: "cmMathRaw" }).range(s.from, s.to));
      continue;
    }
    const tex = text.slice(s.from + dlen, s.to - dlen);
    ranges.push(Decoration.replace({ widget: new MathWidget(tex, s.display) }).range(s.from, s.to));
  }
  // An UNCLOSED math span being typed at the caret is raw too — its brackets
  // should light up while the formula is only half-written.
  if (sel.empty) {
    const um = findMathAtCursor(text, sel.head);
    if (um
      && !mathSpans.some((s) => um.start >= s.from && um.end <= s.to)
      && !fences.some((f) => um.start >= f.from && um.start < f.to)) {
      rawMath.push({ from: um.start, to: um.end, display: um.display });
      ranges.push(Decoration.mark({ class: "cmMathRaw" })
        .range(um.start - (um.display ? 2 : 1), um.end));
    }
  }

  // VSCode-style bracket pair colorization inside raw math: ( [ { colored by
  // nesting depth (3-color cycle), the innermost pair enclosing the caret
  // lifted, unmatched brackets flagged.
  const OPENERS = "([{", CLOSERS = ")]}";
  for (const r of rawMath) {
    const stack = [], pairs = [], loose = [];
    for (let p = r.from; p < r.to; p++) {
      const oi = OPENERS.indexOf(text[p]);
      if (oi >= 0) { stack.push({ p, t: oi }); continue; }
      const ci = CLOSERS.indexOf(text[p]);
      if (ci < 0) continue;
      const top = stack[stack.length - 1];
      if (top && top.t === ci) {
        stack.pop();
        pairs.push({ open: top.p, close: p, depth: stack.length });
      } else loose.push(p);
    }
    for (const s of stack) loose.push(s.p);
    let active = null;
    if (sel.empty) {
      for (const pr of pairs) {
        if (pr.open < sel.head && sel.head <= pr.close
          && (!active || pr.open > active.open)) active = pr;
      }
    }
    for (const pr of pairs) {
      const cls = `cmBk${pr.depth % 3}${pr === active ? " cmBkActive" : ""}`;
      ranges.push(Decoration.mark({ class: cls }).range(pr.open, pr.open + 1));
      ranges.push(Decoration.mark({ class: cls }).range(pr.close, pr.close + 1));
    }
    for (const p of loose) ranges.push(Decoration.mark({ class: "cmBkErr" }).range(p, p + 1));
    // KaTeX's own verdict, underlined where it points (latexLint.js), the
    // message as the hover title. The range under the caret is being typed
    // and waits until the caret moves on.
    const caret = sel.empty && sel.head >= r.from && sel.head <= r.to ? sel.head - r.from : null;
    for (const e of visibleLatexErrors(latexErrors(text.slice(r.from, r.to), r.display), caret)) {
      ranges.push(Decoration.mark({ class: "cmLatexErr", attributes: { title: e.message } })
        .range(r.from + e.from, r.from + e.to));
    }
    // The \command the caret sits on lights up, like the active bracket pair.
    if (sel.empty) {
      for (const m of text.slice(r.from, r.to).matchAll(/\\[a-zA-Z]+/g)) {
        const from = r.from + m.index, to = from + m[0].length;
        if (sel.head >= from && sel.head <= to) {
          ranges.push(Decoration.mark({ class: "cmMathCmdActive" }).range(from, to));
          break;
        }
      }
    }
  }

  for (const m of text.matchAll(/!?\[\[([a-zA-Z0-9_-]+)\]\]/g)) {
    const from = m.index, to = m.index + m[0].length;
    if (overlapsClaimed(from, to)) continue;
    claimed.push([from, to]);
    if (touched(from, to)) continue;
    const label = ctx.labels?.[m[1]]?.content || m[1];
    ranges.push(Decoration.replace({
      widget: new RefChipWidget(label, m[0].startsWith("!")),
    }).range(from, to));
  }

  // ![alt](url): the picture itself, like the rendered view (same syntax
  // scan as mdTools' image editing). Claimed before the inline marks so a
  // `*` in the URL or alt never reads as emphasis, and skipped inside code
  // and math like every other construct.
  if (text.includes("![")) {
    // The object index the rendered view would give this picture (its
    // scan skips images in code and math like the claims here do).
    const objects = scanImages(text);
    for (const im of scanImageSyntax(text)) {
      if (overlapsClaimed(im.from, im.to)) continue;
      claimed.push([im.from, im.to]);
      // Strictly inside: a caret at either end (stepping through the lines,
      // End on its line) keeps the picture; the widget's right-click reveals.
      if (sel.from < im.to && sel.to > im.from) continue;
      const idx = objects.findIndex((o) => o.from === im.from);
      const widget = new ImageWidget(im.url, im.alt, im.width, idx, im.to - im.from, ctx);
      ranges.push(Decoration.replace({ widget }).range(im.from, im.to));
      // A picture alone on its line is centred, like the rendered view.
      const line = state.doc.lineAt(im.from);
      if (!text.slice(line.from, im.from).trim() && !text.slice(im.to, line.to).trim()) {
        ranges.push(Decoration.line({ class: "cmImgLine" }).range(line.from));
      }
    }
  }

  // Inline marks (**bold** etc., table in mdMarks.js — shared with the
  // formatting hotkeys): delimiters hidden, inner text gets the mark class.
  for (const { marker, cls, from, to } of scanMarks(text, claimed)) {
    if (touched(from, to)) continue;
    const dlen = marker.length;
    ranges.push(Decoration.replace({}).range(from, from + dlen));
    ranges.push(Decoration.mark({ class: cls }).range(from + dlen, to - dlen));
    ranges.push(Decoration.replace({}).range(to - dlen, to));
  }

  // Colored runs (`<span style="color:…">` / background tints, mdMarks
  // scanColorSpans — the Obsidian-compatible inline HTML the "/" color
  // commands write): tags hidden, the text shown in its color; touched, the
  // raw markup comes back like any other mark.
  for (const s of scanColorSpans(text)) {
    if (overlapsClaimed(s.from, s.to)) continue;
    claimed.push([s.from, s.to]);
    // An empty span (just inserted by the "/" command) stays raw too — there
    // is nothing to color, and a zero-length mark decoration throws.
    if (touched(s.from, s.to) || s.to - s.closeLen <= s.from + s.openLen) continue;
    ranges.push(Decoration.replace({}).range(s.from, s.from + s.openLen));
    ranges.push(Decoration.mark({ class: "cmColorSpan", attributes: { style: s.style } })
      .range(s.from + s.openLen, s.to - s.closeLen));
    ranges.push(Decoration.replace({}).range(s.to - s.closeLen, s.to));
  }

  // [text](url): show just the text, link-styled.
  for (const m of text.matchAll(/\[([^\]\n]+)\]\(([^)\n]+)\)/g)) {
    const from = m.index, to = m.index + m[0].length;
    if (text[from - 1] === "!" || text[from + 1] === "[") continue;
    if (overlapsClaimed(from, to)) continue;
    claimed.push([from, to]);
    if (touched(from, to)) continue;
    ranges.push(Decoration.replace({}).range(from, from + 1));
    ranges.push(Decoration.mark({ class: "cmLinkText" }).range(from + 1, from + 1 + m[1].length));
    ranges.push(Decoration.replace({}).range(from + 1 + m[1].length, to));
  }

  // Bare URLs: just link-styled (never hidden — the rendered view shows the
  // title chip). Claimed so emphasis regexes can't chew on URL punctuation.
  for (const m of text.matchAll(/https?:\/\/[^\s<>()"\]]+/g)) {
    const from = m.index, to = m.index + m[0].length;
    if (overlapsClaimed(from, to)) continue;
    claimed.push([from, to]);
    ranges.push(Decoration.mark({ class: "cmLinkText" }).range(from, to));
  }

  // Line constructs: headings, quotes/callouts, task and bullet markers,
  // horizontal rules. Heading/quote prefixes un-hide while the caret is
  // anywhere on their line; task/bullet markers only when the caret is
  // strictly INSIDE the marker (so editing a todo's text keeps its checkbox).
  const inside = (from, to) => sel.from < to && sel.to > from;
  const doc = state.doc;
  // Contiguous "> " lines form one quote run; a run opening with "[!type]"
  // renders as a callout: tinted lines, colored bold title, marker hidden.
  const quoteRun = [];
  const flushQuoteRun = () => {
    if (!quoteRun.length) return;
    // The marker may carry Obsidian's fold flag ("[!note]-" / "+"), hidden
    // with the rest of it (callouts.js CALLOUT_MARKER_RE).
    const co = quoteRun[0].line.text.match(/^> ?\[!(\w+)\][-+]?[ \t]*/);
    const type = co ? calloutType(co[1]) : null;
    quoteRun.forEach(({ line, prefixLen, lineTouched }, i) => {
      let cls = "cmQuoteLine";
      if (type) {
        cls += ` cmCalloutLine cmCallout-${type}`;
        if (i === 0) cls += " cmCalloutFirst";
        if (i === quoteRun.length - 1) cls += " cmCalloutLast";
      }
      ranges.push(Decoration.line({ class: cls }).range(line.from));
      const hideLen = i === 0 && type ? co[0].length : prefixLen;
      if (!lineTouched && hideLen && !overlapsClaimed(line.from, line.from + hideLen)) {
        ranges.push(Decoration.replace({}).range(line.from, line.from + hideLen));
        if (i === 0 && type && line.text.length > hideLen
          && !overlapsClaimed(line.from + hideLen, line.to)) {
          ranges.push(Decoration.mark({ class: "cmCalloutTitle" }).range(line.from + hideLen, line.to));
        }
      }
    });
    quoteRun.length = 0;
  };
  for (let i = 1; i <= doc.lines; i++) {
    const line = doc.line(i);
    // Lines inside a ``` fence are code, not markdown: no headings, bullets
    // or rules. Raw (caret-touched / unclosed) fences get mono styling with
    // dimmed marker lines; widget-replaced ones need no line decorations.
    const cf = fences.length
      ? fences.find((f) => line.from >= f.from && line.to <= f.to)
      : null;
    if (cf) {
      flushQuoteRun();
      if (rawFences.includes(cf)) {
        let cls = "cmCodeLine";
        if (line.from === cf.from) cls += " cmCodeFenceLine cmCodeTop";
        else if (cf.closed && line.from === cf.innerTo) cls += " cmCodeFenceLine cmCodeBot";
        ranges.push(Decoration.line({ class: cls }).range(line.from));
      }
      continue;
    }
    const lineTouched = touched(line.from, line.to);
    const q = /^> ?/.exec(line.text);
    if (q) {
      quoteRun.push({ line, prefixLen: q[0].length, lineTouched });
      continue;
    }
    flushQuoteRun();
    const h = /^(#{1,6}) /.exec(line.text);
    if (h) {
      ranges.push(Decoration.line({ class: `cmHeadLine cmH${h[1].length}` }).range(line.from));
      if (!lineTouched && !overlapsClaimed(line.from, line.from + h[0].length)) {
        ranges.push(Decoration.replace({}).range(line.from, line.from + h[0].length));
      }
      continue;
    }
    const task = /^(\s*)([-*+] \[)( |x|X)\] /.exec(line.text);
    if (task) {
      const mFrom = line.from + task[1].length;
      const mTo = line.from + task[0].length;
      const checked = task[3] !== " ";
      if (checked) ranges.push(Decoration.line({ class: "cmTaskDone" }).range(line.from));
      if (!inside(mFrom, mTo) && !overlapsClaimed(mFrom, mTo)) {
        ranges.push(Decoration.replace({
          widget: new TaskCheckboxWidget(checked, task[2].length),
        }).range(mFrom, mTo));
      }
      continue;
    }
    const bullet = /^(\s*)[-*+] (?!\[)\S/.exec(line.text);
    if (bullet) {
      const bFrom = line.from + bullet[1].length;
      if (!inside(bFrom, bFrom + 2) && !overlapsClaimed(bFrom, bFrom + 1)) {
        ranges.push(Decoration.replace({ widget: new BulletWidget() }).range(bFrom, bFrom + 1));
      }
      continue;
    }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line.text.trim()) && line.text.trim()
      && !lineTouched && !overlapsClaimed(line.from, line.to)) {
      ranges.push(Decoration.replace({ widget: new HrWidget() }).range(line.from, line.to));
    }
  }
  flushQuoteRun();

  return Decoration.set(ranges, true);
}

// A StateField, not a ViewPlugin: replace decorations that hide line breaks
// (multi-line $$math$$, ``` fences) are only allowed from state-level
// decoration sources — a plugin throws "Decorations that replace line breaks
// may not be specified via plugins".
function inlineRenderField(ctx) {
  return StateField.define({
    create: (state) => buildInlineDecos(state, ctx),
    update: (deco, tr) =>
      tr.docChanged || tr.selection ? buildInlineDecos(tr.state, ctx) : deco,
    provide: (f) => EditorView.decorations.from(f),
  });
}

// --- other people's carets ------------------------------------------------
// The peers editing THIS block (collab.js presence: {client, rev, anchor,
// head, color, name}) render as a coloured caret with a name tag plus a
// tinted selection. A StateField keyed by peer: a peer whose `rev` changed
// (a new caret report — standalone, or riding on its batch) is placed fresh
// from its offsets, in the text of the transaction that carries the effect
// (the batch's text change comes in the same render, and runs first); the
// others keep their carets mapped through every change, so a remote caret
// stays put while we type and someone else's edit shifts it correctly.
const setRemoteCursors = StateEffect.define();

class RemoteCaretWidget extends WidgetType {
  constructor(name, color) {
    super();
    this.name = name;
    this.color = color;
  }
  eq(other) { return other.name === this.name && other.color === this.color; }
  toDOM() {
    const span = document.createElement("span");
    span.className = `cmRemoteCaret peer-${this.color}`;
    span.setAttribute("data-name", this.name);
    return span;
  }
  ignoreEvent() { return true; }
}

function buildRemoteDecos(peers) {
  const ranges = [];
  for (const c of peers.values()) {
    const from = Math.min(c.anchor, c.head), to = Math.max(c.anchor, c.head);
    if (from < to) ranges.push(Decoration.mark({ class: `cmRemoteSel peer-${c.color}` }).range(from, to));
    ranges.push(Decoration.widget({ widget: new RemoteCaretWidget(c.name || "", c.color || 0), side: 1 }).range(c.head));
  }
  return Decoration.set(ranges, true);
}

// state: { peers: Map<client, {rev, anchor, head, color, name}> (positions
// kept current in this editor's text), deco: the decorations built from it }
const remoteCursorField = StateField.define({
  create: () => ({ peers: new Map(), deco: Decoration.none }),
  update(st, tr) {
    let peers = st.peers;
    let changed = false;
    if (tr.docChanged && peers.size) {
      // Like a caret: an insertion at the position lands before it.
      peers = new Map([...peers].map(([k, c]) => [k, {
        ...c, anchor: tr.changes.mapPos(c.anchor, 1), head: tr.changes.mapPos(c.head, 1),
      }]));
      changed = true;
    }
    for (const e of tr.effects) {
      if (!e.is(setRemoteCursors)) continue;
      const len = tr.state.doc.length;
      const clamp = (n) => Math.max(0, Math.min(Number(n) || 0, len));
      const next = new Map();
      for (const c of e.value || []) {
        const key = c.client || `${c.color}:${c.name}`;
        const prev = peers.get(key);
        next.set(key, prev && prev.rev === c.rev && prev.color === c.color && prev.name === c.name
          ? prev
          : { rev: c.rev, anchor: clamp(c.anchor), head: clamp(c.head), color: c.color || 0, name: c.name || "" });
      }
      peers = next;
      changed = true;
    }
    return changed ? { peers, deco: buildRemoteDecos(peers) } : st;
  },
  provide: (f) => EditorView.decorations.from(f, (st) => st.deco),
});

// A value change that came from outside the editor (a remote edit, an undo
// restore): applied as the smallest replacement so the caret and the remote
// carets map through, and never reported back as a local change.
const externalSync = Annotation.define();

// Textarea-compatible facade + component. blockTree talks to ref.current
// exactly like it talked to the textarea (value / selectionStart / focus /
// setSelectionRange / getBoundingClientRect), plus caretCoords(index) which
// replaces the hidden-mirror caret measurement.
// --- "$" auto-pairing --------------------------------------------------------
// Typing "$" closes the pair ("$|$"); typing "$" again inside the fresh empty
// pair upgrades it to display math ("$$|$$"); with text selected "$" wraps it;
// a "$" typed right before an existing "$" types over instead of doubling the
// closer. Inside ``` code a "$" is a shell variable and an escaped "\$" a
// literal dollar — both stay plain.
// A caret between two dollars is textually ambiguous: "$|$" could be the
// fresh pair the previous keystroke auto-closed (where another "$" should
// upgrade to "$$|$$") or the two CLOSERS of "$$…$$" being typed over (where
// doubling them would corrupt the formula). Only the auto-close itself knows,
// so it leaves a marker that the very next "$" at the same spot consumes.
const _freshPair = new WeakMap(); // view → {pos, len}

const dollarPairing = EditorView.inputHandler.of((view, from, to, insert) => {
  if (insert !== "$") return false;
  const doc = view.state.doc.toString();
  if (fenceInnerAt(doc, from) || doc[from - 1] === "\\") return false;
  const sel = view.state.selection.main;
  if (!sel.empty) {
    view.dispatch({
      changes: [{ from: sel.from, insert: "$" }, { from: sel.to, insert: "$" }],
      selection: { anchor: sel.from + 1, head: sel.to + 1 },
      userEvent: "input.type",
    });
    return true;
  }
  const next = doc[from];
  const fp = _freshPair.get(view);
  if (doc[from - 1] === "$" && next === "$"
    && fp && fp.pos === from && fp.len === doc.length) {
    _freshPair.delete(view);
    view.dispatch({                          // fresh "$|$" → "$$|$$"
      changes: [{ from, insert: "$" }, { from: from + 1, insert: "$" }],
      selection: { anchor: from + 1 },
      userEvent: "input.type",
    });
    return true;
  }
  if (next === "$") {                        // type over an existing closer
    view.dispatch({ selection: { anchor: from + 1 }, userEvent: "select" });
    return true;
  }
  view.dispatch({
    changes: { from, to, insert: "$$" },
    selection: { anchor: from + 1 },
    userEvent: "input.type",
  });
  _freshPair.set(view, { pos: from + 1, len: view.state.doc.length });
  return true;
});

// Backspace between the dollars of an empty pair deletes both — "$$|$$"
// steps down to "$|$" first, then to nothing.
const dollarBackspace = keymap.of([{
  key: "Backspace",
  run: (view) => {
    const sel = view.state.selection.main;
    if (!sel.empty) return false;
    if (view.state.doc.sliceString(sel.head - 1, sel.head + 1) !== "$$") return false;
    view.dispatch({
      changes: { from: sel.head - 1, to: sel.head + 1 },
      userEvent: "delete.backward",
    });
    return true;
  },
}]);

// --- bracket auto-pairing inside math ----------------------------------------
// VSCode-like, scoped to $...$/$$...$$ spans (prose and code-fence brackets
// stay plain characters): typing ( [ { closes the pair with the caret
// between — "\{" pairs LaTeX-style with "\}" — typing a closer over an
// existing one steps past it instead of doubling, a selection gets wrapped
// (and stays selected, ready for the next wrap), and Backspace between an
// empty pair deletes both.
const BRACKET_PAIRS = { "(": ")", "[": "]", "{": "}" };
const BRACKET_CLOSERS = new Set([")", "]", "}"]);

const mathBracketPairing = EditorView.inputHandler.of((view, from, to, insert) => {
  if (view.composing || insert.length !== 1) return false;
  const doc = view.state.doc.toString();
  if (fenceInnerAt(doc, from)) return false;
  const seg = findMathAtCursor(doc, from);
  if (!seg) return false;
  const sel = view.state.selection.main;
  const scaled = leftDelimiterEdit(doc, from, to, insert, seg.start, seg.end);
  if (scaled && to <= seg.end) {
    view.dispatch({ ...scaled, userEvent: "input.type" });
    return true;
  }
  const right = sel.empty && rightDelimiterAt(doc, from);
  if (right && [")", "]", "}", "|"].includes(insert) && right.endsWith(insert)) {
    view.dispatch({ selection: { anchor: from + right.length }, userEvent: "select" });
    return true;
  }
  const close = BRACKET_PAIRS[insert];
  if (!close && !BRACKET_CLOSERS.has(insert)) return false;
  if (!sel.empty) {
    if (!close || sel.to > seg.end) return false;
    view.dispatch({
      changes: [{ from: sel.from, insert }, { from: sel.to, insert: close }],
      selection: { anchor: sel.from + 1, head: sel.to + 1 },
      userEvent: "input.type",
    });
    return true;
  }
  if (escapedAt(doc, from)) {
    if (insert !== "{") return false;          // literal \) \] \} etc.
    view.dispatch({
      changes: { from, insert: "{\\}" },
      selection: { anchor: from + 1 },
      userEvent: "input.type",
    });
    return true;
  }
  if (!close) {                                // a closer: type over
    if (doc[from] !== insert) return false;
    view.dispatch({ selection: { anchor: from + 1 }, userEvent: "select" });
    return true;
  }
  view.dispatch({
    changes: { from, to, insert: insert + close },
    selection: { anchor: from + 1 },
    userEvent: "input.type",
  });
  return true;
});

const mathBracketBackspace = keymap.of([{
  key: "Backspace",
  run: (view) => {
    const sel = view.state.selection.main;
    if (!sel.empty) return false;
    const doc = view.state.doc.toString();
    if (fenceInnerAt(doc, sel.head) || !findMathAtCursor(doc, sel.head)) return false;
    const scaled = emptyLeftPair(doc, sel.head);
    if (scaled) {
      view.dispatch({ changes: scaled, selection: { anchor: scaled.from }, userEvent: "delete.backward" });
      return true;
    }
    const esc = doc.slice(sel.head - 2, sel.head + 2) === "\\{\\}";
    if (!esc && BRACKET_PAIRS[doc[sel.head - 1]] !== doc[sel.head]) return false;
    view.dispatch({
      changes: { from: sel.head - (esc ? 2 : 1), to: sel.head + (esc ? 2 : 1) },
      userEvent: "delete.backward",
    });
    return true;
  },
}]);

// The raw source is usually taller than the rendered view it replaced, so
// the clicked text moves when the editor opens: scroll the notes by however
// far the caret's line landed from the pointer, keeping it where it was.
function keepUnderPointer(view, pos, y) {
  const c = view.coordsAtPos(pos);
  if (!c) return;
  const delta = (c.top + c.bottom) / 2 - y;
  if (Math.abs(delta) < 4) return;
  for (let el = view.dom.parentElement; el; el = el.parentElement) {
    const oy = getComputedStyle(el).overflowY;
    if ((oy === "auto" || oy === "scroll") && el.scrollHeight > el.clientHeight) {
      el.scrollTop += delta;
      return;
    }
  }
  document.scrollingElement?.scrollBy(0, delta);
}

const BlockCmEditor = React.forwardRef(function BlockCmEditor({
  value, onChange, onSelect, onKeyDown, onBlur, onPaste,
  placeholder, autoFocus, clickPos, dataBlockId, className, refLabels, remoteCursors, onObjectDrag,
}, forwardedRef) {
  const hostRef = useRef(null);
  const viewRef = useRef(null);
  const cbRef = useRef({});
  cbRef.current = { onChange, onSelect, onKeyDown, onBlur, onPaste };
  // What the decoration pass reads lazily (see buildInlineDecos).
  const labelsRef = useRef({ labels: refLabels, objectDrag: onObjectDrag }).current;
  labelsRef.labels = refLabels;
  labelsRef.objectDrag = onObjectDrag;
  const chipCompartment = useRef(new Compartment()).current;

  const api = useMemo(() => ({
    get value() { return viewRef.current ? viewRef.current.state.doc.toString() : ""; },
    get selectionStart() { return viewRef.current ? viewRef.current.state.selection.main.from : 0; },
    get selectionEnd() { return viewRef.current ? viewRef.current.state.selection.main.to : 0; },
    setSelectionRange(anchor, head) {
      const view = viewRef.current;
      if (!view) return;
      const len = view.state.doc.length;
      view.dispatch({
        selection: {
          anchor: Math.max(0, Math.min(anchor, len)),
          head: Math.max(0, Math.min(head ?? anchor, len)),
        },
      });
    },
    focus() { viewRef.current?.focus(); },
    getBoundingClientRect() {
      return hostRef.current?.getBoundingClientRect() || { left: 0, top: 0, right: 0, bottom: 0 };
    },
    caretCoords(index) {
      const view = viewRef.current;
      if (!view) return { left: 0, top: 0, bottom: 0 };
      const c = view.coordsAtPos(Math.max(0, Math.min(index, view.state.doc.length)));
      if (c) return { left: c.left, top: c.top, bottom: c.bottom };
      const r = this.getBoundingClientRect();
      return { left: r.left, top: r.top, bottom: r.bottom };
    },
    get view() { return viewRef.current; },
  }), []);
  useImperativeHandle(forwardedRef, () => api, [api]);

  useLayoutEffect(() => {
    const state = EditorState.create({
      doc: value || "",
      extensions: [
        EditorView.lineWrapping,
        // No history() here: undo is the page's one block history
        // (blockHistory.js) — Ctrl+Z bubbles to the window listener, which
        // restores the block's text and hands the caret back.
        // Our keydown runs before CM's keymaps so Enter/Tab/etc. keep the
        // outliner semantics from blockTree; anything not preventDefault-ed
        // falls through to the default editing keymap.
        Prec.highest(EditorView.domEventHandlers({
          keydown: (e) => { cbRef.current.onKeyDown?.(e); return e.defaultPrevented; },
          paste: (e) => { cbRef.current.onPaste?.(e); return e.defaultPrevented; },
          // Alt+Tab / app switch blurs the element too, but it stays
          // document.activeElement and the browser refocuses it on return —
          // only a blur while the window itself has focus (clicking away)
          // closes the editing session. Content is safe either way: every
          // keystroke already went through onChange → debounced autosave.
          blur: () => { if (document.hasFocus()) cbRef.current.onBlur?.(); },
        })),
        dollarPairing,
        dollarBackspace,
        mathBracketPairing,
        mathBracketBackspace,
        // Only the basic editing keys (caret movement, Home/End, selection
        // by word…): every shortcut above that — formatting, line and block
        // operations — is a command of editor/blockCommands.js, dispatched by
        // the row's keydown before this keymap sees the event, so the
        // catalog is the one place a key is declared (docs/dev/hotkeys.md).
        keymap.of(standardKeymap),
        cmPlaceholder(placeholder || ""),
        chipCompartment.of(inlineRenderField(labelsRef)),
        remoteCursorField,
        EditorView.updateListener.of((u) => {
          if (u.transactions.some((tr) => tr.annotation(externalSync))) {
            // Not our edit, but it moved our caret (a remote change before
            // it, a merged save): tell the others where we are now.
            if (u.docChanged) cbRef.current.onSelect?.({ target: api });
            return;
          }
          if (u.docChanged) {
            // The selection the change started from — the history stores it
            // with the entry so undo can put the cursor back there.
            const s = u.startState.selection.main;
            cbRef.current.onChange?.({ target: api, selectionBefore: { from: s.from, to: s.to } });
          }
          else if (u.selectionSet) cbRef.current.onSelect?.({ target: api });
        }),
      ],
    });
    const view = new EditorView({ state, parent: hostRef.current });
    viewRef.current = view;
    if (autoFocus) view.focus();
    // Caret placement on entering edit mode: at the clicked character when
    // the click mapped to a source offset (clickToSource.js), else at the
    // clicked coords, else at the end. A click on the gap line between two
    // blocks may ask for a new empty line there (insertLine) — a normal edit.
    let pos = view.state.doc.length;
    if (clickPos) {
      const p = clickPos.offset ?? view.posAtCoords({ x: clickPos.x, y: clickPos.y });
      if (p != null) pos = Math.max(0, Math.min(p, pos));
    }
    if (clickPos?.insertLine) view.dispatch({ changes: { from: pos, insert: "\n" }, selection: { anchor: pos } });
    else view.dispatch({ selection: { anchor: pos } });
    if (clickPos) keepUnderPointer(view, pos, clickPos.y);
    // Opened by a press on the rendered text: while the button stays down
    // the drag selects, as in any editor — the press landed on the rendered
    // view, so CodeMirror never saw it start and follows it from here.
    let stopDrag = null;
    if (clickPos && !clickPos.insertLine) {
      const onMove = (e) => {
        if (!(e.buttons & 1)) { stopDrag(); return; }
        if (Math.hypot(e.clientX - clickPos.x, e.clientY - clickPos.y) < 4) return;
        view.dispatch({ selection: { anchor: pos, head: view.posAtCoords({ x: e.clientX, y: e.clientY }, false) } });
      };
      stopDrag = () => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", stopDrag);
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", stopDrag);
    }
    return () => { stopDrag?.(); view.destroy(); viewRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // External value changes (a remote edit, an undo restore, programmatic
  // inserts) sync in as the minimal prefix/suffix replacement — the caret
  // maps through instead of jumping; self-originated edits arrive equal.
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const cur = view.state.doc.toString();
    const next = value || "";
    if (next === cur) return;
    const max = Math.min(cur.length, next.length);
    let head = 0;
    while (head < max && cur.charCodeAt(head) === next.charCodeAt(head)) head++;
    let tail = 0;
    while (tail < max - head && cur.charCodeAt(cur.length - 1 - tail) === next.charCodeAt(next.length - 1 - tail)) tail++;
    view.dispatch({
      changes: { from: head, to: cur.length - tail, insert: next.slice(head, next.length - tail) },
      annotations: externalSync.of(true),
    });
  }, [value]);

  // Peers' carets in this block.
  const cursorsKey = (remoteCursors || []).map((c) => `${c.client}:${c.rev}:${c.color}:${c.anchor}:${c.head}:${c.name}`).join("|");
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({ effects: setRemoteCursors.of(remoteCursors || []) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cursorsKey]);

  // Ref labels resolve asynchronously (onFetchRefs); refresh the chip
  // decorations when their text actually changes, not on every render.
  const labelsKey = Object.entries(refLabels || {})
    .map(([id, r]) => `${id}:${r?.content}`).join(" ");
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({ effects: chipCompartment.reconfigure(inlineRenderField(labelsRef)) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [labelsKey]);

  return (
    <div
      ref={hostRef}
      className={className}
      data-block-id={dataBlockId}
      data-guide="notes.editor"
    />
  );
});

export { BlockCmEditor, scanMathSpans };
