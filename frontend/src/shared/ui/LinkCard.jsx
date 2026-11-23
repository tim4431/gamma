// The link card (docs/dev/mentions.md "The link card"): what a link points
// to, and the one place to change it. The citation pill's preview made
// general: a bold title, a muted line saying what and where, a quote when
// there is one, "Open … ↵" — and, where the note may be edited, Edit, Copy
// link and Remove link. Edit turns the same card into two fields: Text (the
// words the link shows, empty for its target's own) and Link (its target:
// type to search pages and notes as the [[ picker does, or paste an
// address, a DOI or an arXiv id).
//
// Every link of a note's text uses it: a page or note link ([[id]],
// [[id|words]]), a web link (with a label or bare), a link into this
// library and a citation. `LinkHover` wraps a rendered link: hovering or
// focusing it previews the card, a right-click or a held finger pins it
// (menuPress), as Gamma opens a menu. The note editor shows it, pinned,
// under the link the caret is on (BlockCmEditor.jsx). `describeLink` says
// what a card shows for a link, the same everywhere.
import React, { createContext, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { CheckIcon, CopyIcon, ExternalLinkIcon, LinkIcon, PencilIcon, UnlinkIcon, XIcon } from "./Icons";
import FileIcon from "./FileIcon.jsx";
import { useMenuAim } from "./menuAim";
import { menuAnchor, placeFloating, surfaceSize, useFloatingPosition } from "./placement.js";
import { menuPress } from "./press.js";
import { blockLinkUrl, copyText } from "../lib/utils";
import { isSourceLink, parseGammaLink, gammaLinkId } from "../model/gammaLinks.js";
import { t } from "../i18n/i18n.js";
import { RefRows } from "../../editor/RefPicker.jsx";
import { useRefRows } from "../../editor/useRefPicker.js";
import { isLinkInput, normalizeLinkInput, pageKindLabel } from "../../library/libraryUtils";
import { plainSnippet } from "../../search/snippets.js";

// What App gives every card: the library's pages (`pages`, pageBlocksOf),
// the [[ref]] labels (`refCache`) and their cache's writer (`onCacheRef`),
// and navigation (`nav`, GammaNavContext's). One object for the app's life,
// its fields read when a card shows (getters), so no link renders again
// when they change.
export const LinkCardContext = createContext(null);

// App's value for LinkCardContext from the latest `values` ({pages,
// refCache, onCacheRef, nav}).
export function useLinkCardValue(values) {
  const ref = useRef(values);
  ref.current = values;
  return useMemo(() => ({
    get pages() { return ref.current.pages; },
    get refCache() { return ref.current.refCache; },
    get nav() { return ref.current.nav; },
    onCacheRef: (...a) => ref.current.onCacheRef?.(...a),
  }), []);
}

// --- a web page's title --------------------------------------------------------

// GET /api/link-preview's {title, author} of an address (backend
// gamma/link_preview.py), asked once per address: a bare link's chip label,
// its card's title and the author on its card's muted line. `sitePreview`
// is what is known now (null before the answer), `loadSitePreview` the
// answer ({} for none), `useSitePreview` both for a component.
const NO_PREVIEW = Object.freeze({});
const sitePreviews = new Map();   // href → {title, author} | Promise
const isWeb = (href) => /^https?:\/\//i.test(href || "");
export function sitePreview(href) {
  const p = sitePreviews.get(href);
  return p && !(p instanceof Promise) ? p : null;
}
export function loadSitePreview(href) {
  if (!isWeb(href)) return Promise.resolve(NO_PREVIEW);
  const known = sitePreviews.get(href);
  if (known) return known instanceof Promise ? known : Promise.resolve(known);
  const asked = fetch(`/api/link-preview?url=${encodeURIComponent(href)}`)
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => (d?.title ? { title: d.title, author: d.author || "" } : NO_PREVIEW))
    .catch(() => NO_PREVIEW)
    .then((p) => { sitePreviews.set(href, p); return p; });
  sitePreviews.set(href, asked);
  return asked;
}
export function useSitePreview(href, enabled = true) {
  const [preview, setPreview] = useState(() => sitePreview(href) || NO_PREVIEW);
  useEffect(() => {
    if (!enabled || !isWeb(href)) return undefined;
    let dead = false;
    loadSitePreview(href).then((p) => { if (!dead) setPreview(p); });
    return () => { dead = true; };
  }, [href, enabled]);
  return sitePreview(href) || preview;
}

const hostOf = (href) => { try { return new URL(href).hostname.replace(/^www\./, ""); } catch { return ""; } };

// GitHub addresses read without a fetch: owner/repo, #issue or PR, the file
// a blob or tree link points at.
function githubLabel(href) {
  try {
    const u = new URL(href);
    if (!/(^|\.)github\.com$/i.test(u.hostname)) return "";
    const p = u.pathname.split("/").filter(Boolean);
    if (p.length === 0) return t("GitHub");
    if (p.length === 1) return p[0];
    const repo = `${p[0]}/${p[1]}`;
    if (["issues", "pull", "discussions"].includes(p[2]) && p[3]) return `${repo} #${p[3]}`;
    if (["blob", "tree"].includes(p[2]) && p.length > 3) return `${repo} · ${p[p.length - 1]}`;
    if (p[2] === "releases") return `${repo} · releases`;
    if (p[2] === "commit" && p[3]) return `${repo} @ ${p[3].slice(0, 7)}`;
    return repo;
  } catch { return ""; }
}

// Whether a web link's words are only its address (a bare link, or link
// text that is itself an address): its chip then shows what it points to.
export const bareLinkText = (text) => !String(text || "").trim() || /^(https?:\/\/|www\.)/i.test(String(text).trim());
// What a web link's chip says, in the rendered view and the editor alike:
// its own words, else GitHub's readable name, the site's title (`title`,
// sitePreview's) or the host. `fetches`: whether the site's title would
// change it (it has no words, and is no GitHub link).
export function webLinkLabel(href, text, title) {
  if (!bareLinkText(text)) return { label: text, fetches: false };
  const gh = githubLabel(href);
  return { label: gh || title || hostOf(href) || text || href, fetches: !gh };
}
// The site's icon a web link's chip shows ("" for an address with no host).
export function siteIconUrl(href) {
  const host = hostOf(href);
  return host ? `https://icons.duckduckgo.com/ip3/${host}.ico` : "";
}
// An address as one muted line: host and path, no scheme, cut in the middle.
function shortUrl(href) {
  const s = String(href || "").replace(/^https?:\/\/(www\.)?/i, "").replace(/\/$/, "");
  return s.length > 56 ? `${s.slice(0, 34)}…${s.slice(-18)}` : s;
}
const firstLine = (text) => plainSnippet(String(text || "").split("\n")[0]);

// --- what a card shows ------------------------------------------------------------

// A link ({kind: "ref", id, words} or {kind: "url", href, label}) as its
// card shows it: {title, sub, quote, open: {label, external, run(e)} | null,
// copy: an absolute address}, and `target`, its Link field's token
// ({kind: "block", id, title, page} | {kind: "url", url, title}). `labels`
// (a block's [[ref]] labels), the context and the site's preview (`site`,
// useSitePreview's {title, author}) read here.
export function describeLink(link, { labels, ctx, site = NO_PREVIEW, onOpenRef }) {
  const nav = ctx?.nav;
  if (link.kind === "ref") {
    const ref = labels?.[link.id] || ctx?.refCache?.[link.id];
    const page = ctx?.pages?.find((p) => p.id === link.id);
    const open = { label: page || !ref?.page_title ? t("Open page") : t("Open note"),
      run: () => (onOpenRef ? onOpenRef(link.id) : nav?.openBlock?.(link.id)) };
    const copy = blockLinkUrl(link.id);
    if (ref?.trashed) {
      return { title: ref.trashed.title, sub: t("In Recently deleted"), open: null, copy,
        target: { kind: "block", id: link.id, title: ref.trashed.title, page: true } };
    }
    if (page) {
      const where = [pageKindLabel(page._doc), page._path || page._folderChips?.[0]?.name].filter(Boolean).join(" · ");
      return { title: page.content || t("Untitled"), sub: where, open, copy,
        target: { kind: "block", id: link.id, title: page.content || t("Untitled"), page: true, format: page._doc?.format, file: page._fileName } };
    }
    const line = firstLine(ref?.content);
    const isPage = !!ref && (!ref.page_title || ref.page_title === line);
    if (isPage) {
      return { title: line || link.words || link.id, sub: t("Page"), open, copy,
        target: { kind: "block", id: link.id, title: line || link.id, page: true } };
    }
    const quote = plainSnippet(ref?.content || "");
    return { title: ref?.page_title || link.words || link.id, sub: ref ? t("Note") : "", open, copy,
      quote: quote.length > 220 ? `${quote.slice(0, 219)}…` : quote,
      target: { kind: "block", id: link.id, title: line || link.id, page: false } };
  }
  const href = link.href || "";
  const abs = (() => { try { return new URL(href, window.location.origin).href; } catch { return href; } })();
  const gl = parseGammaLink(href, window.location.origin);
  if (gl && !gl.foreign) {
    if (isSourceLink(gl)) {
      const source = nav?.citeSource?.(gl.pageId) || null;
      const isLine = gl.kind === "line";
      return {
        title: source?.title || (isLine ? t("This file") : t("This paper")),
        sub: !isLine ? t("PDF page {page}", { page: gl.page })
          : gl.to > gl.line ? t("Lines {from}–{to}", { from: gl.line, to: gl.to }) : t("Line {line}", { line: gl.line }),
        quote: gl.quote || "",
        open: { label: isLine ? t("Open in the file") : t("Open in PDF"), run: () => nav?.openPage?.(gl.pageId, isLine
          ? { kind: "line", pageId: gl.pageId, line: gl.line, to: gl.to, quote: gl.quote }
          : { kind: "citation", pageId: gl.pageId, page: gl.page, quote: gl.quote }) },
        copy: abs,
        target: { kind: "url", url: href, title: source?.title || t("This paper") },
      };
    }
    const ref = labels?.[gammaLinkId(gl)] || ctx?.refCache?.[gammaLinkId(gl)];
    const title = ref?.page_title || firstLine(ref?.content) || link.label || t("This page");
    return {
      title, sub: gl.kind === "block" ? t("Note") : t("Page"),
      open: { label: gl.kind === "block" ? t("Open note") : t("Open page"),
        run: () => (gl.kind === "block" ? nav?.openBlock?.(gl.blockId) : nav?.openPage?.(gl.pageId, null)) },
      copy: abs, target: { kind: "url", url: href, title },
    };
  }
  const title = site.title || link.label || hostOf(href) || href;
  return {
    title, sub: [site.author, shortUrl(href)].filter(Boolean).join(" · "),
    open: { label: t("Open link"), external: true, run: () => window.open(abs, "_blank", "noopener,noreferrer") },
    copy: abs, target: { kind: "url", url: href, title: site.title || hostOf(href) || href },
  };
}

// --- the card -------------------------------------------------------------------------

// The floating card for `info` (describeLink's), at the rect `anchorRect()`
// gives: under it, above when there is no room (menuAnchor), following it as
// the page scrolls. `edit` ({words, placeholder, canRemove, onSave({words,
// to}), onRemove()}, null where the note may not be edited) adds the tools
// and the form; `editing` opens on the form ("text" or "link": the field
// that takes the focus). Hover handlers come in `hover`; `onPlaced(rect)`
// hears where it landed (the pointer's aim at it); `onEditing(field |
// null)` hears the form open from the card's ✎ and close back to the card;
// `onClose` ends it.
export function LinkCardSurface({ info, edit, editing: startEditing = null, pinned, anchorRect, hover, onPlaced, onEditing, onClose, id }) {
  const tipRef = useRef(null);
  const [editing, setEditingState] = useState(startEditing);
  const setEditing = (field) => { setEditingState(field); onEditing?.(field); };
  useEffect(() => { if (startEditing) setEditingState(startEditing); }, [startEditing]);
  const pos = useFloatingPosition(tipRef, (el) => placeFloating(surfaceSize(el), menuAnchor(anchorRect())),
    [editing], { follow: true });
  useLayoutEffect(() => { if (pos) onPlaced?.(tipRef.current?.getBoundingClientRect() || null); }, [pos]);
  const [copied, setCopied] = useState(false);
  const form = editing && edit;
  const open = (e) => {
    if (!info.open) return;
    if (e && (e.metaKey || e.ctrlKey || e.shiftKey)) return;   // the browser's own way with a link
    e?.preventDefault();
    onClose?.();
    info.open.run(e);
  };
  return createPortal(
    <div ref={tipRef} id={id} role={pinned || form ? "dialog" : "tooltip"} aria-label={form ? t("Edit link") : undefined}
      className={`citePreview linkCard${form ? " linkCardEditing" : ""}`}
      style={pos ? { left: pos.left, top: pos.top } : { left: 0, top: 0, visibility: "hidden" }}
      {...hover}
      // Portalled, its events still bubble through the note's row in the
      // React tree, whose press would start editing: they stop here. A
      // press on the card's view keeps the focus where it was.
      onMouseDown={(e) => { e.stopPropagation(); if (!form && !e.target.closest("input")) e.preventDefault(); }}
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => e.stopPropagation()}>
      {form ? (
        <LinkEditForm edit={edit} target={info.target} focus={editing}
          onCancel={() => (startEditing ? onClose?.() : setEditing(null))}
          onDone={(change) => { onClose?.(); edit.onSave(change); }}
          onRemove={edit.canRemove ? () => { onClose?.(); edit.onRemove(); } : null} />
      ) : (
        <>
          <div className="citePreviewTitle">{info.title}</div>
          {info.sub ? <div className="citePreviewPage">{info.sub}</div> : null}
          {info.quote ? <blockquote className="citePreviewQuote">“{info.quote}”</blockquote> : null}
          <div className="linkCardFoot">
            {info.open ? (
              <a href={info.copy || "#"} className="citePreviewOpen" tabIndex={-1} onClick={open}
                {...(info.open.external ? { target: "_blank", rel: "noreferrer" } : {})}>
                {info.open.label} {info.open.external ? <ExternalLinkIcon size={14} /> : <kbd>↵</kbd>}
              </a>
            ) : null}
            <span className="linkCardTools">
              {edit ? (
                <button type="button" className="uiBtn sm iconSq ghost" title={t("Edit link")} aria-label={t("Edit link")}
                  onClick={() => setEditing("text")}><PencilIcon size={14} /></button>
              ) : null}
              {info.copy ? (
                <button type="button" className="uiBtn sm iconSq ghost" title={t("Copy link")} aria-label={t("Copy link")}
                  onClick={() => { copyText(info.copy); setCopied(true); setTimeout(() => setCopied(false), 1200); }}>
                  {copied ? <CheckIcon size={14} /> : <CopyIcon size={14} />}
                </button>
              ) : null}
              {edit?.canRemove ? (
                <button type="button" className="uiBtn sm iconSq ghost" title={t("Remove link")} aria-label={t("Remove link")}
                  onClick={() => { onClose?.(); edit.onRemove(); }}><UnlinkIcon size={14} /></button>
              ) : null}
            </span>
          </div>
        </>
      )}
    </div>, document.body);
}

// The token a Link field shows for its target.
function TargetIcon({ to }) {
  if (to.kind === "url") return <LinkIcon size={14} />;
  return to.page ? <FileIcon path={to.file || ""} format={to.format || ""} size={14} /> : <span className="refPopupDot" />;
}

// The card's form. Text: the link's words (`edit.words`), its target's own
// as the placeholder. Link: the target's token, or, once typed into or
// cleared, a search: an address first ("Link to …"), then pages and notes
// (useRefRows). ↑↓ choose, Enter picks a row, then saves; Esc goes back.
function LinkEditForm({ edit, target, focus, onCancel, onDone, onRemove }) {
  const ctx = useContext(LinkCardContext);
  const [words, setWords] = useState(edit.words || "");
  const [to, setTo] = useState(edit.isNew ? null : target);
  const [picked, setPicked] = useState(false);
  const [query, setQuery] = useState(edit.isNew ? "" : null);
  const [sel, setSel] = useState(0);
  const textRef = useRef(null), linkRef = useRef(null);
  const { rows: refRows, partial } = useRefRows(query?.trim() ? query : null, {
    excludeId: edit.excludeId, pages: ctx?.pages, rootId: edit.excludeId, refCache: ctx?.refCache,
  });
  const url = query != null && isLinkInput(query) ? normalizeLinkInput(query) : "";
  const rows = [
    ...(url ? [{ kind: "url", id: url, title: t("Link to {url}", { url }), icon: <LinkIcon size={14} /> }] : []),
    ...refRows,
  ];
  const selected = Math.min(sel, rows.length - 1);
  // Once the card is placed and shown: an element still hidden takes no focus.
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const el = focus === "link" || edit.isNew ? linkRef.current : textRef.current;
      el?.focus({ preventScroll: true });
      if (el === textRef.current) el?.select();
    });
    return () => cancelAnimationFrame(frame);
  }, []);
  useEffect(() => { if (query != null) linkRef.current?.focus(); }, [query != null]);
  const pick = (r) => {
    if (r.kind === "url") setTo({ kind: "url", url: r.id, title: r.id });
    else {
      setTo({ kind: "block", id: r.id, title: r.title, page: r.kind === "page", format: r.format, file: r.file });
      ctx?.onCacheRef?.(r.id, r.kind === "page" ? { content: r.title, page_title: r.title } : r.block);
    }
    setPicked(true);
    setQuery(null);
    setSel(0);
    requestAnimationFrame(() => textRef.current?.focus());
  };
  const save = () => {
    if (!to) return;
    onDone({ words: words.trim(), to: picked || edit.isNew ? to : null });
  };
  const back = () => {
    if (query != null && to) { setQuery(null); return; }
    onCancel();
  };
  const keys = (e) => {
    if (e.key === "Escape") { e.preventDefault(); back(); return; }
    if (query != null && rows.length && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      setSel((i) => Math.max(0, Math.min(rows.length - 1, i + (e.key === "ArrowDown" ? 1 : -1))));
      return;
    }
    if (e.key === "Enter" && !e.isComposing) {
      e.preventDefault();
      if (query != null && query.trim() && rows.length) pick(rows[selected]);
      else save();
    }
  };
  const placeholder = to && !picked ? (edit.placeholder ?? target?.title ?? "") : to?.title || "";
  return (
    <form className="linkCardForm" onSubmit={(e) => { e.preventDefault(); save(); }} onKeyDown={keys}>
      <label className="linkCardField">
        <span>{t("Text")}</span>
        <input ref={textRef} className="linkCardInput" value={words} placeholder={placeholder}
          onChange={(e) => setWords(e.target.value)} spellCheck={false} />
      </label>
      <label className="linkCardField">
        <span>{t("Link")}</span>
        {query == null && to ? (
          <span className="linkCardTarget" tabIndex={0} role="button" title={t("Change the link")}
            onClick={() => setQuery("")}
            onKeyDown={(e) => {
              if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); setQuery(e.key); }
              else if (e.key === "Backspace" || e.key === "Delete") { e.preventDefault(); setQuery(""); }
            }}>
            <span className="linkCardTargetIcon"><TargetIcon to={to} /></span>
            <span className="linkCardTargetText">{to.title || to.url}</span>
            <span className="linkCardTargetClear" aria-hidden="true"><XIcon size={14} /></span>
          </span>
        ) : (
          <input ref={linkRef} className="linkCardInput" value={query || ""} placeholder={t("Search pages or paste a link")}
            onChange={(e) => { setQuery(e.target.value); setSel(0); }} spellCheck={false} />
        )}
      </label>
      {query != null && query.trim() && rows.length ? (
        <div className="linkCardRows">
          <RefRows rows={rows} selected={selected} query={url ? "" : query} onPick={pick} heads={{ url: t("Link") }} />
          {partial ? <div className="linkCardHint">{t("Block search stopped early — type more to narrow it.")}</div> : null}
        </div>
      ) : null}
      <div className="linkCardActions">
        {onRemove ? (
          <button type="button" className="uiBtn sm ghost" onClick={onRemove}><UnlinkIcon size={14} />{t("Remove link")}</button>
        ) : null}
        <span className="linkCardSpacer" />
        <button type="button" className="uiBtn sm" onClick={onCancel}>{t("Cancel")}</button>
        <button type="submit" className="uiBtn sm primary" disabled={!to}>{t("Save")}</button>
      </div>
    </form>
  );
}

// --- a rendered link's card ----------------------------------------------------------

// Wraps a rendered link (`children`): a hover after a moment, or the focus,
// previews its card; a right-click or a held finger pins it (menuPress);
// the pointer on its way from the link to the card keeps it (menuAim). A
// pinned card closes on a press elsewhere, Escape or a pick.
// `describe(site, ctx)` is describeLink's answer, asked when the card
// shows; `edit` as LinkCardSurface takes it, `siteHref` an address whose
// preview (`site`) the card fetches.
export function LinkHover({ describe, edit, siteHref, children }) {
  const ctx = useContext(LinkCardContext);
  const ref = useRef(null);
  const timer = useRef(null);
  const [card, setCard] = useState(null);   // {pinned, editing}
  const aim = useMenuAim({ active: !!card });
  const tipId = useRef(`linkcard-${Math.random().toString(36).slice(2)}`).current;
  const site = useSitePreview(siteHref, !!card);
  const later = (fn, ms) => { clearTimeout(timer.current); timer.current = setTimeout(fn, ms); };
  const close = () => { clearTimeout(timer.current); aim.keep(); setCard(null); };
  const preview = () => setCard((c) => c || { pinned: false });
  // The link's own menu: the note's row around it opens none.
  const pin = (e) => {
    e?.preventDefault?.();
    e?.stopPropagation?.();
    clearTimeout(timer.current);
    setCard((c) => ({ pinned: true, editing: c?.editing || null }));
  };
  useEffect(() => () => clearTimeout(timer.current), []);
  // A pinned card closes on a press anywhere else, or Escape.
  useEffect(() => {
    if (!card?.pinned) return undefined;
    const away = (e) => {
      if (ref.current?.contains(e.target) || document.getElementById(tipId)?.contains(e.target)) return;
      close();
    };
    // Escape in the card's form is the form's (back from a search, then out of the form).
    const esc = (e) => {
      if (e.key !== "Escape" || document.getElementById(tipId)?.contains(e.target)) return;
      e.stopPropagation();
      close();
    };
    document.addEventListener("pointerdown", away, true);
    window.addEventListener("keydown", esc, true);
    return () => { document.removeEventListener("pointerdown", away, true); window.removeEventListener("keydown", esc, true); };
  }, [card?.pinned]);
  // An unpinned preview is fixed to where it opened: any scroll leaves it behind.
  useEffect(() => {
    if (!card || card.pinned) return undefined;
    window.addEventListener("scroll", close, true);
    return () => window.removeEventListener("scroll", close, true);
  }, [card]);
  useEffect(() => { if (!card) aim.setTarget(null); }, [card]);
  const info = card ? describe(site, ctx) : null;
  return (
    <>
      <span ref={ref} className="linkAnchor" aria-describedby={card ? tipId : undefined}
        onMouseEnter={() => { aim.keep(); if (card) clearTimeout(timer.current); else later(preview, 250); }}
        onMouseLeave={() => { if (!card?.pinned) aim.guard(close); else clearTimeout(timer.current); }}
        onFocus={() => { if (!card) preview(); }}
        onBlur={(e) => { if (!card?.pinned && !document.getElementById(tipId)?.contains(e.relatedTarget)) close(); }}
        {...menuPress(pin)}>
        {children}
      </span>
      {info ? (
        <LinkCardSurface id={tipId} info={info} edit={edit} pinned={card.pinned} editing={card.editing}
          anchorRect={() => ref.current?.getBoundingClientRect() || { left: 0, top: 0, right: 0, bottom: 0 }}
          hover={card.pinned ? {} : {
            onPointerEnter: () => { aim.keep(); clearTimeout(timer.current); },
            onMouseLeave: () => later(close, 200),
          }}
          onPlaced={(rect) => aim.setTarget(rect)}
          // A form open holds the card wherever the pointer goes.
          onEditing={(field) => { if (field) setCard((c) => (c ? { ...c, pinned: true } : c)); }}
          onClose={close} />
      ) : null}
    </>
  );
}
