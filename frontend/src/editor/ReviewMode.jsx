// The account's way of taking AI edits, a button among the page header's
// (app/App.jsx), as Overleaf's Editing / Reviewing menu and Word's Track
// Changes: Editing, where an edit goes through once it is allowed (on the
// chat's card, or in an assistant's own question over MCP), or Reviewing,
// where an edit that can be drawn where it lands is made at once as a
// suggestion to accept or reject (chat/noteReview.js, the account pref
// "ai-review"). Its menu holds the two, then, while the page has
// suggestions (those made while reviewing stay after a switch back to
// Editing until they are decided), how many, Previous and Next, which step
// through them in the page's order (its tree's, notes under a folded one
// included, a deleted note at the place it had) and keep the menu open,
// each landed on where it shows, as a jump to a change is (`onLand`, App's
// landOnChange: its note's row flashed, a highlight's passage, a placed
// block's place on its page, a deleted note's struck row, whatever is
// folded over them unfolded), and Accept all and Reject all. Reject all
// stops at the first suggestion whose Reject stops: the menu lands on it,
// and its buttons say why. The mode is read as the button mounts and as
// its menu opens, so a switch in another tab or on another device shows.
// It watches the page while it shows (noteReview.watch), as the page's PDF
// and pages of paper do, so a suggestion made in another tab, on another
// device or by an assistant over MCP is drawn in the notes too.
import React, { useEffect, useMemo, useState } from "react";
import { T, t, tn } from "../shared/i18n/i18n.js";
import { CheckIcon, ChevronDownIcon, ChevronUpIcon, PencilIcon, TextSearchIcon, XIcon } from "../shared/ui/Icons";
import { ContextMenu, MenuDivider, MenuGroup, MenuItem, useMenuTrigger } from "../shared/ui/Menus";
import { changeLanding, deletedAt, noteReview, usePageChanges, useReviewing, useWatchPage } from "../chat/noteReview.js";
import { refusedOnBar } from "./ReviewButtons";

// The two modes: [reviewing, name, what it does, glyph].
const MODES = [
  [false, T("Editing"), T("AI edits go through once you allow them"), PencilIcon],
  [true, T("Reviewing"), T("AI edits become suggestions you accept or reject"), TextSearchIcon],
];

// The page's suggestions (their blocks, `ids`) in the order its tree holds
// them, depth first, a deleted note's among its parent's notes at its
// place; one the tree does not hold (its parent went too) last.
function inTreeOrder(blocks, rootId, ids) {
  if (!ids.length) return ids;
  const wanted = new Set(ids);
  const { changes } = noteReview.getSnapshot();
  const deleted = new Map(); // parent → its deleted notes' changes
  for (const id of ids) {
    const c = changes.get(id);
    if (c?.type === "delete") deleted.set(c.parentId, [...(deleted.get(c.parentId) || []), c]);
  }
  const out = [];
  const walk = (list, parent) => {
    const at = new Map(); // index among `list` → the deleted notes before it
    for (const c of (deleted.get(parent) || []).sort((a, b) => (a.position > b.position ? 1 : -1))) {
      const i = deletedAt(list, c);
      at.set(i, [...(at.get(i) || []), c.blockId]);
    }
    list.forEach((b, i) => {
      out.push(...(at.get(i) || []));
      if (wanted.has(b.id)) out.push(b.id);
      walk(b.children || [], b.id);
    });
    out.push(...(at.get(list.length) || []));
  };
  walk(blocks || [], rootId);
  const placed = new Set(out);
  return [...out, ...ids.filter((id) => !placed.has(id))];
}

// `blocks`: the page's tree; `onLand(landing)`: the app's jump to a change
// of the page (app/App.jsx landOnChange, chat/noteReview.js
// changeLanding's); `onStatus(text)`: what went wrong.
export function ReviewMode({ pageId, blocks, onLand, onStatus }) {
  useWatchPage(pageId);
  const reviewing = useReviewing();
  const { ids } = usePageChanges(pageId);
  const { kind, trigger, menu, close } = useMenuTrigger();
  const [at, setAt] = useState(""); // the suggestion last stepped to (its block)
  const [busy, setBusy] = useState(false);
  useEffect(() => { noteReview.loadMode(); }, []);
  useEffect(() => { if (kind) noteReview.loadMode(); }, [kind]);
  useEffect(() => { setAt(""); }, [pageId]);
  const order = useMemo(() => inTreeOrder(blocks, pageId, ids), [blocks, pageId, ids]);

  const land = (blockId) => {
    const change = noteReview.getSnapshot().changes.get(blockId);
    if (change) onLand?.(changeLanding(change));
  };
  const step = (delta) => {
    if (!order.length) return;
    const i = order.indexOf(at);
    const next = i < 0 ? (delta > 0 ? 0 : order.length - 1) : (i + delta + order.length) % order.length;
    setAt(order[next]);
    land(order[next]);
  };
  const run = async (fn) => {
    setBusy(true);
    try {
      await fn();
    } catch (err) {
      onStatus?.(err?.message || String(err));
    } finally {
      setBusy(false);
    }
  };
  const choose = (value) => run(() => (value === reviewing ? null : noteReview.setReviewing(value)));
  const rejectAll = () => run(async () => {
    const stopped = await noteReview.rejectPage(pageId, { refused: refusedOnBar });
    if (stopped) land(stopped);
  });
  const current = MODES.find(([value]) => value === reviewing);
  const ModeIcon = current[3];
  return (
    <>
      <button type="button" className={`pageActionBtn reviewModeBtn${reviewing ? " on" : ""}`}
        title={`${t(current[1])}: ${t(current[2])}`} aria-label={t(current[1])} {...trigger("mode")}>
        <ModeIcon size={16} />
        <span className="reviewModeLabel">{t(current[1])}</span>
        <ChevronDownIcon size={14} className="uiSelectChev" />
      </button>
      {menu ? (
        <ContextMenu {...menu} className="reviewModeMenu">
          {MODES.map(([value, name, hint, Icon]) => (
            <MenuItem key={name} role="menuitemradio" aria-checked={value === reviewing} icon={Icon} disabled={busy}
              trailing={value === reviewing ? <CheckIcon size={14} className="ctxMenuCheck" /> : null}
              onClick={() => { close(); choose(value); }}>
              <span>{t(name)}</span>
              <span className="reviewModeHint">{t(hint)}</span>
            </MenuItem>
          ))}
          {ids.length ? <>
            <MenuDivider />
            <MenuGroup label={tn("{n} suggestion on this page", "{n} suggestions on this page", ids.length)}>
              <MenuItem icon={ChevronUpIcon} onClick={() => step(-1)}>{t("Previous suggestion")}</MenuItem>
              <MenuItem icon={ChevronDownIcon} onClick={() => step(1)}>{t("Next suggestion")}</MenuItem>
              <MenuItem icon={CheckIcon} disabled={busy} title={t("Accept every suggestion on this page")}
                onClick={() => { close(); run(() => noteReview.acceptPage(pageId)); }}>{t("Accept all")}</MenuItem>
              <MenuItem icon={XIcon} danger disabled={busy} title={t("Reject every suggestion on this page, newest first")}
                onClick={() => { close(); rejectAll(); }}>{t("Reject all")}</MenuItem>
            </MenuGroup>
          </> : null}
        </ContextMenu>
      ) : null}
    </>
  );
}
