// Citation HTML as render.js gives it, wherever it is shown: the share
// popover's Reference, the metadata popover's Reference, the Settings
// pane's preview and the Export dialog's reference list. The engine
// sanitized it already (citations/cslCore.js: its own markup and http(s)
// links only) and gave it Zotero's inline layout, made for the clipboard: a
// word processor keeps it. On screen it is read as a page's text is
// (screenHtml): the style's print spacing (APA's double lines, Chicago's
// blank line between entries) gives way to the app's line height and an
// entry gap, the hanging indent moves onto each entry, and a long address
// may break where an address breaks. The copies are made from the render,
// never from this.
import React from "react";

// Where a long address may break, as the Chicago Manual has it: after //,
// and before a single slash (not before a period, which would split a
// DOI's 10.1103 prefix). Each part ends where a <wbr> goes; a part still
// longer than the line breaks anywhere (.citeHtml).
function addressParts(text) {
  const parts = [];
  let from = 0;
  for (let i = 1; i < text.length; i++) {
    const c = text[i];
    const breakBefore = c === "/" && text[i - 1] !== "/" && text[i + 1] !== "/";
    const breakAfterSlashes = text[i - 1] === "/" && text[i - 2] === "/" && c !== "/";
    if (breakBefore || breakAfterSlashes) { parts.push(text.slice(from, i)); from = i; }
  }
  parts.push(text.slice(from));
  return parts;
}

const textOf = (node) => (node?.textContent || "").replace(/\s+/g, " ").trim();

// The HTML as the screen shows it, with the entry whose text is `mark`'s
// (an entry of the same render, as render.js gives each) marked.
function screenHtml(html, mark) {
  if (typeof document === "undefined" || !html) return html || "";
  const box = document.createElement("template");
  box.innerHTML = html;
  const root = box.content;
  const body = root.querySelector(".csl-bib-body");
  if (body) {
    const indent = body.style.textIndent.startsWith("-") ? body.style.marginLeft : "";
    for (const prop of ["line-height", "margin-left", "text-indent"]) body.style.removeProperty(prop);
    for (const entry of root.querySelectorAll(".csl-entry")) {
      entry.style.removeProperty("margin-bottom");
      // The hanging indent on the entry itself, so its box (the marked
      // one's tint) holds its first line.
      if (indent) { entry.style.paddingLeft = indent; entry.style.textIndent = `-${indent}`; }
    }
    const wanted = mark ? textOf(entryOf(mark)) : "";
    if (wanted) {
      for (const entry of root.querySelectorAll(".csl-entry")) {
        if (textOf(entry) === wanted) { entry.classList.add("citeMark"); break; }
      }
    }
  }
  for (const link of root.querySelectorAll("a")) {
    const walker = document.createTreeWalker(link, NodeFilter.SHOW_TEXT);
    const texts = [];
    while (walker.nextNode()) texts.push(walker.currentNode);
    for (const node of texts) {
      const parts = addressParts(node.nodeValue);
      if (parts.length < 2) continue;
      const out = document.createDocumentFragment();
      parts.forEach((part, i) => {
        if (i) out.append(document.createElement("wbr"));
        out.append(part);
      });
      node.replaceWith(out);
    }
  }
  const out = document.createElement("div");
  out.append(root);
  return out.innerHTML;
}
// A render's lone entry (or the whole fragment), to read its text.
function entryOf(html) {
  const box = document.createElement("template");
  box.innerHTML = html;
  return box.content.querySelector(".csl-entry") || box.content;
}

// Scrolls the list's own scroller (the Export dialog's pane) so `el`, when
// not wholly in view, stands in its middle, and nothing around it: the
// dialog stays where it is.
function scrollIntoPane(el) {
  for (let box = el.parentElement; box; box = box.parentElement) {
    if (!/(auto|scroll)/.test(getComputedStyle(box).overflowY)) continue;
    if (box.scrollHeight <= box.clientHeight) return;
    const at = el.getBoundingClientRect(), view = box.getBoundingClientRect();
    if (at.top < view.top || at.bottom > view.bottom) box.scrollTop += at.top - view.top - Math.max(0, (view.height - at.height) / 2);
    return;
  }
}

// `mark`: an entry of this list (render.js's entries[i]) to tint and
// scroll to (the Export dialog's picked paper).
export function CitationHtml({ html, className = "", mark = null }) {
  const shown = React.useMemo(() => screenHtml(html, mark), [html, mark]);
  const ref = React.useRef(null);
  React.useEffect(() => {
    const marked = mark ? ref.current?.querySelector(".citeMark") : null;
    if (marked) scrollIntoPane(marked);
  }, [shown, mark]);
  return <div ref={ref} className={`citeHtml ${className}`.trim()} dangerouslySetInnerHTML={{ __html: shown }} />;
}
