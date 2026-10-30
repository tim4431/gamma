// Conflict resolution for a clone (docs/dev/mirror.md), in git's words:
// local is this clone, remote is origin; and for a Zotero link
// (docs/dev/zotero_sync.md), whose sides are Gamma and Zotero. One card —
// ConflictCard — serves every surface: the chip on a block row or on the
// page's record (its popover walks the page's conflicts one by one), the
// sync pill's list and Settings (Workspaces → Clones and Zotero links,
// Account & sync → Publishing).
// A block both sides edited shows what each side changed as a word diff
// against the text before either edit (removed words struck through, added
// words in the side's colour), and under them the text that is in the block
// now — the automatic merge, its additions coloured by who wrote them.
// Every version carries a radio, the one in the block preselected, and one
// Apply confirms. A decision is an ordinary edit the next round pushes.
// Rows from before the base was kept (and diverged blocks, which have
// none) show the two texts against each other instead, as a Zotero
// link's do: Gamma's version, kept on both sides (or in Gamma alone while
// a receive-only link sends nothing), against Zotero's, which Apply can
// take instead (an edit Gamma cannot write back may also be sent
// anyway). The other kinds are acknowledged with OK; what each kind says is
// conflictKinds.js's.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { linkPath } from "./syncLinks.js";
import { T, t } from "../shared/i18n/i18n.js";
import { guideEvents } from "../guide/events.js";
import {
  AlertCircleIcon, ArrowDownIcon, ArrowUpIcon, BookIcon, CheckIcon, ChevronLeftIcon, ChevronRightIcon, ExternalLinkIcon,
  HardDriveIcon, MergeIcon, ServerIcon, TrashIcon, UploadIcon,
} from "../shared/ui/Icons";
import { conflictPlace, inkMergeLines, isTextual, isZotero, kindOf as conflictKind, zoteroReason } from "./conflictKinds.js";
import { Popover } from "../shared/ui/Surfaces";
import { lcs, tokens, wordDiff } from "../shared/lib/wordDiff.js";

// The word diff itself is shared (shared/lib/wordDiff.js); MirrorPopover
// takes it from here.
export { wordDiff };

// For every token of `a`, whether it is part of the longest common
// subsequence with `b`.
function matched(a, b) {
  const n = a.length, m = b.length;
  if (!n || !m) return new Array(n).fill(false);
  const dp = lcs(a, b);
  const out = new Array(n).fill(false);
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { out[i] = true; i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) i++;
    else j++;
  }
  return out;
}

// The result's tokens, each tagged by where it came from: "same" (in both
// versions), "mine" (local only), "theirs" (remote only), "both" (neither
// — a merge artefact, rare).
export function attribute(result, mine, theirs) {
  const r = tokens(result);
  const inMine = matched(r, tokens(mine));
  const inTheirs = matched(r, tokens(theirs));
  return r.map((text, i) => ({
    text,
    tag: inMine[i] && inTheirs[i] ? "same" : inMine[i] ? "mine" : inTheirs[i] ? "theirs" : "both",
  }));
}

// One side against the other: the tokens of `text` that `other` lacks are
// tagged `tag`, the rest "same" — what this version adds.
export function onlyIn(text, other, tag) {
  const t = tokens(text);
  const m = matched(t, tokens(other));
  return t.map((s, i) => ({ text: s, tag: m[i] ? "same" : tag }));
}

// What one side changed: the diff from the base, its additions in the
// side's colour, its removals struck through.
function sideParts(base, text, tag) {
  return wordDiff(base, text).map((p) => ({ text: p.text, tag: p.tag === "add" ? tag : p.tag }));
}

// What the merge did: the diff from the base, each added word coloured by
// the side that wrote it (both, when both added it).
function mergedParts(base, mine, theirs, result) {
  const added = (text) => new Set(wordDiff(base, text).filter((p) => p.tag === "add").flatMap((p) => tokens(p.text)));
  const mineAdds = added(mine), theirsAdds = added(theirs);
  return wordDiff(base, result).flatMap((p) => p.tag !== "add" ? [p] : tokens(p.text).map((t) => ({
    text: t,
    tag: /^\s+$/.test(t) ? "same" : mineAdds.has(t) && theirsAdds.has(t) ? "both" : mineAdds.has(t) ? "mine" : theirsAdds.has(t) ? "theirs" : "both",
  })));
}

// A kind's glyph, by the name conflictKinds.js gives it.
const KIND_ICONS = { merge: MergeIcon, alert: AlertCircleIcon, up: ArrowUpIcon, down: ArrowDownIcon, trash: TrashIcon };

// A row's kind (conflictKinds.js: its title and sentence) with its glyph.
function kindOf(conflict) {
  const kind = conflictKind(conflict);
  return { ...kind, Icon: KIND_ICONS[kind.icon] || AlertCircleIcon };
}

// The sides: a glyph, a word, where it lives. (The API keeps mine / theirs.)
const SIDE = {
  mine: { label: T("Local"), hint: T("this clone"), Icon: HardDriveIcon },
  theirs: { label: T("Remote"), hint: T("origin"), Icon: ServerIcon },
  result: { label: T("Merged"), hint: T("both edits in one text"), Icon: MergeIcon },
};
// A Zotero link's sides.
const SIDE_ZOTERO = {
  mine: { label: T("Gamma"), hint: T("this workspace"), Icon: HardDriveIcon },
  theirs: { label: T("Zotero"), hint: T("zotero.org"), Icon: BookIcon },
  result: SIDE.result,
};
const sidesOf = (conflict) => (isZotero(conflict) ? SIDE_ZOTERO : SIDE);

// Neighbouring tokens of one tag become one run, the whitespace between
// two marked tokens included — a phrase reads as a phrase, not as words.
function runs(parts) {
  const out = [];
  parts.forEach((p, i) => {
    const prev = out[out.length - 1];
    const bridge = /^\s+$/.test(p.text) && p.tag === "same" && prev && prev.tag !== "same"
      && parts[i + 1] && parts[i + 1].tag === prev.tag;
    const tag = bridge ? prev.tag : p.tag;
    if (prev && prev.tag === tag) prev.text += p.text;
    else out.push({ text: p.text, tag });
  });
  return out;
}

// Marked text: a run per tag, `mark.merge-<tag>` for everything but "same"
// (mine / theirs / both / add / del).
export function Marked({ parts }) {
  return runs(parts).map((p, i) => p.tag === "same" ? <span key={i}>{p.text}</span> : <mark key={i} className={`merge-${p.tag}`}>{p.text}</mark>);
}

// One version of the block: a header naming the side, a tag when it is the
// text in the block now, a radio to pick it, the text with the marks. Its
// name is the side and the hint it shows (`hint`, else the side's own).
function Version({ side, sides = SIDE, parts, current, hint, group, selected, onSelect, busy }) {
  const s = sides[side];
  const pickable = Boolean(onSelect);
  return (
    <section className={`mergeVersion ${side} ${current ? "current" : ""} ${selected === side ? "selected" : ""}`}
      aria-label={t("{label} ({hint})", { label: t(s.label), hint: hint || t(s.hint) })} onClick={pickable ? () => onSelect(side) : undefined}>
      <header className="mergeVersionHead">
        {pickable ? (
          <input type="radio" className="mergePick" name={group} value={side} checked={selected === side} disabled={busy}
            onChange={() => onSelect(side)} aria-label={t(s.label)}
            title={sides === SIDE_ZOTERO ? t("Take {side}'s version", { side: t(s.label) }) : t("Put the {side} text into the block", { side: t(s.label).toLowerCase() })} />
        ) : null}
        <span className={`mergeSide ${side}`} title={hint || t(s.hint)}><s.Icon size={14} />{t(s.label)}</span>
        <span className="popoverHint mergeSideHint">{hint || t(s.hint)}</span>
        {current ? <span className="uiTag">{t("in the block")}</span> : null}
      </header>
      <div className="mergeText"><Marked parts={parts} /></div>
    </section>
  );
}

// The versions of a textual conflict, one radio each and an Apply. With a
// base (an auto-merge): local and remote as what each changed, the merged
// text as what the merge did. Without one (diverged, or an older row): the
// two texts against each other, the merged text by attribution. The
// version in the block now is preselected, so Apply on its own marks the
// conflict resolved as it is.
function Versions({ conflict, busy, onUse }) {
  const c = conflict;
  const kind = conflictKind(c);
  const sides = sidesOf(c);
  const zotero = isZotero(c);
  // Two texts and no merged one: a diverged block, or a Zotero difference,
  // whose Gamma version is the one kept (unless the row says Zotero's).
  const two = c.kind === "diverged" || Boolean(kind.two);
  const currentSide = !two ? "result"
    : zotero ? (c.result && c.result === c.theirs && c.result !== c.mine ? "theirs" : "mine")
    : c.result === c.mine ? "mine" : "theirs";
  const [selected, setSelected] = React.useState(currentSide);
  const group = React.useId();
  // (a Zotero row's base is its projection, not a text both sides came from)
  const base = !two && typeof c.base === "string" && c.base ? c.base : null;
  const mineParts = base ? sideParts(base, c.mine, "mine") : onlyIn(c.mine, c.theirs, "mine");
  const theirsParts = base ? sideParts(base, c.theirs, "theirs") : onlyIn(c.theirs, c.mine, "theirs");
  const pick = { group, selected, onSelect: setSelected, busy, sides };
  const keeps = selected === currentSide;
  const sideWord = t(sides[selected].label);
  const reason = zoteroReason(c);
  React.useEffect(() => { guideEvents.emit("conflict.shown"); }, []);
  return (
    <>
      {reason ? <div className="popoverHint mergeReason">{reason}</div> : null}
      <div className={`mergeVersions ${two ? "two" : "three"}`} data-guide="merge.versions">
        <Version side="mine" parts={mineParts} current={currentSide === "mine"} hint={base || zotero ? t("changed here") : undefined} {...pick} />
        <Version side="theirs" parts={theirsParts} current={currentSide === "theirs"}
          hint={base ? t("changed on origin") : zotero ? t("changed in Zotero") : undefined} {...pick} />
        {two ? null : (
          <Version side="result" current {...pick}
            parts={base ? mergedParts(base, c.mine, c.theirs, c.result) : attribute(c.result, c.mine, c.theirs)} />
        )}
      </div>
      <div className="mergeActions">
        <span className="popoverHint mergeActionsHint">
          {keeps ? t("Keeps the text as it is and marks the conflict resolved")
            : zotero ? t("Takes {side}'s version, here and in Zotero", { side: sideWord })
            : t("Puts the {side} text into the block", { side: sideWord.toLowerCase() })}
        </span>
        {kind.send ? (
          <button type="button" className="uiBtn sm" disabled={busy} onClick={() => onUse("mine")}
            title={t("Write Gamma's version over Zotero's, losing what Gamma cannot hold")}>
            <UploadIcon size={14} /> {t("Send to Zotero anyway")}
          </button>
        ) : null}
        <button type="button" className="uiBtn sm primary" disabled={busy} onClick={() => onUse(keeps ? "keep" : selected)}
          title={keeps ? t("Mark resolved as it is") : zotero ? t("Write the chosen version; the next round sends it") : t("Write the chosen text; the next round pushes it")}>
          <CheckIcon size={14} /> {t("Apply")}
        </button>
      </div>
    </>
  );
}

// A non-textual decision: the one text involved, to acknowledge. A removed
// block or page carries the text of the side that lost it, a drawing the
// remote merged what each side lost (inkMergeLines), one it refused its
// reason; a Zotero row says what happened in its own sentence (`result`).
function Decision({ conflict }) {
  const c = conflict;
  if (isZotero(c)) {
    // the row's sentence (a refusal, Gamma giving up after its tries,
    // another member's annotation back), then what each side holds
    const reason = zoteroReason(c);
    const shown = [["mine", c.mine, t("in Gamma")], ["theirs", c.theirs, t("in Zotero")]].filter(([, text]) => text);
    if (!shown.length && !reason) return null;
    return (
      <>
        {reason ? <div className="popoverHint mergeReason">{reason}</div> : null}
        {shown.length ? (
          <div className={`mergeVersions ${shown.length > 1 ? "two" : "one"}`}>
            {shown.map(([side, text, hint]) => <Version key={side} side={side} sides={SIDE_ZOTERO} parts={[{ text, tag: "same" }]} hint={hint} />)}
          </div>
        ) : null}
      </>
    );
  }
  if (c.kind === "ink_merged") {
    const lines = inkMergeLines(c);
    return (
      <div className={`mergeVersions ${lines.length > 1 ? "two" : "one"}`}>
        {lines.map(({ side, text }) => (
          <Version key={side} side={side} parts={[{ text, tag: "same" }]} hint={side === "result" ? t("on both sides now") : undefined} />
        ))}
      </div>
    );
  }
  const dropped = c.kind === "dropped";
  const ours = dropped ? Boolean(c.mine) : c.kind === "kept_local_edit" || c.kind === "page_restored";
  const text = ours ? c.mine : c.theirs;
  if (!text) return null;
  const hint = dropped ? t("removed") : ours ? t("kept and pushed back") : c.kind === "file" ? t("refused") : t("pulled back");
  return (
    <div className="mergeVersions one">
      <Version side={ours ? "mine" : "theirs"} parts={[{ text, tag: "same" }]} hint={hint} />
    </div>
  );
}

// A link's open conflicts and their resolution, for the lists (the sync
// pill's review view, Settings → Workspaces → Clones and Zotero links):
// `[items, busy, resolve, keepAll]` — `items` null while loading;
// `resolve(conflict, choice)` posts the choice, drops the row and raises
// `gamma:mirror` so the page's chips and the pill follow; `keepAll()` marks
// every listed one seen, keeping what the workspace holds (a Zotero link's
// list offers it: a first link can leave many); `onError(message)` hears a
// failed post. `kind` is the link's, "mirror" or "zotero" (syncLinks.js).
export function useConflicts(wsId, { onError, kind = "mirror" } = {}) {
  const [items, setItems] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  React.useEffect(() => {
    setItems(null);
    apiJson(`${API}${linkPath(kind, wsId, "/conflicts")}`).then((d) => setItems(d.conflicts || [])).catch(() => setItems([]));
  }, [wsId, kind]);
  const post = React.useCallback((c, choice) => apiJson(`${API}${linkPath(kind, wsId, `/conflicts/${c.id}`)}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ choice }),
  }), [wsId, kind]);
  const resolve = React.useCallback(async (c, choice) => {
    setBusy(true);
    try {
      await post(c, choice);
      setItems((prev) => (prev || []).filter((x) => x.id !== c.id));
    } catch (err) {
      onError?.(err.message);
    } finally {
      setBusy(false);
      window.dispatchEvent(new CustomEvent("gamma:mirror"));
    }
  }, [post, onError]);
  const keepAll = React.useCallback(async () => {
    setBusy(true);
    const kept = new Set();
    try {
      for (const c of items || []) {
        await post(c, "keep");
        kept.add(c.id);
      }
    } catch (err) {
      onError?.(err.message);
    } finally {
      setItems((prev) => (prev || []).filter((x) => !kept.has(x.id)));
      setBusy(false);
      window.dispatchEvent(new CustomEvent("gamma:mirror"));
    }
  }, [items, post, onError]);
  return [items, busy, resolve, keepAll];
}

// The card. `onResolve(conflict, choice)` with choice keep | mine | theirs;
// `nav` {index, total, onStep(delta)} walks a page's conflicts (the row
// chip); `onOpen` jumps to the block (the lists), or to the folder or
// label a row of the folders or labels tree is about; `showPage` names
// the page.
export function ConflictCard({ conflict, busy, onResolve, nav, onOpen, showPage = false }) {
  const kind = kindOf(conflict);
  const textual = isTextual(conflict);
  const many = nav && nav.total > 1;
  const pageGone = conflict.kind === "dropped" && conflict.page_title == null; // a removed page: nothing to open
  const place = conflictPlace(conflict);
  const opens = place === "folder" ? t("Open the folder") : place === "label" ? t("Open the label") : "";
  return (
    <div className={`mergeCard kind-${conflict.kind}`}>
      <div className="mergeHead">
        <span className="mergeKindIcon"><kind.Icon size={14} /></span>
        <span className="mergeHeadText">
          <span className="popoverTitle">
            {t(kind.short)}
            {showPage && conflict.page_title && place === "page" ? <span className="mergePage" title={conflict.page_title}> · {conflict.page_title}</span> : null}
          </span>
          <span className="popoverHint">{t(kind.long)}</span>
        </span>
        {many ? (
          <span className="mergeNav" title={t("The page's conflicts, one by one")}>
            <button type="button" className="ctlBtn" onClick={() => nav.onStep(-1)} aria-label={t("Previous conflict")}><ChevronLeftIcon size={16} /></button>
            <span className="mergeNavCount">{nav.index} / {nav.total}</span>
            <button type="button" className="ctlBtn" onClick={() => nav.onStep(1)} aria-label={t("Next conflict")}><ChevronRightIcon size={16} /></button>
          </span>
        ) : null}
        {onOpen && !pageGone ? (
          <button type="button" className="uiBtn sm iconSq" onClick={() => onOpen(conflict)} aria-label={opens || t("Open the block")} title={opens || t("Open the page on this block")}>
            <ExternalLinkIcon size={16} />
          </button>
        ) : null}
      </div>
      {textual ? <Versions conflict={conflict} busy={busy} onUse={(choice) => onResolve(conflict, choice)} /> : <Decision conflict={conflict} />}
      {textual ? null : (
        <div className="mergeActions">
          <button type="button" className="uiBtn sm primary" disabled={busy} onClick={() => onResolve(conflict, "keep")} title={t("Mark it seen")}>
            <CheckIcon size={14} /> {t("OK")}
          </button>
        </div>
      )}
    </div>
  );
}

// The chip on a block row and its popover. Controlled when `open` +
// `onOpenChange` are given (App walks the page's conflicts through `nav`),
// self-contained otherwise. `onResolve(conflict, choice)`.
export function MergeChip({ conflict, onResolve, open: openProp, onOpenChange, nav }) {
  const [openState, setOpenState] = React.useState(false);
  const controlled = typeof openProp === "boolean" && Boolean(onOpenChange);
  const open = controlled ? openProp : openState;
  const setOpen = controlled ? onOpenChange : setOpenState;
  const [busy, setBusy] = React.useState(false);
  const ref = React.useRef(null);
  React.useEffect(() => {
    if (!open) return undefined;
    const away = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const key = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", key); };
  }, [open, setOpen]);
  const kind = kindOf(conflict);
  async function resolve(c, choice) {
    setBusy(true);
    try { await onResolve(c, choice); } finally { setBusy(false); }
  }
  return (
    <span className="mergeChipWrap" ref={ref} onMouseDown={(e) => e.stopPropagation()}>
      <button type="button" className={`mergeChip ${open ? "on" : ""}`} title={t("{short} — click to resolve", { short: t(kind.short) })}
        aria-label={t("Conflict to resolve")} onClick={(e) => { e.stopPropagation(); setOpen(!open); }}>
        <kind.Icon size={14} />
      </button>
      {open ? (
        <Popover className="mergePopover" role="dialog" aria-label={t("Merge")}>
          <ConflictCard conflict={conflict} busy={busy} onResolve={resolve} nav={nav} />
        </Popover>
      ) : null}
    </span>
  );
}
