// A note editor's links (docs/dev/mentions.md "The link card"), for
// BlockCmEditor.jsx: a web link drawn as the rendered view's chip
// (linkChip), an address pasted over selected words (pasteLinkOver), and
// the link card under the link the caret is in, with its form
// (useEditorLinkCard). The links are linkEdit.js's spans; the card is
// shared/ui/LinkCard.jsx's.
import React, { useContext, useEffect, useMemo, useRef, useState } from "react";
import { WidgetType } from "@codemirror/view";
import { applyLinkChange, linkMarkdown, linkSpanAt, linkSpans, pastedLink, spanWords } from "./linkEdit.js";
import { protectedSpans, tablePipe } from "./mdScan.js";
import { LinkCardContext, LinkCardSurface, describeLink } from "../shared/ui/LinkCard.jsx";
import { bareLinkText, loadSitePreview, siteIconUrl, sitePreview, useSitePreview, webLinkLabel } from "../shared/lib/webLinks.js";
import { parseGammaLink } from "../shared/model/gammaLinks.js";
import { plainSnippet } from "../search/snippets.js";

// A web link as the rendered view's chip (BlockTree.jsx LinkChip): the
// site's icon, or the link icon for a link into this library (`own`), and
// its label; a bare address's (`fetches`) becomes its page's title once
// /api/link-preview answers (webLinks.js loadSitePreview). The address is
// its hover title. A press shows its link card without opening the source
// (ctx.pressLink); with Ctrl or Cmd it opens the link.
const LINK_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>';
class LinkChipWidget extends WidgetType {
  constructor(href, words, label, { own = false, fetches = false }, ctx) {
    super();
    this.href = href;
    this.words = words;
    this.label = label;
    this.own = own;
    this.fetches = fetches;
    this.ctx = ctx;
  }
  eq(other) {
    return other.href === this.href && other.label === this.label && other.own === this.own && other.fetches === this.fetches;
  }
  toDOM(view) {
    const chip = document.createElement("span");
    chip.className = "linkChip cmLinkChip";
    chip.title = this.href;
    const icon = this.own ? "" : siteIconUrl(this.href);
    if (icon) {
      const img = document.createElement("img");
      img.className = "linkChipIcon";
      img.src = icon;
      img.alt = "";
      img.addEventListener("error", () => img.replaceWith(document.createRange().createContextualFragment(LINK_SVG)));
      chip.appendChild(img);
    } else chip.insertAdjacentHTML("beforeend", LINK_SVG);
    const text = document.createElement("span");
    text.className = "linkChipText";
    text.textContent = this.label;
    chip.appendChild(text);
    if (this.fetches) {
      loadSitePreview(this.href).then((p) => {
        if (!p.title) return;
        text.textContent = webLinkLabel(this.href, this.words, p.title).label;
        view.requestMeasure();
      });
    }
    chip.addEventListener("mousedown", (e) => {
      e.preventDefault();
      if (e.button !== 0) return;
      if (e.ctrlKey || e.metaKey) {
        window.open(new URL(this.href, window.location.origin).href, "_blank", "noopener,noreferrer");
        return;
      }
      this.ctx.pressLink?.(view, view.posAtDOM(chip));
    });
    return chip;
  }
}

// The chip of a web link span (linkEdit.js linkSpans): its words (a
// Markdown link's, as plain text), else what it points to — a link into
// this library its card's title, any other the rendered chip's label.
export function linkChip(span, ctx) {
  const words = span.kind === "md" ? plainSnippet(spanWords(span)) : "";
  const gl = parseGammaLink(span.href, window.location.origin);
  if (gl && !gl.foreign) {
    const label = bareLinkText(words)
      ? describeLink({ kind: "url", href: span.href, label: "" }, { labels: ctx.labels, ctx: ctx.linkCtx }).title : words;
    return new LinkChipWidget(span.href, words, label, { own: true }, ctx);
  }
  const known = sitePreview(span.href);
  const { label, fetches } = webLinkLabel(span.href, words, known?.title);
  return new LinkChipWidget(span.href, words, label, { fetches: fetches && !known }, ctx);
}

// An address pasted over selected words, in any note editor: the words
// linked to it ([words](url), a copied page link [[id|words]]: linkEdit.js
// pastedLink), not replaced by it. Not in code or math, nor with files on
// the clipboard; true when it took the paste.
export function pasteLinkOver(e, view) {
  const sel = view.state.selection.main;
  if (view.state.readOnly || sel.empty || e.clipboardData?.files?.length) return false;
  const doc = view.state.doc.toString();
  if (protectedSpans(doc).some((s) => sel.from < s.to && sel.to > s.from)) return false;
  const md = pastedLink(doc.slice(sel.from, sel.to), e.clipboardData?.getData("text/plain") || "",
    window.location.origin, tablePipe(doc, sel.from));
  if (!md) return false;
  e.preventDefault();
  view.dispatch({ changes: { from: sel.from, to: sel.to, insert: md }, selection: { anchor: sel.from + md.length },
    userEvent: "input.paste", scrollIntoView: true });
  return true;
}

// The link card of the editor (shared/ui/LinkCard.jsx), under the link
// `card.span` the caret is in (linkEdit.js linkSpans) or, from Ctrl+K on
// words that are no link, the selection a new link takes ({kind: "new"}).
// Its edits are the editor's own changes; the span is found again by its
// text if the note changed under an open form, and nothing is written when
// it is gone.
function EditorLinkCard({ view, card, labels, blockId, onEditing, onClose }) {
  const ctx = useContext(LinkCardContext);
  const { span } = card;
  const href = span.kind === "md" || span.kind === "bare" ? span.href : "";
  const site = useSitePreview(href, !!href);
  const info = span.kind === "new" ? { title: "", target: null }
    : describeLink(span.kind === "ref" ? { kind: "ref", id: span.id, words: span.words } : { kind: "url", href, label: spanWords(span) },
      { labels, ctx, site });
  const write = (change) => {
    const doc = view.state.doc.toString();
    let at = span;
    if (doc.slice(span.from, span.to) !== span.text) {
      const i = span.text ? doc.indexOf(span.text) : Math.min(span.from, doc.length);
      if (i < 0) return;
      at = { ...span, from: i, to: i + span.text.length };
    }
    let insert;
    if (at.kind === "new") insert = linkMarkdown(change.to, change.words, tablePipe(doc, at.from));
    else {
      const next = applyLinkChange(doc, at, change);
      if (next == null) return;
      insert = next.slice(at.from, next.length - (doc.length - at.to));
    }
    view.dispatch({ changes: { from: at.from, to: at.to, insert }, selection: { anchor: at.from + insert.length }, userEvent: "input" });
  };
  const edit = view.state.readOnly ? null : {
    words: span.kind === "new" ? span.words : spanWords(span), isNew: span.kind === "new",
    canRemove: span.kind === "ref" || span.kind === "md", excludeId: blockId,
    onSave: write, onRemove: () => write({ remove: true, shown: info.title }),
  };
  const anchorRect = () => {
    const len = view.state.doc.length;
    const a = view.coordsAtPos(Math.min(span.from, len), 1), b = view.coordsAtPos(Math.min(span.to, len), -1) || a;
    if (!a) return view.dom.getBoundingClientRect();
    return { left: a.left, right: b.right, top: Math.min(a.top, b.top), bottom: Math.max(a.bottom, b.bottom) };
  };
  return <LinkCardSurface info={info} edit={edit} pinned editing={card.editing} anchorRect={anchorRect}
    onEditing={onEditing} onClose={onClose} />;
}


// The editor's link card ({span, editing}: EditorLinkCard). It shows while
// the caret, put there by a press or a key, stands inside a link, and after
// a press on a link's chip, until the caret moves; typing hides it. Its
// form (Ctrl+K, a Link button, its ✎) holds it until it closes, and a press
// outside the editor and the form closes both (`cbRef.current.onBlur`: the
// editor's blur skipped the card's fields). `decoCtx`, the decoration
// pass's context, gets the cards' context (`linkCtx`) and a chip's press
// (`pressLink`). The answer's functions stay the same for the editor's
// life, for the extensions it makes once: `update(u)` an update
// listener's, `openForm()` linkCardHost.js's, `escape()` true when Escape
// closed the card; `element` is the card.
export function useEditorLinkCard({ viewRef, hostRef, cbRef, decoCtx, labels, blockId }) {
  decoCtx.linkCtx = useContext(LinkCardContext);
  const [card, setCard] = useState(null);
  const cardRef = useRef(null);
  cardRef.current = card;
  const at = (span, doc, editing = null) => ({ span: { ...span, text: doc.slice(span.from, span.to) }, editing });
  const links = useMemo(() => ({
    // A press on a link chip (LinkChipWidget): the caret after the link,
    // and its card, pinned.
    press(view, from) {
      const doc = view.state.doc.toString();
      const span = linkSpans(doc).find((s) => s.from === from);
      if (!span) return;
      view.focus();
      view.dispatch({ selection: { anchor: span.to } });
      setCard(at(span, doc));
    },
    update(u) {
      const cur = cardRef.current;
      if (cur?.editing) return;
      if (u.docChanged) { if (cur) setCard(null); return; }
      if (!u.selectionSet && !u.focusChanged) return;
      const sel = u.state.selection.main;
      const doc = u.state.doc.toString();
      const span = u.view.hasFocus && sel.empty ? linkSpanAt(doc, sel.head) : null;
      if (!span || !(span.from < sel.head && sel.head < span.to)) { if (cur) setCard(null); return; }
      if (cur && cur.span.from === span.from && cur.span.to === span.to) return;
      setCard(at(span, doc));
    },
    // Ctrl+K and the Link buttons: the form, on the link the caret is on,
    // else a new link on the selected words.
    openForm() {
      const view = viewRef.current;
      if (!view || view.state.readOnly) return;
      const doc = view.state.doc.toString();
      const sel = view.state.selection.main;
      const span = sel.empty ? linkSpanAt(doc, sel.head) : linkSpans(doc).find((s) => s.from <= sel.from && sel.to <= s.to);
      setCard(span ? at(span, doc, "text")
        : { span: { kind: "new", from: sel.from, to: sel.to, words: doc.slice(sel.from, sel.to), text: doc.slice(sel.from, sel.to) }, editing: "link" });
    },
    // The card takes Escape before the editor closes; its form takes its own.
    escape() {
      if (!cardRef.current || cardRef.current.editing) return false;
      setCard(null);
      return true;
    },
  }), []);
  decoCtx.pressLink = links.press;
  useEffect(() => {
    if (!card?.editing) return undefined;
    const away = (e) => {
      if (e.target.closest?.(".linkCard")) return;
      setCard(null);
      if (!hostRef.current?.contains(e.target)) cbRef.current.onBlur?.();
    };
    document.addEventListener("pointerdown", away, true);
    return () => document.removeEventListener("pointerdown", away, true);
  }, [!!card?.editing]);
  const element = card && viewRef.current ? (
    <EditorLinkCard view={viewRef.current} card={card} labels={labels} blockId={blockId}
      onEditing={(field) => {
        setCard((c) => (c ? { ...c, editing: field } : c));
        if (!field) viewRef.current?.focus();
      }}
      onClose={() => { setCard(null); viewRef.current?.focus(); }} />
  ) : null;
  return { ...links, element };
}
