// A block's Properties, in a popover under what opened it (the row's ⋮⋮
// handle, the highlight on the PDF, a menu's point): what the block is and
// every key it stores, read-only. Details lays the keys out in sections of
// rows (editor/blockProps.js works every row out; this file only draws
// them); JSON shows the raw object, highlighted. Each row copies its stored
// value in place, and its menu (right-click or a held finger) copies it as
// JSON, its key path, its SQL path or the id it names; a row that names
// another block or page steps into that one's properties, and Back returns.
// One instance, rendered by App through a portal and loaded on first use
// (shared/ui/lazy.jsx), placed like ThreadPopover from its measured size
// before it is painted, following its anchor as the page scrolls (out of
// sight while the anchor is scrolled out of its pane). A step into another
// block, and Back, change it in place. The rows come from this tab's block
// at once (`live`); the block dict's own fields come from GET
// /api/blocks/{id}, read on opening, again a second after this tab's block
// last changed, and every DIFFER_MS while the two differ.
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { useLeave } from "../shared/ui/leave.js";
import { VIEWPORT_PAD, growFrom, placeFloating, scrollBox, surfaceSize, useFloatingPosition } from "../shared/ui/placement.js";
import {
  ArrowLeftIcon, BracesIcon, CheckIcon, ChevronRightIcon, CodeIcon, CopyIcon, DatabaseIcon, ExternalLinkIcon, FileTextIcon,
  FolderIcon, HighlightIcon, ImageIcon, LabelIcon, LinkIcon, ListIcon, LocateIcon, MicIcon, PaperclipIcon, PenIcon, PinIcon,
  SheetIcon, TypeIcon, XIcon,
} from "../shared/ui/Icons";
import { ContextMenu, MenuButton, MenuItem, MenuLabel } from "../shared/ui/Menus";
import { CopyBox, useCopied } from "../shared/ui/Widgets";
import { Chevron, FADE_IN, IconSwap, MorphText, Reveal } from "../shared/ui/MotionWidgets";
import { Segmented } from "../settings/SettingsKit";
import { menuPress } from "../shared/ui/press.js";
import { API, apiJson, assetUrl, blockLinkUrl, copyText, usePersistedState } from "../shared/lib/utils";
import { pdfViewerUrl } from "../documents/pageDocument.js";
import { T, t } from "../shared/i18n/i18n.js";
import { inkFor, subscribe as inkSubscribe, currentVersion as inkVersion } from "../ink/inkStore.js";
import { CLIENT_ID } from "../collaboration/usePageCollab.js";
import { zoteroReadonlyWords } from "../collaboration/ZoteroPage.jsx";
import { highlightCode } from "./codeHighlight.js";
import { DIFFER_MS, changedKeys, effectiveKind, headInfo, jsonSources, jsonText, propertySections, refsToFetch, statusChips,
  timeDisplay } from "./blockProps.js";
import "./blockProperties.css";

// The head's tile per kind (blockProps.js KINDS).
const KIND_ICONS = {
  highlight: HighlightIcon,
  link: LinkIcon,
  ink: PenIcon,
  text_box: TypeIcon,
  picture: ImageIcon,
  sheet: SheetIcon,
  recording: MicIcon,
  page: FileTextIcon,
  folder: FolderIcon,
  label: LabelIcon,
  note: ListIcon,
};

// A row's way somewhere, by its nav action: to the place on the PDF, or into
// the page, block or folder it names.
const NAV_ICONS = {
  showOnPage: LocateIcon,
  jumpHighlight: LocateIcon,
  openPage: ChevronRightIcon,
  openBlock: ChevronRightIcon,
  openFolder: ChevronRightIcon,
  openLabel: ChevronRightIcon,
};

const VIEWS = [["details", T("Details"), ListIcon], ["json", T("JSON"), BracesIcon]];
const parseView = (v) => (v === "details" || v === "json" ? v : undefined);

// The body keeps at least this height once placed, so a short view does not
// shrink the surface it switches from.
const MIN_BODY = 240;
// The least room under its anchor the surface is fitted into; with less it
// is moved up instead.
const MIN_ROOM = 320;
// How long after this tab's block last changed the server is read again.
const REFETCH_MS = 1000;
// How many times a block of this tab's the server does not have yet (its
// insert still on the way) is read again, REFETCH_MS apart.
const INSERT_TRIES = 10;

// A text this long, or of more lines, is clamped to three with "Show all".
const isLong = (text) => text.length > 160 || text.split("\n").length > 3;

// What the popover hangs under, {rect, el}: its anchor while that is on
// screen and not under a dialog, else the menu's point it opened at (no
// el), else the block's row handle (its row, on a read-only page), else its
// highlight on the PDF; null to centre it. An element in a note row stands
// for the whole row (its handle sits at the row's top), so the row stays in
// sight.
function anchorOf(target) {
  const usable = (el) => !!el?.isConnected && !el.closest("[inert]");
  const box = (el) => {
    const r = el.getBoundingClientRect();
    if (!r.width && !r.height) return null;
    const row = el.closest(".sortableBlockWrap, .blockRowWrap")?.getBoundingClientRect();
    return row ? { left: r.left, right: r.right, top: Math.min(r.top, row.top), bottom: Math.max(r.bottom, row.bottom) } : r;
  };
  if (usable(target.anchor)) {
    const r = box(target.anchor);
    if (r) return { rect: r, el: target.anchor };
  }
  if (target.at) return { rect: { left: target.at.x, right: target.at.x, top: target.at.y, bottom: target.at.y }, el: null };
  const id = CSS.escape(target.blockId);
  for (const selector of [`.sortableBlockWrap[data-block-id="${id}"] .dragHandle`, `.blockRowWrap[data-block-id="${id}"]`, `[data-hl-id="${id}"]`]) {
    const el = document.querySelector(selector);
    const r = usable(el) ? box(el) : null;
    if (r) return { rect: r, el };
  }
  return null;
}

// A highlight's place, drawn small: the PDF page and its line boxes in the
// block's colour, dashed while it waits to be checked, hollow when lost.
function MiniMap({ place }) {
  const { width, height } = place.frame;
  return (
    <svg className="propsMiniMap" viewBox={`0 0 ${width} ${height}`} width={44} height={Math.round((44 * height) / width)}
      aria-hidden="true" data-rects={place.rects.length}>
      <rect className="propsMiniPage" x="0" y="0" width={width} height={height} vectorEffect="non-scaling-stroke" />
      {place.rects.map((r, i) => (
        <rect key={i} className="propsMiniRect" x={r.x1} y={r.y1} width={Math.max(r.x2 - r.x1, 1)} height={Math.max(r.y2 - r.y1, 1)}
          fill={place.hollow ? "none" : place.color || undefined} stroke={place.dashed || place.hollow ? place.color || undefined : "none"}
          strokeDasharray={place.dashed ? "3 2" : undefined} vectorEffect="non-scaling-stroke" />
      ))}
    </svg>
  );
}

const Tags = ({ tags }) => (tags || []).map((tag, i) => (
  <span key={i} className={`uiTag ${tag.tone || ""}`.trim()}>{tag.text}</span>
));

// target: {blockId, anchor, at, back} (App's propsPop). `live`: the block as
// this tab has it (the open page or a row of it), null for a library page
// (the listing holds a summary of its keys), a folder, a label or a block
// not in this tab. `isOpenPage`: the block is the
// page on screen. ctx: what the rows resolve ids with ({pages, libTree,
// lookupBlock, refCache, onFetchRefs, pageFileHash, shareMode, pageId,
// pageTitle, tree}). nav: App's ways somewhere ({openBlockLink (a page or a
// block, with Alt+← back),
// showOnPage, jumpToHighlightId, openFolder, openLabel, openMetaPopover,
// openUrl, stepInto(id), back()}). onClose(returnFocus).
export default function BlockProperties({ target, live, isOpenPage = false, ctx, nav, onClose }) {
  const ref = useRef(null);
  const bodyRef = useRef(null);
  const id = target.blockId;

  // --- state, kept per block ---------------------------------------------------
  const [server, setServer] = useState(null);
  const [err, setErr] = useState(null);
  const [readAt, setReadAt] = useState(null); // when the last read that answered was asked for
  // Which disclosures, JSON values and long texts are open, by id.
  const [open, setOpen] = useState({});
  const [rowMenu, setRowMenu] = useState(null); // {x, y, row}
  const seq = useRef(0); // the last read asked for: an older answer is dropped
  const keySeen = useRef(false);
  const lastLive = useRef(live);
  const differ = useRef({ since: null, stamp: null });
  const asked = useRef(new Set());
  const insertTries = useRef(0);
  // A step into another block, or Back, keeps the surface (its place, its
  // height, the view) and starts this block's state over.
  const [forId, setForId] = useState(id);
  if (forId !== id) {
    setForId(id);
    setServer(null);
    setErr(null);
    setReadAt(null);
    setOpen({});
    setRowMenu(null);
    keySeen.current = false;
    lastLive.current = live;
    differ.current = { since: null, stamp: null };
    asked.current = new Set();
    insertTries.current = 0;
  }

  // --- the server's dict -------------------------------------------------------
  const load = useCallback(() => {
    const n = ++seq.current;
    const at = Date.now();
    apiJson(`${API}/blocks/${encodeURIComponent(id)}`).then(
      (dict) => { if (n === seq.current) { setServer(dict); setErr(null); setReadAt(at); } },
      (error) => { if (n === seq.current) setErr(error); },
    );
  }, [id]);
  useEffect(() => {
    load();
    return () => { seq.current += 1; };
  }, [load]);
  // Read again a second after this tab's block last changed (an edit here,
  // a peer's op), so the dict's fields and the sync chips follow it.
  const liveKey = live ? JSON.stringify([live.properties ?? null, live.content ?? "", live.position ?? null]) : "";
  useEffect(() => {
    if (!keySeen.current) { keySeen.current = true; return undefined; }
    const timer = setTimeout(load, REFETCH_MS);
    return () => clearTimeout(timer);
  }, [liveKey, load]);

  // A block deleted while the popover is open keeps its last rows.
  if (live) lastLive.current = live;
  const gone = !live && !!lastLive.current;
  const mine = live || lastLive.current; // the block as this tab has (or had) it
  const block = mine || server;

  // A block of this tab's the server does not have yet (its insert on the
  // way) is read again until it does.
  const awaitingInsert = err?.status === 404 && !server && !!live && !err.data?.trashed;
  useEffect(() => {
    if (!awaitingInsert || insertTries.current >= INSERT_TRIES) return undefined;
    const timer = setTimeout(() => { insertTries.current += 1; load(); }, REFETCH_MS);
    return () => clearTimeout(timer);
  }, [awaitingInsert, err, load]);

  // Since when this tab's block differs from the server's, started over
  // whenever the server's copy moves (its saves are landing): it is read
  // again every DIFFER_MS while they differ, and the chip says it differs
  // only when a read asked that long after still finds it (statusChips).
  const differing = !!(mine && server && !gone && changedKeys(mine, server).length);
  const stamp = server ? String(server.updated_at ?? "") : null;
  if (!differing) differ.current = { since: null, stamp: null };
  else if (differ.current.since === null || differ.current.stamp !== stamp) differ.current = { since: Date.now(), stamp };
  const differSince = differ.current.since;
  useEffect(() => {
    if (differSince === null) return undefined;
    const timer = setTimeout(load, DIFFER_MS);
    return () => clearTimeout(timer);
  }, [differSince, readAt, load]);
  // A handwriting group's strokes as this tab holds them load on their own.
  const inkV = useSyncExternalStore(inkSubscribe, inkVersion);

  // --- what is shown -------------------------------------------------------------
  const kind = isOpenPage && !server ? "page" : effectiveKind(mine, server);
  const minute = Math.floor(Date.now() / 60000); // relative times move on by the minute
  const sections = useMemo(() => {
    if (!block) return [];
    const rowCtx = { ...ctx, now: Date.now(), isOpenPage, tabId: CLIENT_ID, inkStrokes: (b) => inkFor(b)?.strokes?.length ?? null };
    return propertySections(block, { kind, server, ctx: rowCtx });
  }, [block, kind, server, ctx.tree, ctx.refCache, ctx.pages, ctx.libTree, ctx.pageFileHash, ctx.pageId, ctx.pageTitle, // eslint-disable-line react-hooks/exhaustive-deps
    ctx.shareMode, isOpenPage, inkV, minute]);
  const head = block ? headInfo(block, kind) : null;
  // the open page's PDF only measures the open page's blocks
  const chips = statusChips(mine, server, err, { pageFileHash: live ? ctx.pageFileHash : undefined, differSince, readAt, gone });
  const missing = err?.status === 404 && !block;
  const trashed = missing ? err.data?.trashed || null : null;

  // The ids the rows name that neither the page nor the cache knows: asked
  // for once each.
  const unresolved = refsToFetch(sections).filter((x) => !asked.current.has(x)).join(",");
  useEffect(() => {
    if (!unresolved || !ctx.onFetchRefs) return;
    const ids = unresolved.split(",");
    ids.forEach((x) => asked.current.add(x));
    ctx.onFetchRefs(ids);
  }, [unresolved]); // eslint-disable-line react-hooks/exhaustive-deps

  const [view, setView] = usePersistedState("gamma-props-view", "details", { parse: parseView });
  const [source, setSource] = useState("properties");
  const sources = useMemo(() => jsonSources(mine, server), [mine, server]);
  const shownSource = source === "server" && !sources.server ? "block" : source;
  const json = jsonText(shownSource === "server" ? sources.server : shownSource === "block" ? sources.block : sources.properties);
  const jsonHtml = useMemo(() => (view === "json" ? highlightCode(json, "json") : ""), [json, view]);

  const toggle = (key, was) => setOpen((o) => ({ ...o, [key]: !was }));

  const [copied, flash] = useCopied();
  const copy = (key, text) => { copyText(text).then((ok) => { if (ok !== false) flash(key); }); };

  // --- placement, focus, dismissal -------------------------------------------------
  // Under its anchor by their left edges, growing from that corner, so the
  // row it describes stays in sight; with nothing to hang under, centred
  // near the top. An anchor element keeps it below the top of the pane the
  // element scrolls in, and out of sight (data-away) while the element is
  // scrolled out of that pane. Its height stops at the window's foot when
  // it is first placed with room enough under the anchor
  // (--props-room), so a view that grows later scrolls inside it rather
  // than push it up over the row; with less room it takes its full height
  // and is kept inside the window.
  const roomSet = useRef(false);
  const pos = useFloatingPosition(ref, (el) => {
    const found = anchorOf(target);
    const r = found?.rect || null;
    const pane = found?.el ? scrollBox(found.el)?.getBoundingClientRect() : null;
    const away = !!pane && (r.bottom < pane.top || r.top > pane.bottom);
    el.toggleAttribute("data-away", away);
    if (away) return { left: 0, top: 0, origin: "0 0" };
    // not over the pane's header or the tab strip; below, the window's foot
    const bounds = pane ? { top: Math.max(0, pane.top), bottom: window.innerHeight } : undefined;
    if (!roomSet.current) {
      roomSet.current = true;
      const room = r ? window.innerHeight - VIEWPORT_PAD - (r.bottom + 4) : 0;
      if (room >= MIN_ROOM) el.style.setProperty("--props-room", `${Math.floor(room)}px`);
    }
    const size = surfaceSize(el);
    if (!r) return { ...placeFloating(size, { x: (window.innerWidth - size.width) / 2, y: 80 }), origin: "50% 0" };
    const at = placeFloating(size, { x: r.left, y: r.bottom + 4, flipY: r.top - 4, bounds });
    return { ...at, origin: growFrom(r.left, r.bottom + 4, at) };
  }, [target.blockId, target.anchor, target.at], { follow: true });
  const leaveRef = useLeave("surface", ref);

  // The focus comes in once it is placed (a hidden element takes none), and
  // back to the surface when what held it inside goes (a chip or a button
  // a read took away, the row a step left), so Escape still closes it.
  const focused = useRef(false);
  useEffect(() => {
    if (!pos || focused.current) return;
    focused.current = true;
    ref.current?.focus({ preventScroll: true });
  }, [pos]);
  useEffect(() => {
    if (focused.current && document.activeElement === document.body) ref.current?.focus({ preventScroll: true });
  });

  // The body's height only grows: switching views, folding a section or
  // stepping into another block never shrinks the surface. At least
  // MIN_BODY, within the room the surface's max-height leaves it; a resized
  // window measures again.
  const [bodyH, setBodyH] = useState(0);
  useLayoutEffect(() => {
    const body = bodyRef.current, pop = ref.current;
    if (!pos || !body || !pop) return;
    const h = body.offsetHeight;
    const room = (parseFloat(getComputedStyle(pop).maxHeight) || Infinity) - (pop.offsetHeight - h);
    const next = Math.min(Math.max(h, MIN_BODY), Math.max(room, h));
    if (next > bodyH + 0.5) setBodyH(next);
  }, [pos, view, open, sections, json, chips.length, bodyH]);
  useEffect(() => {
    const reset = () => setBodyH(0);
    window.addEventListener("resize", reset);
    return () => window.removeEventListener("resize", reset);
  }, []);

  // A press anywhere else closes it; not one in its own row menu.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const down = (e) => {
      const el = e.target;
      if (ref.current?.contains(el) || el.closest?.(".ctxMenu")) return;
      closeRef.current(false);
    };
    document.addEventListener("pointerdown", down, true);
    return () => document.removeEventListener("pointerdown", down, true);
  }, []);

  // --- a row ---------------------------------------------------------------------------
  const go = (n) => {
    switch (n.action) {
      case "showOnPage": nav.showOnPage(n.id); break;
      case "openPage": nav.openBlockLink(n.id); break;
      case "openBlock": nav.openBlockLink(n.id, n.pageId || undefined); break;
      case "jumpHighlight": nav.jumpToHighlightId(n.id); break;
      case "openFolder": onClose(false); nav.openFolder(n.id); break;
      case "openLabel": onClose(false); nav.openLabel(n.id); break;
      case "openUrl": onClose(false); nav.openUrl(n.url); break;
      default: break;
    }
  };
  // A row button's name says which row it acts on; its title stays short.
  const named = (label, row) => `${label}: ${row.label}`;
  function navButton(row) {
    const n = row.nav;
    if (!n) return null;
    if (n.action === "copyLink") {
      const key = `${row.id}:link`;
      return (
        <button type="button" className={`ctlBtn metaRowBtn ${copied === key ? "on" : ""}`} title={n.label} aria-label={named(n.label, row)}
          onClick={() => copy(key, blockLinkUrl(n.id))}>
          <IconSwap swap={copied === key} from={<LinkIcon size={14} />} to={<CheckIcon size={14} />} />
        </button>
      );
    }
    if (n.action === "openPdf") {
      return (
        <a className="ctlBtn metaRowBtn" href={assetUrl(pdfViewerUrl(n.url))} target="_blank" rel="noreferrer" title={n.label} aria-label={named(n.label, row)}>
          <ExternalLinkIcon size={14} />
        </a>
      );
    }
    // a web address opens from its value; the page on screen is where it is
    if (n.action === "openUrl" || (n.action === "openPage" && n.id === ctx.pageId)) return null;
    const Icon = NAV_ICONS[n.action];
    if (!Icon) return null;
    return (
      <button type="button" className="ctlBtn metaRowBtn" title={n.label} aria-label={named(n.label, row)} onClick={() => go(n)}>
        <Icon size={14} />
      </button>
    );
  }

  function value(row) {
    const d = row.display || {};
    const cls = [d.muted ? "propsMuted" : "", d.warn ? "metaValWarn" : ""].filter(Boolean).join(" ") || undefined;
    switch (row.type) {
      case "text":
      case "quote": {
        const text = String(d.text ?? "");
        const long = isLong(text);
        const more = !!open[`text:${row.id}`];
        return (
          <>
            {d.icon === "paperclip" ? <PaperclipIcon size={12} className="propsInlineIcon" /> : null}
            <span className={[cls, "propsText", row.type === "quote" ? "propsQuote" : "", long && !more ? "propsClamp" : ""].filter(Boolean).join(" ")}
              title={d.title}>
              {row.type === "quote" ? `“${text}”` : text}
            </span>
            {d.detail ? <span className="propsMuted propsDetail">{d.detail}</span> : null}
            {long ? (
              <button type="button" className="uiBtn sm ghost propsMore" onClick={() => toggle(`text:${row.id}`, more)}>
                {more ? t("Show less") : t("Show all")}
              </button>
            ) : null}
          </>
        );
      }
      case "mono":
        return (
          <>
            {d.icon === "pin" ? <PinIcon size={12} className="propsInlineIcon" /> : null}
            {d.short !== undefined ? <code className="metaCiteKey" title={d.text}>{d.short}</code> : <span className={cls}>{d.text}</span>}
          </>
        );
      case "flag":
        return <span className={`uiTag ${d.tone || ""}`.trim()}>{d.text}</span>;
      case "number":
        return <span className={[cls, "propsNum"].filter(Boolean).join(" ")}>{d.text}</span>;
      case "time":
        return (
          <span className={[cls, "propsNum"].filter(Boolean).join(" ")} title={d.title || d.iso}>
            {row.field && row.key === "updated_at" ? <MorphText>{d.text}</MorphText> : d.text}
          </span>
        );
      case "color":
        return (
          <>
            {d.css ? <span className="propsSwatch" style={{ "--swatch": d.css }} aria-hidden="true" /> : null}
            <span className={cls}>{d.text}</span>
          </>
        );
      case "page":
        return (
          <span className="propsRef">
            <FileTextIcon size={14} className="propsInlineIcon" />
            <span className={d.mono ? "metaCiteKey" : cls}>{d.text}</span>
          </span>
        );
      case "block":
        return (
          <span className="propsRef">
            <span className={d.state === "ok" ? undefined : "propsMuted"}>{d.text}</span>
            {d.page ? <span className="propsMuted propsDetail">{t("on {page}", { page: d.page })}</span> : null}
          </span>
        );
      case "folders":
      case "labels": {
        const Icon = row.type === "folders" ? FolderIcon : LabelIcon;
        const opener = row.type === "folders" ? "openFolder" : "openLabel";
        return (
          <span className="propsChips">
            {(d.chips || []).map((c) => (
              <button key={c.id} type="button" className="categoryTag propsChip" title={c.name}
                onClick={() => go({ action: opener, id: c.id })}>
                <Icon size={10} />{c.name}
              </button>
            ))}
            {(d.missing || []).map((m) => <span key={m.id} className={m.warn ? "uiTag warn" : "uiTag"} title={m.id}>{m.text}</span>)}
          </span>
        );
      }
      case "url":
        return d.host ? (
          <button type="button" className="propsUrl" title={d.url} onClick={() => go({ action: "openUrl", url: d.url })}>
            <b>{d.host}</b><span>{d.path}</span>
          </button>
        ) : <span className={cls}>{d.text}</span>;
      case "file":
        return d.url ? (
          <span className="propsFile">
            {d.thumb ? <img className="propsThumb" src={assetUrl(d.url)} alt="" {...FADE_IN} /> : null}
            <span className="propsFileName">{d.name}</span>
            <a className="metaLink" href={assetUrl(d.url)} target="_blank" rel="noreferrer" title={t("Open file")} aria-label={t("Open file")}>
              <ExternalLinkIcon size={12} />
            </a>
          </span>
        ) : <span className={cls || "propsMuted"}>{d.text}</span>;
      case "place":
        return (
          <span className="propsPlace">
            {d.frame ? <MiniMap place={d} /> : null}
            <span className="propsPlaceText">
              <span>{d.text}</span>
              {d.box ? <span className="propsMuted propsNum">{d.box}</span> : null}
              {d.prev ? <span className="propsMuted">{d.prev}</span> : null}
              {d.closest ? <span className="propsMuted">{d.closest}</span> : null}
              {d.chips?.length ? <span className="propsChips"><Tags tags={d.chips} /></span> : null}
            </span>
          </span>
        );
      case "json": {
        const unfolded = !!open[`json:${row.id}`];
        return (
          <>
            <button type="button" className="propsJsonToggle" aria-expanded={unfolded} onClick={() => toggle(`json:${row.id}`, unfolded)}>
              <Chevron open={unfolded} dir="right" size={12} />
              <code className="metaCiteKey">{d.preview}</code>
            </button>
            {unfolded ? <pre className="propsJson" dangerouslySetInnerHTML={{ __html: highlightCode(d.text || "", "json") }} /> : null}
          </>
        );
      }
      case "code":
        return (
          <CopyBox copied={copied === row.id} onCopy={() => copy(row.id, row.copy)} title={t("Copy value")} label={named(t("Copy value"), row)}>
            <pre className="bibtexPre">{d.text}</pre>
          </CopyBox>
        );
      default:
        return <span className={cls}>{d.text}</span>;
    }
  }

  // The row's menu, from a right-click, a held finger or its "⋯".
  const rowMenuOpener = (row) => (e) => { e.preventDefault(); setRowMenu({ x: e.clientX, y: e.clientY, row }); };
  function renderRow(row) {
    const hint = row.hint ? `${row.keyPath} — ${row.hint}` : row.keyPath;
    const openMenu = rowMenuOpener(row);
    return (
      <div key={row.id} className="metaRow propsRow rowMenuHost" data-key={row.key} data-row={row.id} {...menuPress(openMenu)}>
        <span className="metaKey" title={hint}>{row.label}</span>
        <div className="metaVal">
          {value(row)}
          <Tags tags={row.tags} />
        </div>
        <span className="propsRowBtns">
          {navButton(row)}
          {row.step && row.step !== id ? (
            <button type="button" className="ctlBtn metaRowBtn propsStep" title={t("Show its properties")} aria-label={named(t("Show its properties"), row)}
              onClick={() => nav.stepInto(row.step)}>
              <BracesIcon size={14} />
            </button>
          ) : null}
          {row.type !== "code" ? (
            <button type="button" className={`ctlBtn metaRowBtn propsCopy ${copied === row.id ? "on" : ""}`} title={t("Copy value")}
              aria-label={named(t("Copy value"), row)} onClick={() => copy(row.id, row.copy)}>
              <IconSwap swap={copied === row.id} from={<CopyIcon size={14} />} to={<CheckIcon size={14} />} />
            </button>
          ) : null}
          <MenuButton className="metaRowBtn" open={openMenu} label={named(t("Actions"), row)} title={t("Actions")} />
        </span>
      </div>
    );
  }

  function renderSection(s) {
    const shown = !s.disclose || (open[`section:${s.id}`] ?? !s.collapsed);
    return (
      <section key={s.id} className="propsSection" data-section={s.id}>
        {s.disclose ? (
          <button type="button" className="popoverSection propsDisclose" aria-expanded={shown}
            onClick={() => toggle(`section:${s.id}`, shown)}>
            <Chevron open={shown} dir="right" size={12} />
            <span>{s.title}</span>
            <span className="propsCount">{s.rows.length}</span>
          </button>
        ) : (
          <div className="popoverSection propsSectionHead">
            <span>{s.title}</span>
            {s.action?.action === "editMeta" ? (
              <button type="button" className="uiBtn sm ghost" onClick={() => { onClose(false); nav.openMetaPopover(); }}>{s.action.label}</button>
            ) : null}
          </div>
        )}
        {shown ? <div className={`metaTable propsTable${s.dim ? " dim" : ""}`}>{s.rows.map(renderRow)}</div> : null}
      </section>
    );
  }

  // --- the row menu ---------------------------------------------------------------------
  function rowMenuItems(row) {
    const done = (key, text) => { setRowMenu(null); copy(key, text); };
    const refId = (row.type === "page" || row.type === "block") && typeof row.value === "string" ? row.value : "";
    return (
      <>
        <MenuLabel><code className="propsMenuKey">{row.keyPath}</code></MenuLabel>
        <MenuItem icon={CopyIcon} onClick={() => done(row.id, row.copy)}>{t("Copy value")}</MenuItem>
        <MenuItem icon={BracesIcon} onClick={() => done(row.id, row.copyJson)}>{t("Copy as JSON")}</MenuItem>
        <MenuItem icon={CodeIcon} onClick={() => done(row.id, row.keyPath)}>{t("Copy key path")}</MenuItem>
        {row.sqlPath && !ctx.shareMode ? (
          <MenuItem icon={DatabaseIcon} onClick={() => done(row.id, row.sqlPath)}>{t("Copy SQL path")}</MenuItem>
        ) : null}
        {refId ? <MenuItem icon={CopyIcon} onClick={() => done(row.id, refId)}>{t("Copy id")}</MenuItem> : null}
        {row.nav?.action === "copyLink" ? (
          <MenuItem icon={LinkIcon} onClick={() => done(`${row.id}:link`, blockLinkUrl(row.nav.id))}>{t("Copy link to block")}</MenuItem>
        ) : null}
        {row.step && row.step !== id ? (
          <MenuItem icon={BracesIcon} onClick={() => { setRowMenu(null); nav.stepInto(row.step); }}>{t("Show its properties")}</MenuItem>
        ) : null}
      </>
    );
  }

  // --- the surface ----------------------------------------------------------------------
  const KindIcon = KIND_ICONS[kind] || ListIcon;
  const loading = <div className="popoverHint loadingHint">{t("Loading…")}</div>;
  const deletedAt = trashed ? timeDisplay(trashed.deleted_at) : null;
  const details = !missing ? sections.map(renderSection)
    : trashed ? (
      <div className="popoverHint propsGone">
        {t("It is in Recently deleted, with the page “{title}”.", { title: trashed.title || t("Untitled") })}
        {deletedAt ? <span className="propsMuted propsDetail" title={deletedAt.iso}>{deletedAt.text}</span> : null}
      </div>
    ) : <div className="popoverHint propsGone">{t("This block no longer exists.")}</div>;
  const sourceOptions = [["properties", T("Properties")], ["block", T("Whole block")], ...(sources.server ? [["server", T("Server")]] : [])];
  return createPortal(
    <div
      ref={leaveRef}
      // ui-allow owned-class: one .popover.propsPopover that App renders through a portal like ThreadPopover (ui-design.md, The properties popover)
      className="popover propsPopover"
      data-popover="props"
      data-view={view}
      role="dialog"
      aria-label={t("Block properties")}
      tabIndex={-1}
      style={pos ? { left: pos.left, top: pos.top, transformOrigin: pos.origin } : { left: 0, top: 0, visibility: "hidden" }}
      onMouseDown={(e) => e.stopPropagation()}
      // Escape closes it; the other keys stay with it rather than reach the
      // app's own (Enter or Delete on the library's selection), all but the
      // app's chords.
      onKeyDown={(e) => {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(true); return; }
        if (!e.ctrlKey && !e.metaKey && !e.altKey) e.stopPropagation();
      }}
    >
      <div className="propsHead">
        {target.back?.length ? (
          <button type="button" className="ctlBtn propsBack" onClick={nav.back} aria-label={t("Back")} title={t("Back")}>
            <ArrowLeftIcon size={14} />
          </button>
        ) : null}
        <span className="propsKind" aria-hidden="true">
          {head?.swatch ? <span className="propsSwatch propsKindSwatch" style={{ "--swatch": head.swatch }} /> : <KindIcon size={14} />}
        </span>
        <div className="propsHeadText">
          <div className="propsTitle">{head ? head.title : t("Block properties")}</div>
          {head ? <div className={`popoverHint propsPreview${head.empty ? " propsMuted" : ""}`} title={head.empty ? undefined : head.preview}>{head.preview}</div> : null}
        </div>
      </div>
      {chips.length ? (
        <div className="propsStatus">
          {chips.map((c) => (
            <span key={c.id} className={`uiTag ${c.tone || ""}`.trim()} data-chip={c.id}
              title={c.id === "zotero" ? zoteroReadonlyWords(block?.properties).badge : undefined}>{c.text}</span>
          ))}
          {chips.some((c) => c.action === "refetch") ? (
            <button type="button" className="uiBtn sm ghost" data-action="refetch" title={t("Read the server's copy again")} onClick={load}>
              {t("Refresh")}
            </button>
          ) : null}
          {chips.some((c) => c.action === "retry") ? (
            <button type="button" className="uiBtn sm ghost" onClick={load}>{t("Retry")}</button>
          ) : null}
        </div>
      ) : null}
      <div className="propsBar">
        <Segmented value={view} onChange={setView} options={VIEWS} />
        <button type="button" className={`uiBtn sm ghost propsCopyJson ${copied === "json" ? "on" : ""}`} disabled={!block}
          onClick={() => copy("json", json)}>
          <IconSwap swap={copied === "json"} from={<CopyIcon size={14} />} to={<CheckIcon size={14} />} />
          {t("Copy JSON")}
        </button>
      </div>
      <div ref={bodyRef} className="propsBody" style={bodyH ? { minHeight: bodyH } : undefined}>
        {view === "json" ? (
          block ? (
            <div className="propsJsonView">
              <Segmented value={shownSource} onChange={setSource} options={sourceOptions} />
              <CopyBox copied={copied === "json"} onCopy={() => copy("json", json)} title={t("Copy JSON")} label={t("Copy JSON")}>
                <pre className="propsJson" dangerouslySetInnerHTML={{ __html: jsonHtml }} />
              </CopyBox>
              {sources.server ? <div className="popoverHint">{t("This tab has changes the server hasn't saved yet.")}</div> : null}
            </div>
          ) : missing ? details : loading
        ) : (
          <Reveal ready={!!block || !!err} k={`props:${id}`} fallback={loading}>{details}</Reveal>
        )}
      </div>
      {rowMenu ? (
        <ContextMenu x={rowMenu.x} y={rowMenu.y} onClose={() => setRowMenu(null)} className="propsRowMenu">
          {rowMenuItems(rowMenu.row)}
        </ContextMenu>
      ) : null}
      <button type="button" className="uiClose uiCloseSm propsClose" onClick={() => onClose(true)} aria-label={t("Close")} title={t("Close")}>
        <XIcon size={14} />
      </button>
    </div>,
    document.body,
  );
}
