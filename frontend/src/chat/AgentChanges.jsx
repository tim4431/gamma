// What an agent reply changed, listed under its steps pill (grouped by
// chat/agentSteps.js): renamed, filed, saved, deleted or restored pages,
// renamed, made, applied or deleted folders and labels, and edited, added or
// moved notes, each with a link to what changed. A change the server
// journaled for review (`change_id`, chat/noteReview.js) is the same
// decision as its note's bar: while it waits for review its row has Keep
// and Undo, once kept it says so and keeps Undo, once undone it has Redo,
// whichever surface decided. A note change, pages the agent renamed,
// moved, labelled, deleted or restored (one or many in one call), and a
// deleted folder or label have an undo button (chat/aiRevert.js,
// gamma/ai_revert.py). An undo keeps what anyone wrote since. When the
// note changed where the agent wrote, was filled in or moved on, the row
// asks first: it says so, shows what undoing anyway would do as a word
// diff, and offers to do it. An undone change stays listed, marked, with a
// redo button that puts it back the same way (and asks the same way). A
// call that changed several pages is one row that unfolds to its pages,
// and undoes them together; pages changed since are left as they are, and
// an undo that would leave every one says so instead. Keep All in the head
// keeps the reply's changes that wait for review; "Undo…" opens the dialog
// that takes back several changes at once, this reply's or any of the
// conversation's (chat/RevertChanges.jsx).
import React, { useContext, useState, useSyncExternalStore } from "react";
import { API, apiJson } from "../shared/lib/utils";
import { GammaNavContext } from "../shared/ui/Widgets";
import { t, tn } from "../shared/i18n/i18n.js";
import { ActivityIcon, BookIcon, BotIcon, CheckIcon, CloudDownloadIcon, ExportIcon, EyeIcon, FilePlusIcon, FileTextIcon,
  FolderIcon, GlobeIcon, HighlightIcon, HistoryIcon, ImageIcon, LabelIcon, ListIcon, LocateIcon, MessageSquareIcon, PenIcon,
  PencilIcon, PlusIcon, QuoteMarkIcon, RedoIcon, ScissorsIcon, SearchIcon, SlashIcon, TerminalIcon, TrashIcon, UndoIcon,
  XIcon } from "../shared/ui/Icons";
import { PathLink } from "../editor/PathLink.jsx";
import { changeCount, changePlace, isChange, noteChangeText } from "./agentSteps";
import { canRedo, canRevert, revertKind, revertRefusal } from "./aiRevert.js";
import { WordDiff } from "./ApprovalCard";
import { CHANGE_STATES, noteReview, useChangeState } from "./noteReview.js";

// The icon of each action kind, in this list and on the tool chips under the
// pill (chat/ChatTranscript.jsx), each chip expanding to the raw call the
// server ran; a computer agent's steps are chips of kinds of their own
// (`file`, `fileEdit`, `run`, `work`: chat/computer/computerChat.js
// stepChip).
export const ACTION_ICONS = { file: FileTextIcon, fileEdit: PencilIcon, run: TerminalIcon, work: ActivityIcon, rename: PencilIcon, move: FolderIcon, search: SearchIcon, read: BookIcon, view: EyeIcon, ink: PenIcon, image: ImageIcon, cite: QuoteMarkIcon, list: ListIcon, edit: PencilIcon, create: PlusIcon, delete: TrashIcon, clip: ScissorsIcon, websearch: GlobeIcon, fetch: CloudDownloadIcon, helper: BotIcon, save: FilePlusIcon, restore: HistoryIcon, label: LabelIcon, comment: MessageSquareIcon, resolve: CheckIcon, highlight: HighlightIcon, write: MessageSquareIcon, show: LocateIcon, skill: SlashIcon, export: ExportIcon, error: XIcon };

// Take one change back, or with `redo` put an undone one back, as its row
// and the dialog do: `{page_id, noop, kept?}`. A change the journal holds
// goes through it (chat/noteReview.js, POST /api/ai/changes/<id>), so its
// note and every other tab hear of it; one it no longer holds, or saved
// before it, through POST /api/ai/revert with the record the action keeps.
// A refusal throws the API error, which `revertRefusal` reads.
export async function sendRevert(a, { force = false, redo = false } = {}) {
  if (a.change_id) {
    try {
      return await noteReview.decide(a.change_id, redo ? "redo" : "undo", { force });
    } catch (err) {
      if (err?.status !== 404) throw err;
    }
  }
  return apiJson(`${API}/ai/revert`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: revertKind(a), block_id: a.block_id || "", revert: a.revert, force, redo }),
  });
}

// What a row that stopped says, and the button that reverts (or redoes)
// anyway — none when it can't be forced.
export function refusalCopy({ conflict, preview, detail, status }, redo) {
  if (conflict === "changed") {
    return redo
      ? { text: t("The note was changed since. Redoing anyway makes this change:"), force: t("Redo anyway") }
      : { text: t("The note was changed since. Undoing anyway makes this change:"), force: t("Undo anyway") };
  }
  if (conflict === "filled") {
    if (preview?.page) {
      return { text: t("Notes, pages or comments were added to the page since. Undoing moves it to Recently deleted with them."),
        force: t("Move to Recently deleted anyway") };
    }
    return {
      text: preview?.children ? t("Notes were added under it since. Undoing deletes it with them.")
        : preview?.drawn ? t("Strokes were added to it since. Undoing deletes them with it.")
          : preview?.threads ? t("It was commented on since. Undoing deletes it, and its comments with it.")
            : t("It was typed in since. Undoing deletes what it says now:"),
      force: t("Delete anyway"),
    };
  }
  if (conflict === "moved") {
    return redo
      ? { text: t("The note was moved since. Redoing puts it where the AI moved it."), force: t("Move anyway") }
      : { text: t("The note was moved since. Undoing puts it back where the AI found it."), force: t("Move back anyway") };
  }
  if (conflict === "kept") {
    return { text: tn("The page changed since, or was deleted, and was left as it is.",
      "These {n} pages changed since, or were deleted, and were left as they are.", preview?.kept || 1) };
  }
  return { text: status === 404 ? t("The note was deleted since.") : detail };
}

const labelChange = (a) => [...(a.added || []).map((n) => `+${n}`), ...(a.removed || []).map((n) => `−${n}`)].join(", ");
const old = (text) => <s className="chatChangeOld">{text}</s>;
const bold = (text) => <strong>{text}</strong>;

// A change to the library, one page's: `page(id, label)` is how a page is
// named (a link in the reply, plain text in the dialog).
function libraryText(a, page) {
  switch (a.tool) {
    case "rename_folder": return t("Folder {from} renamed to {to}", { from: old(a.from), to: bold(a.to) });
    case "rename_label": return t("Label {from} renamed to {to}", { from: old(a.from), to: bold(a.to) });
    case "create_label": return t("Made the label {label}", { label: bold(a.to) });
    case "create_page": return a.inside
      ? t("Made the page {page} inside {parent}", { page: page(a.page_id, a.title), parent: bold(a.inside) })
      : t("Made the page {page} in {folder}", { page: page(a.page_id, a.title), folder: bold(a.to || t("the library root")) });
    case "label_page": return t("Labels of {page}: {change}", { page: page(a.page_id, a.title), change: labelChange(a) });
    case "delete_folder": return a.count
      ? tn("Deleted the folder {folder}; its {n} page stays in your library",
        "Deleted the folder {folder}; its {n} pages stay in your library", a.count, { folder: bold(a.title) })
      : t("Deleted the folder {folder}", { folder: bold(a.title) });
    case "delete_label": return a.count
      ? tn("Deleted the label {label} from {n} page", "Deleted the label {label} from {n} pages", a.count, { label: bold(a.title) })
      : t("Deleted the label {label}", { label: bold(a.title) });
    // No link: the page is in Recently deleted.
    case "delete_page": return a.inside
      ? tn("Moved {page} to Recently deleted, with {n} page inside", "Moved {page} to Recently deleted, with {n} pages inside",
        a.inside, { page: bold(a.title) })
      : t("Moved {page} to Recently deleted", { page: bold(a.title) });
    default: break;
  }
  if (a.kind === "rename" && a.to) {
    return <><s className="chatChangeOld">{a.from}</s> → {page(a.page_id, a.to)}</>;
  }
  if (a.kind === "move" && a.title) {
    return <span title={a.from ? t("Was in: {folders}", { folders: a.from }) : undefined}>
      {t("{page} moved to {folder}", { page: page(a.page_id, a.title), folder: <strong>{a.to || t("the library root")}</strong> })}
    </span>;
  }
  if ((a.kind === "save" || a.kind === "restore") && a.title) {
    const args = { page: page(a.page_id, a.title), folder: <strong>{a.to || t("the library root")}</strong> };
    return a.kind === "restore" ? t("Restored {page} to {folder}", args)
      : a.existed ? t("{page}, already in your library, filed in {folder}", args)
        : t("Saved {page} to {folder}", args);
  }
  return page(a.page_id, a.summary); // saved before the structured fields
}

// A call that changed several pages: one sentence, its pages folded under it.
function batchText(a) {
  const n = a.items.length;
  switch (a.tool) {
    case "rename_page": return tn("Renamed {n} page", "Renamed {n} pages", n);
    case "move_page": return tn("Moved {n} page to {folder}", "Moved {n} pages to {folder}", n,
      { folder: <strong>{a.to || t("the library root")}</strong> });
    case "label_page": return tn("Changed the labels of {n} page: {change}", "Changed the labels of {n} pages: {change}", n,
      { change: labelChange(a) });
    case "restore_page": return tn("Restored {n} page", "Restored {n} pages", n);
    case "delete_page": return tn("Moved {n} page to Recently deleted", "Moved {n} pages to Recently deleted", n);
    default: return a.summary;
  }
}

// The sentence a change reads as. `links.page(id, label)` and
// `links.block(action, label)` name a page and a note: links under the reply,
// plain text in the dialog, where a link inside a checkbox's label would
// tick it.
export function changeText(a, { page, block }) {
  if (changePlace(a) === "notes") return a.title ? noteChangeText(a, block(a, `“${a.title}”`)) : block(a, a.summary);
  return a.items?.length ? batchText(a) : libraryText(a, page);
}

// The card a reply's changes are listed in (`.chatChanges`, behind the
// accent rule): its head, `tail` at the head's end, then a `.chatChange`
// row each. A computer agent's turn lists its changed files in it too
// (FileChanges).
export function ChangesCard({ head, tail = null, guide, className = "", children }) {
  return (
    <div className={`chatChanges${className ? ` ${className}` : ""}`} data-guide={guide}>
      <div className="chatChangesHead"><span>{head}</span>{tail}</div>
      {children}
    </div>
  );
}

// What a computer agent's turn changed (chat/computer/computerChat.js
// turnChanges: {shown, rest, more, n, built, vault}), under its reply: a
// row a file, each its path as the link that opens it while the agent works
// in a synced folder (`vault`: PathLink, resolved from the folder's top),
// else as plain monospace text, since no page holds a file of a directory
// Gamma does not sync; the first few and then "and N more" unfolding to the
// rest, and "The PDF was rebuilt" last.
export function FileChanges({ files }) {
  const { shown, rest, more, n, built, vault } = files;
  const file = (path) => (vault ? <PathLink href={path}>{path}</PathLink> : <code>{path}</code>);
  const row = (key, icon, body) => (
    <div key={key} className="chatChange">{icon}<span className="chatChangeText">{body}</span></div>
  );
  const edited = <PencilIcon size={14} />;
  const head = vault ? (n ? t("Changed in the synced folder · {n}", { n }) : t("Changed in the synced folder"))
    : n ? t("Changed in its directory · {n}", { n }) : t("Changed in its directory");
  return (
    <ChangesCard className="chatFileChanges" head={head}>
      {shown.length ? shown.map((path) => row(path, edited, file(path)))
        : n ? row("n", edited, tn("Changed {n} file", "Changed {n} files", n)) : null}
      {shown.length && more > 0 ? row("more", edited, rest.length ? (
        <details className="chatChangeItems">
          <summary>{t("and {n} more", { n: more })}</summary>
          {rest.map((path) => <div key={path}>{file(path)}</div>)}
        </details>
      ) : t("and {n} more", { n: more })) : null}
      {built ? row("built", <FileTextIcon size={14} />, t("The PDF was rebuilt")) : null}
    </ChangesCard>
  );
}

// The comment threads a computer agent opened in its turn
// (chat/computer/computerChat.js shownMessages' `threads`: [{id, page_id,
// block_id, text}]), under its reply: a row a thread, its first comment's
// words the link that opens it there (GammaNavContext's openThread), as an
// agent asks its question on the passage it is about.
export function ThreadsOpened({ threads }) {
  const nav = useContext(GammaNavContext);
  return (
    <ChangesCard className="chatThreadsOpened" head={t("Comments it opened · {n}", { n: threads.length })}>
      {threads.map((th) => (
        <div key={th.id} className="chatChange">
          <MessageSquareIcon size={14} />
          <span className="chatChangeText">
            <button type="button" className="chatChangeLink" data-thread-link={th.id}
              onClick={() => nav?.openThread?.(th.id, th.block_id, th.page_id)}>{th.text || t("A comment thread")}</button>
          </span>
        </div>
      ))}
    </ChangesCard>
  );
}

// Keep All in a reply's head: keeps those of `ids` that wait for review,
// shown while one does.
function KeepAll({ ids, disabled }) {
  const states = useSyncExternalStore(noteReview.subscribe, () => noteReview.getSnapshot().states);
  for (const id of ids) if (!states.has(id)) noteReview.want(id);
  const open = ids.filter((id) => states.get(id) === CHANGE_STATES.OPEN);
  if (!open.length) return null;
  return (
    <button type="button" className="uiBtn sm ghost chatChangesAll" disabled={disabled} onClick={() => noteReview.keep(open)}
      title={t("Keep this reply's changes that wait for review")}>
      <CheckIcon size={14} />{t("Keep All")}
    </button>
  );
}

// One change's row: its sentence, what was decided, and its buttons. A
// change the journal holds (`change_id`, its state known) goes by that
// state; another by the mark its conversation saved.
function ChangeRow({ a, text, row, working, busy, canAct, onUndo, onKeep, children }) {
  const state = useChangeState(a.change_id);
  const journaled = !!a.change_id && !!state;
  const undone = journaled ? state === CHANGE_STATES.UNDONE : !!a.reverted;
  const open = journaled && state === CHANGE_STATES.OPEN;
  const Icon = ACTION_ICONS[a.kind] || PencilIcon;
  const label = undone ? t("Redo this change") : t("Undo this change");
  const ButtonIcon = undone ? RedoIcon : UndoIcon;
  const held = working || busy || !!row?.refusal;
  return (
    <>
      <div className={`chatChange${undone ? " reverted" : ""}`}>
        <Icon size={14} />
        <span className="chatChangeText">{text}</span>
        {undone ? <span className="chatChangeTag">{t("Undone")}</span>
          : journaled && state === CHANGE_STATES.KEPT ? <span className="chatChangeTag">{t("Kept")}</span> : null}
        {canAct && open ? (
          <button type="button" className="ctlBtn chatChangeRevert" disabled={held} title={t("Keep this change")}
            aria-label={t("Keep this change")} onClick={onKeep}>
            <CheckIcon size={14} />
          </button>
        ) : null}
        {canAct && (undone ? canRedo({ ...a, reverted: true }) : canRevert({ ...a, reverted: false })) ? (
          <button type="button" className="ctlBtn chatChangeRevert" disabled={held}
            title={label} aria-label={label} onClick={() => onUndo(undone)}>
            {row?.working ? <span className="transferSpin inline" aria-hidden="true" /> : <ButtonIcon size={14} />}
          </button>
        ) : null}
      </div>
      {children}
    </>
  );
}

// `onReverted(indexes, answer, reverted)`: the actions at those indexes were
// undone (`reverted` false: redone) — the dock marks them in the saved
// conversation. Without it (a viewer, the share view) nothing can be
// undone; `busy` (a reply streaming here) holds the buttons.
// `onRevertMany`: open the dialog on this reply's changes (none: the
// conversation has fewer than two to take back).
export default function AgentChanges({ actions, onOpenPage, onReverted, onRevertMany, busy = false }) {
  const nav = useContext(GammaNavContext);
  // index → {working} while its request runs, or {refusal, redo} once it stopped
  const [rows, setRows] = useState({});
  const changes = actions.map((a, i) => ({ a, i })).filter(({ a }) => isChange(a));
  const library = changes.filter(({ a }) => changePlace(a) === "library");
  const notes = changes.filter(({ a }) => changePlace(a) === "notes");
  if (!library.length && !notes.length) return null;
  const setRow = (i, state) => setRows((prev) => {
    const next = { ...prev };
    if (state) next[i] = state; else delete next[i];
    return next;
  });
  const working = Object.values(rows).some((r) => r.working);
  const revertOne = async (i, { force = false, redo = false } = {}) => {
    setRow(i, { working: true });
    try {
      const answer = await sendRevert(actions[i], { force, redo });
      setRow(i, answer?.kept ? { kept: answer.kept } : null);
      onReverted([i], answer, !redo);
    } catch (err) {
      setRow(i, { refusal: revertRefusal(err), redo });
    }
  };

  const pageLink = (id, label) => id
    ? <button type="button" className="chatChangeLink" onClick={() => onOpenPage?.(id)}>{label}</button>
    : <span>{label}</span>;
  // A note change's link opens its note with the change unfolded there,
  // as its mark unfolds it (chat/noteReview.js reveal); one the journal no
  // longer holds is drawn from this row's record and undone as the row does.
  const blockLink = (a, label) => a.block_id && nav?.openBlock
    ? <button type="button" className="chatChangeLink" onClick={() => {
      const i = actions.indexOf(a);
      noteReview.reveal(a, onReverted && canRevert(a) ? {
        undo: async ({ force }) => onReverted([i], await sendRevert(a, { force }), true),
      } : {});
      nav.openBlock(a.block_id, a.page_id);
    }}>{label}</button>
    : pageLink(a.page_id, label);
  const links = { page: pageLink, block: blockLink };
  const many = onReverted && onRevertMany && changes.some(({ a }) => canRevert(a));

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
  // A revert or redo that left some pages as they are: changed since, or gone.
  const keptRow = (i) => (
    <div className="chatRevertAsk" role="status">
      <div>{tn("{n} of these pages changed since, or was deleted, and was left as it is.",
        "{n} of these pages changed since, or were deleted, and were left as they are.", rows[i].kept)}</div>
      <div className="chatApprovalActions">
        <button type="button" className="uiBtn sm ghost" onClick={() => setRow(i, null)}>{t("Dismiss")}</button>
      </div>
    </div>
  );
  const keepOne = async (i) => {
    setRow(i, { working: true });
    try {
      await noteReview.keep([actions[i].change_id]);
      setRow(i, null);
    } catch (err) {
      setRow(i, { refusal: revertRefusal(err), redo: false });
    }
  };
  const changeRow = ({ a, i }) => (
    <ChangeRow key={i} a={a} row={rows[i]} working={working} busy={busy} canAct={!!onReverted}
      onUndo={(redo) => revertOne(i, { redo })} onKeep={() => keepOne(i)}
      text={<>
        {changeText(a, links)}
        {a.items?.length ? (
          <details className="chatChangeItems">
            <summary>{t("Show the pages")}</summary>
            {a.items.map((item, k) => <div key={k}>{libraryText({ ...item, tool: a.tool, kind: a.kind }, pageLink)}</div>)}
          </details>
        ) : null}
      </>}>
      {rows[i]?.refusal ? refusalRow(i) : rows[i]?.kept ? keptRow(i) : null}
    </ChangeRow>
  );
  // every journaled change of the reply: Keep All keeps those that wait for review
  const changeIds = changes.map(({ a }) => a.change_id).filter(Boolean);
  const group = (list, place, first) => list.length ? (
    <ChangesCard key={place} guide="chat.changes"
      head={place === "library"
        ? t("Changed in your library · {n}", { n: list.reduce((n, { a }) => n + changeCount(a), 0) })
        : t("Changed in your notes · {n}", { n: list.length })}
      tail={first ? <>
        {onReverted && changeIds.length ? <KeepAll ids={changeIds} disabled={working || busy} /> : null}
        {many ? (
          <button type="button" className="uiBtn sm ghost chatChangesAll" disabled={working || busy} onClick={onRevertMany}
            title={t("Choose changes of this conversation to undo together")}>
            <UndoIcon size={14} />{t("Undo…")}
          </button>
        ) : null}
      </> : null}>
      {list.map(changeRow)}
    </ChangesCard>
  ) : null;
  return <>{group(library, "library", true)}{group(notes, "notes", !library.length)}</>;
}
