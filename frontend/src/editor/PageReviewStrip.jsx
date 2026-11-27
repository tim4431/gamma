// The strip at the foot of a page's notes while agents' changes are marked
// on it (chat/noteReview.js). While some wait for review (made without
// asking): how many, Undo All and Keep All. Else, the kept ones' marks (for
// as long as the account lets a mark stay, Settings → AI → Tool usage): how
// many, and Clear marks, which takes the marks away and leaves the changes
// (their rows in the chat still undo them). ‹ and › step through the
// changes in the page's order (its tree's, notes under a folded one
// included), each landed on as a jump to a note is (`onLand`: its folded
// parents unfolded, scrolled to the middle of the notes), flashed and
// unfolded. It watches the page while it shows (noteReview.watch), as the
// page's PDF and pages of paper do, so a change made in another tab, on
// another device or by an assistant over MCP is drawn here too. Undo All
// stops at the first change whose Undo stops: that note's bar says why,
// and the strip goes to it.
import React, { useEffect, useMemo, useState } from "react";
import { t, tn } from "../shared/i18n/i18n.js";
import { CheckIcon, ChevronLeftIcon, ChevronRightIcon, EyeOffIcon, SparklesIcon, UndoIcon } from "../shared/ui/Icons";
import { noteReview, usePageChanges, useWatchPage } from "../chat/noteReview.js";
import { flattenBlocks } from "../shared/model/blockModel.js";
import { refusedOnBar } from "./NoteReviewBar";

// A changed note's row flashes once it is drawn: a folded parent unfolds
// for it a render or two after the press.
function flash(blockId, frames = 10) {
  const el = document.querySelector(`.blockRowWrap[data-block-id="${CSS.escape(blockId)}"] .blockRow`);
  if (!el) {
    if (frames > 0) requestAnimationFrame(() => flash(blockId, frames - 1));
    return;
  }
  el.classList.remove("reviewFocus");
  void el.offsetWidth; // the flash runs again on a second press
  el.classList.add("reviewFocus");
}

// `blocks`: the page's tree; `onLand(blockId)`: the app's jump to a note of
// the page (app/App.jsx scrollToBlock), which unfolds its parents and
// scrolls it to the middle of the notes.
export function PageReviewStrip({ pageId, blocks, readOnly = false, onLand, onStatus }) {
  useWatchPage(pageId, !readOnly);
  const { ids, open } = usePageChanges(pageId);
  // the changed notes in the order the page holds them: its tree, depth first
  const order = useMemo(() => {
    const wanted = new Set(ids);
    return wanted.size ? flattenBlocks(blocks).filter((b) => wanted.has(b.id)).map((b) => b.id) : ids;
  }, [blocks, ids]);
  const [at, setAt] = useState(""); // the change last stepped to (its block)
  const [busy, setBusy] = useState(false);
  useEffect(() => { setAt(""); }, [pageId]);
  if (readOnly || !ids.length) return null;
  const i = order.indexOf(at);
  const land = (blockId) => {
    noteReview.focus(blockId);
    onLand?.(blockId);
    flash(blockId);
  };
  const step = (delta) => {
    if (!order.length) return;
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
  const undoAll = () => run(async () => {
    const stopped = await noteReview.undoPage(pageId, { refused: refusedOnBar });
    if (stopped) land(stopped);
  });
  return (
    <div className="pageReviewStrip" role="region" aria-label={open ? t("AI changes to review") : t("AI changes on this page")}>
      <span className="pageReviewCount">
        <SparklesIcon size={14} />
        {open ? tn("{n} AI change to review", "{n} AI changes to review", open)
          : tn("{n} AI change marked", "{n} AI changes marked", ids.length)}
      </span>
      <span className="pageReviewNav">
        <button type="button" className="ctlBtn" title={t("Previous change")} aria-label={t("Previous change")} onClick={() => step(-1)}>
          <ChevronLeftIcon size={14} />
        </button>
        <span className="pageReviewAt">{i >= 0 ? t("{i} of {n}", { i: i + 1, n: order.length }) : null}</span>
        <button type="button" className="ctlBtn" title={t("Next change")} aria-label={t("Next change")} onClick={() => step(1)}>
          <ChevronRightIcon size={14} />
        </button>
      </span>
      <span className="pageReviewSpacer" />
      {open ? <>
        <button type="button" className="uiBtn sm ghost" disabled={busy} title={t("Undo every change on this page that waits for review")}
          onClick={undoAll}>
          <UndoIcon size={14} />{t("Undo All")}
        </button>
        <button type="button" className="uiBtn sm primary" disabled={busy} title={t("Keep every change on this page that waits for review")}
          onClick={() => run(() => noteReview.keepPage(pageId))}>
          <CheckIcon size={14} />{t("Keep All")}
        </button>
      </> : (
        <button type="button" className="uiBtn sm ghost" disabled={busy}
          title={t("Take the marks away; the changes stay, and their rows in the chat still undo them")}
          onClick={() => run(() => noteReview.clearPage(pageId))}>
          <EyeOffIcon size={14} />{t("Clear marks")}
        </button>
      )}
    </div>
  );
}
