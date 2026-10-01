// What an agent reply changed, listed under its steps pill (grouped by
// chat/agentSteps.js): renamed, filed, saved or restored pages, and edited,
// added or moved notes, each with a link to what changed. A note change has
// a revert button, and the list a "Revert all" when it holds more than one
// (chat/aiRevert.js, gamma/ai_revert.py). A revert keeps what anyone wrote
// since. When the note changed where the agent wrote, was filled in or moved
// on, the row asks first: it says so, shows what reverting anyway would do
// as a word diff, and offers to do it. "Revert all" goes newest first and
// stops at the first row that asks. A reverted change stays listed, marked,
// with a redo button that puts it back the same way (and asks the same way).
import React, { useContext, useState } from "react";
import { API, apiJson } from "../shared/lib/utils";
import { GammaNavContext } from "../shared/ui/Widgets";
import { t } from "../shared/i18n/i18n.js";
import { BookIcon, CloudDownloadIcon, EyeIcon, FilePlusIcon, FolderIcon, GlobeIcon, HistoryIcon, ListIcon,
  PenIcon, PencilIcon, PlusIcon, QuoteIcon, RedoIcon, SearchIcon, UndoIcon, XIcon } from "../shared/ui/Icons";
import { changePlace, isChange, noteChangeText } from "./agentSteps";
import { canRedo, canRevert, revertOrder, revertRefusal } from "./aiRevert.js";
import { WordDiff } from "./ApprovalCard";

// The icon of each action kind, in this list and on the tool chips under the
// pill (ChatDock), each chip expanding to the raw call the server ran.
export const ACTION_ICONS = { rename: PencilIcon, move: FolderIcon, search: SearchIcon, read: BookIcon, view: EyeIcon, ink: PenIcon, cite: QuoteIcon, list: ListIcon, edit: PencilIcon, create: PlusIcon, websearch: GlobeIcon, fetch: CloudDownloadIcon, save: FilePlusIcon, restore: HistoryIcon, error: XIcon };

// What a row that stopped says, and the button that reverts (or redoes)
// anyway — none when it can't be forced.
function refusalCopy({ conflict, preview, detail, status }, redo) {
  if (conflict === "changed") {
    return redo
      ? { text: t("The note was changed since. Redoing anyway makes this change:"), force: t("Redo anyway") }
      : { text: t("The note was changed since. Reverting anyway makes this change:"), force: t("Revert anyway") };
  }
  if (conflict === "filled") {
    return {
      text: preview?.children ? t("Notes were added under it since. Reverting deletes it with them.")
        : t("It was typed in since. Reverting deletes what it says now:"),
      force: t("Delete anyway"),
    };
  }
  if (conflict === "moved") {
    return redo
      ? { text: t("The note was moved since. Redoing puts it where the AI moved it."), force: t("Move anyway") }
      : { text: t("The note was moved since. Reverting puts it back where the AI found it."), force: t("Move back anyway") };
  }
  return { text: status === 404 ? t("The note was deleted since.") : detail };
}

// `onReverted(indexes, answer, reverted)`: the actions at those indexes were
// reverted (`reverted` false: redone) — the dock marks them in the saved
// conversation. Without it (a viewer, the share view) nothing can be
// reverted; `busy` (a reply streaming here) holds the buttons.
export default function AgentChanges({ actions, onOpenPage, onReverted, busy = false }) {
  const nav = useContext(GammaNavContext);
  // index → {working} while its request runs, or {refusal, redo} once it stopped
  const [rows, setRows] = useState({});
  const [all, setAll] = useState(false);
  const changes = actions.map((a, i) => ({ a, i })).filter(({ a }) => isChange(a));
  const library = changes.filter(({ a }) => changePlace(a) === "library");
  const notes = changes.filter(({ a }) => changePlace(a) === "notes");
  if (!library.length && !notes.length) return null;
  const setRow = (i, state) => setRows((prev) => {
    const next = { ...prev };
    if (state) next[i] = state; else delete next[i];
    return next;
  });
  const working = all || Object.values(rows).some((r) => r.working);
  const revertOne = async (i, { force = false, redo = false } = {}) => {
    setRow(i, { working: true });
    try {
      const { kind, block_id, revert } = actions[i];
      const answer = await apiJson(`${API}/ai/revert`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, block_id, revert, force, redo }),
      });
      setRow(i, null);
      onReverted([i], answer, !redo);
      return true;
    } catch (err) {
      setRow(i, { refusal: revertRefusal(err), redo });
      return false;
    }
  };
  const revertAll = async () => {
    setAll(true);
    try {
      for (const i of revertOrder(actions)) if (!(await revertOne(i))) break;
    } finally {
      setAll(false);
    }
  };

  const pageLink = (id, label) => id
    ? <button type="button" className="chatChangeLink" onClick={() => onOpenPage?.(id)}>{label}</button>
    : <span>{label}</span>;
  const blockLink = (a, label) => a.block_id && nav?.openBlock
    ? <button type="button" className="chatChangeLink" onClick={() => nav.openBlock(a.block_id, a.page_id)}>{label}</button>
    : pageLink(a.page_id, label);
  const libraryRow = (a) => {
    if (a.kind === "rename" && a.to) {
      return <><s className="chatChangeOld">{a.from}</s> → {pageLink(a.page_id, a.to)}</>;
    }
    if (a.kind === "move" && a.title) {
      return <span title={a.from ? t("Was in: {folders}", { folders: a.from }) : undefined}>
        {t("{page} moved to {folder}", { page: pageLink(a.page_id, a.title), folder: <strong>{a.to || t("the library root")}</strong> })}
      </span>;
    }
    if ((a.kind === "save" || a.kind === "restore") && a.title) {
      const args = { page: pageLink(a.page_id, a.title), folder: <strong>{a.to || t("the library root")}</strong> };
      return a.kind === "restore" ? t("Restored {page} to {folder}", args)
        : a.existed ? t("{page}, already in your library, filed in {folder}", args)
          : t("Saved {page} to {folder}", args);
    }
    return pageLink(a.page_id, a.summary); // saved before the structured fields
  };
  const noteRow = (a) => (a.title ? noteChangeText(a, blockLink(a, `“${a.title}”`)) : blockLink(a, a.summary));
  const revertible = onReverted ? notes.filter(({ a }) => canRevert(a)) : [];

  const refusalRow = (i) => {
    const { refusal, redo } = rows[i];
    const copy = refusalCopy(refusal, redo);
    return (
      <div className="chatRevertAsk" role="alert">
        <div>{copy.text}</div>
        {refusal.preview?.diff?.length ? <WordDiff diff={refusal.preview.diff} /> : null}
        <div className="chatApprovalActions">
          {copy.force ? (
            <button type="button" className="uiBtn sm" disabled={working || busy}
              onClick={() => revertOne(i, { force: true, redo })}>{copy.force}</button>
          ) : null}
          <button type="button" className="uiBtn sm ghost" onClick={() => setRow(i, null)}>
            {copy.force ? t("Keep it") : t("Dismiss")}
          </button>
        </div>
      </div>
    );
  };
  const changeRow = ({ a, i }, place) => {
    const Icon = ACTION_ICONS[a.kind] || PencilIcon;
    const row = rows[i];
    const redo = !!a.reverted;
    const label = redo ? t("Redo this change") : t("Revert this change");
    const ButtonIcon = redo ? RedoIcon : UndoIcon;
    return (
      <React.Fragment key={i}>
        <div className={`chatChange${redo ? " reverted" : ""}`}>
          <Icon size={14} />
          <span className="chatChangeText">{place === "library" ? libraryRow(a) : noteRow(a)}</span>
          {redo ? <span className="chatChangeTag">{t("Reverted")}</span> : null}
          {onReverted && (redo ? canRedo(a) : canRevert(a)) ? (
            <button type="button" className="ctlBtn chatChangeRevert" disabled={working || busy || !!row?.refusal}
              title={label} aria-label={label} onClick={() => revertOne(i, { redo })}>
              {row?.working ? <span className="transferSpin inline" aria-hidden="true" /> : <ButtonIcon size={14} />}
            </button>
          ) : null}
        </div>
        {row?.refusal ? refusalRow(i) : null}
      </React.Fragment>
    );
  };
  const group = (list, place) => list.length ? (
    <div className="chatChanges" key={place}>
      <div className="chatChangesHead">
        <span>{place === "library" ? t("Changed in your library · {n}", { n: list.length }) : t("Changed in your notes · {n}", { n: list.length })}</span>
        {place === "notes" && revertible.length > 1 ? (
          <button type="button" className="uiBtn sm ghost chatChangesAll" disabled={working || busy} onClick={revertAll}
            title={t("Revert every change listed here, newest first")}>
            <UndoIcon size={12} />{t("Revert all")}
          </button>
        ) : null}
      </div>
      {list.map((c) => changeRow(c, place))}
    </div>
  ) : null;
  return <>{group(library, "library")}{group(notes, "notes")}</>;
}
