// The feedback view (docs/dev/feedback.md "In the app"): a work folder's
// open comment threads, as the reader lists them (GET /api/feedback), to
// read or to send to an agent. One dialog holds both the view and Send:
// a scope switch between the file or directory it was opened on and the
// whole work folder; the entries grouped by file, each with its kind, its
// heading (feedbackText.js), the picture of its region, where it was found
// again and its source line, its first comment and the chip of a Send
// waiting on it, a click opening the thread on its page; the line of
// instruction and Send, which posts a request with an id this tab minted,
// so a retry writes it once; and the work folder's requests below, the
// newest first, with what became of each and Withdraw on the person's own.
// It reads the requests and the entries again every few seconds while it
// is open, so an agent's answers show as they come. Loaded on first use
// (feedback/FeedbackHost.jsx); its state is its own, and the pure part
// feedbackView.js.
import React, { useEffect, useRef, useState } from "react";
import { t, tn } from "../shared/i18n/i18n.js";
import { API, apiJson, makeId, withWorkspace } from "../shared/lib/utils";
import { Segmented, SubDialog } from "../settings/SettingsKit";
import { FileTextIcon, HighlightIcon, MessageSquareIcon, PenIcon, RectSelectIcon } from "../shared/ui/Icons";
import { PersonAvatar } from "../collaboration/Presence";
import { anchorSnippet, sentChip } from "../comments/comments.js";
import { entryHeading, placedText, requestLine, sourceText } from "./feedbackText.js";
import {
  NOTE_MAX, POLL_MS, canWithdraw, firstComment, groupByFile, hasChoice, newestRequests, pictureOf, readScope, sendBody,
} from "./feedbackView.js";
import "./feedback.css";

const KIND_ICONS = { passage: HighlightIcon, area: RectSelectIcon, ink: PenIcon, note: MessageSquareIcon, file: FileTextIcon };
const JSON_POST = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// One entry's row: a click opens its thread on its page. The picture is an
// <img> request, which carries no workspace header, so its URL names the
// workspace (withWorkspace, as every API picture drawn by the browser).
function Entry({ entry, onOpen }) {
  const Icon = KIND_ICONS[entry.kind] || MessageSquareIcon;
  const chip = sentChip(entry);
  const first = firstComment(entry);
  const picture = pictureOf(entry);
  const meta = [placedText(entry.where, entry.kind), sourceText(entry.source)].filter(Boolean).join(" · ");
  return (
    <button type="button" className="feedbackEntry" data-thread-id={entry.id} onClick={() => onOpen(entry)}>
      <span className="feedbackEntryIcon"><Icon size={16} /></span>
      <span className="feedbackEntryMain">
        <span className="feedbackEntryHead">
          <span className="feedbackEntryTitle">{entryHeading(entry)}</span>
          {chip ? <span className={`uiTag ${chip.tone}`} data-sent={chip.state} title={chip.title}>{chip.label}</span> : null}
        </span>
        {meta ? <span className="feedbackEntryMeta">{meta}</span> : null}
        <span className="feedbackEntryFirst">
          <PersonAvatar name={first.who || "?"} title={first.who} />
          <span className="feedbackEntryText">
            <span className="commentAuthor">{first.who || t("Someone")}</span>{" "}
            {first.deleted ? <em>{t("Comment deleted")}</em> : anchorSnippet(first.body, 200)}
          </span>
        </span>
      </span>
      {picture ? <img className="feedbackEntryPicture" loading="lazy" src={withWorkspace(picture)} alt="" /> : null}
    </button>
  );
}

// One request's row: when it was sent and how many comments, what became
// of it, and Withdraw on the person's own while it waits or runs.
function RequestRow({ request, me, busy, onWithdraw }) {
  const { line, state, kind } = requestLine(request);
  return (
    <li className="feedbackRequest" data-request-id={request.id} data-state={kind}>
      <span className="feedbackRequestLine">
        <span>{line}</span>
        {request.note ? <span className="feedbackRequestNote" title={request.note}>{anchorSnippet(request.note, 120)}</span> : null}
      </span>
      <span className="feedbackRequestState">{state}</span>
      {canWithdraw(request, me) ? (
        <button type="button" className="uiBtn sm" disabled={busy} onClick={() => onWithdraw(request)}>{t("Withdraw")}</button>
      ) : null}
    </li>
  );
}

// `scope`: what it was opened on ({kind, id}); `me`: the signed-in username
// (whose requests get Withdraw); `canSend`: the person may edit, so Send
// shows; `links`: the desktop app's work-folder vaults by folder id
// (vault/useVault.js), whose `paused` the notice reads; onOpenThread(threadId,
// blockId, pageId) opens a thread on its page (App's openThreadAt).
export default function FeedbackDialog({ scope, me, canSend, links, onOpenThread, onClose }) {
  const [choice, setChoice] = useState("scope");
  const [read, setRead] = useState(null); // {doc} | {error}
  const [requests, setRequests] = useState([]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  // The Send's id, minted at the first attempt and kept until it is stored.
  const requestId = useRef("");
  const doc = read?.doc || null;
  const workFolder = doc?.work_folder || null;
  const target = readScope(scope, choice, workFolder);
  // The work folder, as the document names it (the scope opened on may be
  // a directory of it); its requests and its paused state wait for that.
  const w = workFolder?.id || "";

  // The document of the scope read, and the work folder's requests, read
  // as the scope changes and then every POLL_MS while the dialog is open.
  useEffect(() => {
    let live = true;
    const load = async () => {
      try {
        const next = await apiJson(`${API}/feedback?scope=${encodeURIComponent(target)}`);
        if (live) setRead({ doc: next });
      } catch (err) {
        if (live) setRead({ error: err.message || String(err) });
      }
      if (!w) return;
      try {
        const listed = await apiJson(`${API}/feedback/requests?scope=${encodeURIComponent(w)}&limit=100`);
        if (live) setRequests(listed.requests || []);
      } catch {}
    };
    load();
    const timer = setInterval(load, POLL_MS);
    return () => { live = false; clearInterval(timer); };
  }, [target, w]);

  const reload = async () => {
    try {
      setRead({ doc: await apiJson(`${API}/feedback?scope=${encodeURIComponent(target)}`) });
      if (w) setRequests((await apiJson(`${API}/feedback/requests?scope=${encodeURIComponent(w)}&limit=100`)).requests || []);
    } catch {}
  };
  const send = async () => {
    if (!requestId.current) requestId.current = makeId();
    setBusy(true); setProblem("");
    try {
      await apiJson(`${API}/feedback/requests`, JSON_POST(sendBody(requestId.current, target, note)));
      requestId.current = "";
      setNote("");
      await reload();
    } catch (err) {
      setProblem(err.message || String(err));
    } finally { setBusy(false); }
  };
  const withdraw = async (request) => {
    setBusy(true); setProblem("");
    try {
      await apiJson(`${API}/feedback/requests/${encodeURIComponent(request.id)}/state`, JSON_POST({ state: "failed", detail: "withdrawn" }));
      await reload();
    } catch (err) {
      setProblem(err.message || String(err));
    } finally { setBusy(false); }
  };
  const open = (entry) => {
    onClose();
    onOpenThread(entry.id, entry.block_id, entry.page_id);
  };

  const entries = doc?.entries || [];
  const n = entries.length;
  const paused = !!(w && links?.get(w)?.paused);
  const choices = hasChoice(scope, workFolder) ? [
    ["scope", scope.kind === "file" ? t("This file") : t("This folder")],
    ["work_folder", workFolder.name || t("Work folder")],
  ] : null;
  return (
    <SubDialog title={t("Feedback")} onClose={onClose} className="feedbackDialog">
      {choices ? (
        <div className="feedbackScope" role="group" aria-label={t("Feedback scope")}>
          <Segmented value={choice} onChange={setChoice} options={choices} />
        </div>
      ) : null}
      {paused ? <p className="feedbackNotice" role="status">{t("This work folder is paused: its feedback files wait until it syncs again.")}</p> : null}
      {read?.error ? <p className="importWarning" role="alert">{read.error}</p> : null}
      {!read ? <p className="reportModalHint">{t("Reading…")}</p> : null}
      {doc ? (
        <div className="feedbackEntries" data-count={n}>
          {n ? groupByFile(entries).map((group) => (
            <section key={group.file} className="feedbackFile">
              <h3 className="feedbackFileName">{group.file}</h3>
              {group.entries.map((entry) => <Entry key={entry.id} entry={entry} onOpen={open} />)}
            </section>
          )) : <p className="feedbackEmpty">{t("No open comments here.")}</p>}
          {doc.truncated ? <p className="reportModalHint">{t("{n} more are not shown.", { n: doc.truncated })}</p> : null}
        </div>
      ) : null}
      {doc && canSend ? (
        <div className="feedbackSend">
          <textarea className="feedbackNote" rows={2} maxLength={NOTE_MAX} value={note} disabled={busy}
            aria-label={t("Instruction for the agent (optional)")} placeholder={t("Instruction for the agent (optional)")}
            onChange={(e) => setNote(e.target.value)} />
          <button type="button" className="uiBtn primary" disabled={!n || busy} onClick={send}>
            {tn("Send {n} comment", "Send {n} comments", n)}
          </button>
        </div>
      ) : null}
      {problem ? <p className="importWarning" role="alert">{problem}</p> : null}
      {requests.length ? (
        <div className="feedbackRequests">
          <h3 className="feedbackFileName">{t("Sent to the agent")}</h3>
          <ul>{newestRequests(requests).map((request) => (
            <RequestRow key={request.id} request={request} me={me} busy={busy} onWithdraw={withdraw} />
          ))}</ul>
        </div>
      ) : null}
    </SubDialog>
  );
}
