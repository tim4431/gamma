// The feedback view (docs/dev/feedback.md "In the app"): a work folder's
// open comment threads, as the reader lists them (GET /api/feedback), to
// read or to send to an agent. One dialog holds both the view and Send:
// a scope switch between the file or directory it was opened on and the
// whole work folder; the entries grouped by file, each with its kind, its
// heading (feedbackText.js), the picture of its region, where it was found
// again and its source line, its first comment and the chip of a Send
// waiting on it, a click opening the thread on its page; the line of
// instruction and Send, which posts a request with an id this tab minted,
// so a retry writes it once, naming the folder's agent on this computer,
// or on the computer that keeps it, when one answers the person's Sends
// (the document's `agent`,
// docs/dev/local_agents.md), with its lines under it; and the work folder's
// requests below, the newest first, with what became of each and Withdraw
// on the person's own.
// It reads the requests and the entries again every few seconds while it
// is open, so an agent's answers show as they come. Loaded on first use
// (feedback/FeedbackHost.jsx); its state is its own, and the pure part
// feedbackView.js.
import React, { useEffect, useId, useRef, useState } from "react";
import { t } from "../shared/i18n/i18n.js";
import { API, apiJson, makeId, withWorkspace } from "../shared/lib/utils";
import { FormNotice, Segmented, SubDialog } from "../settings/SettingsKit";
import { FileTextIcon, HighlightIcon, MessageSquareIcon, PenIcon, RectSelectIcon } from "../shared/ui/Icons";
import { PersonAvatar } from "../collaboration/Presence";
import { StateTag } from "../shared/ui/StateTag";
import { anchorSnippet, sentChip } from "../comments/comments.js";
import { entryHeading, placedText, requestLine, sendWords, sourceText, withdrawTitle } from "./feedbackText.js";
import {
  NOTE_MAX, POLL_MS, canWithdraw, firstComment, groupByFile, hasChoice, namesAgent, newestRequests, othersCount, pictureOf, readScope,
  sendAgent, sendBody,
} from "./feedbackView.js";
import "./feedback.css";

const KIND_ICONS = { passage: HighlightIcon, area: RectSelectIcon, ink: PenIcon, note: MessageSquareIcon, file: FileTextIcon };
const JSON_POST = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// One entry's row: a click opens its thread on its page. The picture is an
// <img> request, which carries no workspace header, so its URL names the
// workspace (withWorkspace, as every API picture drawn by the browser).
// `answers`: the agent that takes the reader's Sends, which the chip of
// one waiting names, or the computer it runs on (sendAgent's plan; null
// for none).
function Entry({ entry, answers, onOpen }) {
  const Icon = KIND_ICONS[entry.kind] || MessageSquareIcon;
  const chip = sentChip(entry, answers);
  const first = firstComment(entry);
  const picture = pictureOf(entry);
  const meta = [placedText(entry.where, entry.kind), sourceText(entry.source)].filter(Boolean).join(" · ");
  return (
    <button type="button" className="feedbackEntry" data-thread-id={entry.id} onClick={() => onOpen(entry)}>
      <span className="feedbackEntryIcon"><Icon size={16} /></span>
      <span className="feedbackEntryMain">
        <span className="feedbackEntryHead">
          <span className="feedbackEntryTitle">{entryHeading(entry)}</span>
          {chip ? <StateTag state={chip} data-sent={chip.kind} /> : null}
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
// of it, the entries' tag, with the agent's own words on a line under the
// note (its progress, summary or reason, at most three lines, whole in its
// title), and Withdraw on the person's own while it waits or runs; one of
// theirs waiting names the agent that runs them or its computer
// (`answers`, as the entries').
function RequestRow({ request, me, answers, busy, onWithdraw }) {
  const sent = requestLine(request, Date.now(), request.by === me ? answers : null);
  const { line, detail, kind } = sent;
  const withdraws = canWithdraw(request, me);
  // what Withdraw does to a run of this computer's agent, said on a line (a finger has no hover)
  const stops = withdraws ? withdrawTitle(request) : "";
  return (
    <li className="feedbackRequest" data-request-id={request.id} data-state={kind}>
      <span className="feedbackRequestLine">
        <span>{line}</span>
        {request.note ? <span className="feedbackRequestNote" title={request.note}>{anchorSnippet(request.note, 120)}</span> : null}
        {detail ? <span className="feedbackRequestDetail" title={detail}>{detail}</span> : null}
        {stops ? <span className="feedbackRequestHint">{stops}</span> : null}
      </span>
      <StateTag state={sent} className="feedbackRequestState" />
      {withdraws ? (
        <button type="button" className="uiBtn sm" disabled={busy} title={stops || undefined}
          onClick={() => onWithdraw(request)}>{t("Withdraw")}</button>
      ) : null}
    </li>
  );
}

// `scope`: what it was opened on ({kind, id}); `me`: the signed-in username
// (whose requests get Withdraw); `canSend`: the person may edit, so Send
// shows; `links`: the desktop app's work-folder vaults by folder id
// (vault/useVault.js), whose `paused` the notice reads and whose presence
// says the folder is kept here (where an agent can be turned on); onOpenThread(threadId,
// blockId, pageId) opens a thread on its page (App's openThreadAt).
export default function FeedbackDialog({ scope, me, canSend, links, onOpenThread, onClose }) {
  const [choice, setChoice] = useState("scope");
  // {target, doc, error}: the document of `target` read last, and the
  // error of the read since that failed, which keeps the document shown.
  const [read, setRead] = useState(null);
  const [requests, setRequests] = useState([]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  // The Send's id, minted at the first attempt and kept until it is stored.
  const requestId = useRef("");
  // The agent's lines under Send are a live region that says why the button
  // is off, so a screen reader hears a change a poll brings.
  const notesId = useId();
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
        if (live) setRead({ target, doc: next });
      } catch (err) {
        // A poll that fails says so over the entries, which stay, as the
        // instruction being typed does; another scope's are not shown.
        const error = err.message || String(err);
        if (live) setRead((r) => r?.target === target ? { ...r, error } : { target, error });
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
      setRead({ target, doc: await apiJson(`${API}/feedback?scope=${encodeURIComponent(target)}`) });
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
  // Who answers the Send: the folder's agent on this computer or on the
  // computer that keeps the folder, as the document says, or an agent the
  // person runs (the generic button).
  const plan = sendAgent(doc, !!(w && links?.get(w)));
  const words = sendWords(plan, n, othersCount(entries, me));
  // What a Send of the person's waits for: the folder's agent, or the computer that keeps the folder.
  const answers = namesAgent(plan) ? plan : null;
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
      {read?.error ? <FormNotice>{read.error}</FormNotice> : null}
      {!read ? <p className="reportModalHint loadingHint">{t("Reading…")}</p> : null}
      {doc ? (
        <div className="feedbackEntries" data-count={n}>
          {n ? groupByFile(entries).map((group) => (
            <section key={group.file} className="feedbackFile">
              <h3 className="feedbackFileName">{group.file}</h3>
              {group.entries.map((entry) => <Entry key={entry.id} entry={entry} answers={answers} onOpen={open} />)}
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
          <button type="button" className="uiBtn primary" disabled={!n || busy || words.blocked} onClick={send}
            aria-describedby={words.notes.length ? notesId : undefined}>
            {words.button}
          </button>
        </div>
      ) : null}
      {doc && canSend ? (
        <div className="feedbackAgent" id={notesId} role="status">
          {words.notes.map((line) => (
            <p key={line.text} className={`feedbackAgentNote${line.tone === "warn" ? " agentProblem" : ""}`} data-tone={line.tone}>{line.text}</p>
          ))}
        </div>
      ) : null}
      {problem ? <FormNotice>{problem}</FormNotice> : null}
      {requests.length ? (
        <div className="feedbackRequests">
          <h3 className="feedbackFileName">{t("Sent to the agent")}</h3>
          <ul>{newestRequests(requests).map((request) => (
            <RequestRow key={request.id} request={request} me={me} answers={answers} busy={busy} onWithdraw={withdraw} />
          ))}</ul>
        </div>
      ) : null}
    </SubDialog>
  );
}
