// Taking back several of the AI's changes at once: every change of the
// conversation that can still be reverted (chat/aiRevert.js `revertible`),
// grouped under the message that asked for it, the latest first, each with a
// checkbox — the reply it was opened from ticked, "Select all" for every
// one. Revert takes the ticked ones back newest first, each as its row under
// the reply does (`sendRevert`, POST /api/ai/revert), and stops at the
// first that asks: it says why and offers to revert it anyway where that
// can be forced, or to skip it. The list is the one the dialog opened with,
// so a row reverted here stays, marked, and nothing moves under the
// pointer. AgentChanges' "Revert…" opens it; ChatDock holds it.
import React, { useMemo, useState } from "react";
import { t, tn } from "../shared/i18n/i18n.js";
import { DialogOverlay } from "../shared/ui/Surfaces";
import { backdropPress } from "../shared/ui/press.js";
import { CheckIcon, PencilIcon, UndoIcon } from "../shared/ui/Icons";
import { revertible, revertRefusal } from "./aiRevert.js";
import { ACTION_ICONS, changeText, refusalCopy, sendRevert } from "./AgentChanges";
import { WordDiff } from "./ApprovalCard";

// No links in a checkbox's label: a click on one would tick the box too.
const PLAIN = { page: (_, label) => <span>{label}</span>, block: (_, label) => <span>{label}</span> };
const keyOf = (item) => `${item.id}:${item.index}`;

// The ticked state of a checkbox standing for several: on, off or mixed.
function Mixed({ on, some, ...rest }) {
  return <input type="checkbox" checked={on} ref={(el) => { if (el) el.indeterminate = some && !on; }} {...rest} />;
}

// `onReverted(id, indexes, answer)`: the actions at `indexes` of reply `id`
// were reverted — the dock marks them in the saved conversation. `busy` (a
// reply streaming here) holds the Revert button.
export default function RevertChanges({ messages, fromId, busy = false, onReverted, onClose }) {
  const [items] = useState(() => revertible(messages));
  const [picked, setPicked] = useState(() => new Set(items.filter((item) => item.id === fromId).map(keyOf)));
  // key → {working} | {done, kept?} | {refusal}
  const [rows, setRows] = useState({});
  const [running, setRunning] = useState(false);
  const groups = useMemo(() => {
    const asked = {};
    let last = "";
    for (const m of messages) {
      if (m.role === "user") last = m.text || "";
      else if (m.id) asked[m.id] = last;
    }
    const out = [];
    for (const item of items) {
      if (out.at(-1)?.id === item.id) out.at(-1).items.push(item);
      else out.push({ id: item.id, asked: asked[item.id] || "", items: [item] });
    }
    return out;
  }, [items, messages]);
  const setRow = (key, state) => setRows((prev) => ({ ...prev, [key]: state }));
  const open = (list) => list.filter((item) => !rows[keyOf(item)]?.done);
  const ticked = open(items).filter((item) => picked.has(keyOf(item)));
  const reverted = items.filter((item) => rows[keyOf(item)]?.done).length;
  const stopped = items.find((item) => rows[keyOf(item)]?.refusal);
  const pick = (list, on) => setPicked((prev) => {
    const next = new Set(prev);
    for (const item of open(list)) if (on) next.add(keyOf(item)); else next.delete(keyOf(item));
    return next;
  });
  const close = () => { if (!running) onClose(); };

  // Newest first, as `items` are; `force` reverts the row that stopped anyway.
  const run = async (force = "") => {
    setRunning(true);
    try {
      for (const item of ticked) {
        const key = keyOf(item);
        setRow(key, { working: true });
        try {
          const answer = await sendRevert(item.action, { force: key === force });
          onReverted(item.id, [item.index], answer);
          setRow(key, { done: true, kept: answer?.kept || 0 });
        } catch (err) {
          setRow(key, { refusal: revertRefusal(err) });
          return;
        }
      }
    } finally {
      setRunning(false);
    }
  };
  const skip = (item) => {
    pick([item], false);
    setRow(keyOf(item), null);
  };

  const stop = stopped ? (() => {
    const copy = refusalCopy(rows[keyOf(stopped)].refusal, false);
    const preview = rows[keyOf(stopped)].refusal.preview;
    return (
      <div className="chatRevertAsk revertDialogAsk" role="alert">
        <div>{copy.text}</div>
        {preview?.diff?.length ? <WordDiff diff={preview.diff} /> : null}
        <div className="chatApprovalActions">
          {copy.force ? (
            <button type="button" className="uiBtn sm" disabled={running || busy}
              onClick={() => run(keyOf(stopped))}>{copy.force}</button>
          ) : null}
          <button type="button" className="uiBtn sm ghost" disabled={running} onClick={() => skip(stopped)}>
            {t("Skip it")}
          </button>
        </div>
      </div>
    );
  })() : null;

  const row = (item) => {
    const key = keyOf(item);
    const state = rows[key] || {};
    const Icon = ACTION_ICONS[item.action.kind] || PencilIcon;
    return (
      <React.Fragment key={key}>
        <label className={`docPickerItem revertDialogRow${state.done ? " reverted" : ""}`}>
          <input type="checkbox" checked={state.done || picked.has(key)} disabled={running || !!state.done}
            onChange={(e) => pick([item], e.target.checked)} />
          <Icon size={14} />
          <span className="chatChangeText">{changeText(item.action, PLAIN)}</span>
          {state.working ? <span className="transferSpin inline" aria-hidden="true" />
            : state.done ? <span className="chatChangeTag"><CheckIcon size={12} />{t("Reverted")}</span> : null}
        </label>
        {state.done && state.kept ? (
          <div className="chatRevertAsk revertDialogAsk" role="status">
            {tn("{n} of these pages changed since, or was deleted, and was left as it is.",
              "{n} of these pages changed since, or were deleted, and were left as they are.", state.kept)}
          </div>
        ) : null}
        {stopped === item ? stop : null}
      </React.Fragment>
    );
  };

  const all = open(items);
  return (
    <DialogOverlay {...backdropPress(close)}>
      <div className="reportModal docPickerModal revertDialog" role="dialog" aria-modal="true"
        aria-label={t("Revert the AI's changes")} onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } }}>
        <div className="confirmHead">
          <span className="confirmIcon"><UndoIcon size={16} /></span>
          <span className="settingText">
            <span className="reportModalTitle">{t("Revert the AI's changes")}</span>
            <span className="confirmMessage">
              {t("Tick the changes to take back. They go back newest first, and whatever anyone wrote since stays.")}
            </span>
          </span>
        </div>
        {all.length ? (
          <label className="docPickerItem docPickerNotes">
            <Mixed on={ticked.length === all.length} some={ticked.length > 0} disabled={running}
              onChange={(e) => pick(items, e.target.checked)} />
            <span className="attachName">{t("Select all")}</span>
          </label>
        ) : null}
        <div className="reportPageList docPickerList">
          {groups.map((g) => {
            const left = open(g.items);
            const on = left.filter((item) => picked.has(keyOf(item))).length;
            return (
              <React.Fragment key={g.id}>
                <label className="docPickerItem revertDialogGroup">
                  <Mixed on={left.length > 0 && on === left.length} some={on > 0} disabled={running || !left.length}
                    onChange={(e) => pick(g.items, e.target.checked)} />
                  <span className="attachName">{g.asked ? `“${g.asked.split("\n")[0]}”` : t("A reply")}</span>
                </label>
                {g.items.map(row)}
              </React.Fragment>
            );
          })}
        </div>
        <div className="reportModalBtns">
          {reverted ? <span className="revertDialogDone">{tn("{n} change reverted", "{n} changes reverted", reverted)}</span> : null}
          <button type="button" className="uiBtn" disabled={running} onClick={close} autoFocus>
            {reverted && !ticked.length ? t("Done") : t("Cancel")}
          </button>
          <button type="button" className="uiBtn primary" disabled={running || busy || !ticked.length || !!stopped}
            onClick={() => run()}>
            {ticked.length ? tn("Revert {n} change", "Revert {n} changes", ticked.length) : t("Revert")}
          </button>
        </div>
      </div>
    </DialogOverlay>
  );
}
