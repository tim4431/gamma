// The card under a reply whose fetch_paper was stopped by a CAPTCHA, a
// sign-in page or a paywall (chat/fetchHandoff.js has the rules). "Open"
// goes through /api/ai/handoffs/<id>/go, the address Gamma Connector knows
// the tab by: it sends the PDF back from that tab once the user is through.
// The card asks the Connector (its bridge.js) whether it can, and says why
// not when it cannot; then the user drops the downloaded PDF on the card.
// The card follows the request on the server, and when the PDF has arrived
// the chat continues by itself (or offers Continue when it can't).
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API, apiJson, isPdfFile } from "../shared/lib/utils";
import { xhrUpload } from "../shared/lib/xhrUpload.js";
import { CheckIcon, ExternalLinkIcon, ShieldIcon, UploadIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";
import {
  SETTLED, connectorNote, continuePrompt, handoffHint, handoffState, pollDelay, replyHandoffs, shouldContinue,
  wallHeadline, watchNote,
} from "./fetchHandoff.js";

const requestUrl = (id) => `${API}/ai/handoffs/${encodeURIComponent(id)}`;
// The desktop app opens links in the system browser, where a Connector may be.
const DESKTOP = /\bElectron\//.test(navigator.userAgent);

// Gamma Connector's word on request `id` (connectorNote), asked through
// window messages its content script answers. The script loads after the
// page, so the question is asked again before no answer counts as "missing";
// `round` asks afresh (the page opened again, the window came back into
// focus after signing the Connector in).
function useConnector(id, active, round) {
  const [status, setStatus] = useState(DESKTOP ? "desktop" : "unknown");
  useEffect(() => {
    if (DESKTOP || !active) return undefined;
    let answered = false;
    const onMessage = (e) => {
      const d = e.data;
      if (e.source !== window || e.origin !== window.location.origin || d?.source !== "gamma-connector"
          || d.type !== "connector-status" || d.id !== id) return;
      answered = true;
      setStatus(d.status);
    };
    window.addEventListener("message", onMessage);
    const ask = () => { if (!answered) window.postMessage({ source: "gamma-app", type: "connector-probe", id }, window.location.origin); };
    const timers = [0, 1500, 3000].map((ms) => setTimeout(ask, ms));
    timers.push(setTimeout(() => { if (!answered) setStatus("missing"); }, 4500));
    return () => { window.removeEventListener("message", onMessage); timers.forEach(clearTimeout); };
  }, [id, active, round]);
  return status;
}

function HandoffCard({ handoff, isLast, readOnly, onState }) {
  const [view, setView] = useState(null);
  const [opened, setOpened] = useState(false);
  const [working, setWorking] = useState(""); // "upload" | "dismiss"
  const [error, setError] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef(null);
  const [round, setRound] = useState(0);
  const state = handoffState(view, { opened });
  const connector = useConnector(handoff.id, state === "waiting" || state === "opened", round);

  const refresh = useCallback(async () => {
    try {
      setView(await apiJson(requestUrl(handoff.id)));
    } catch (err) {
      // A restart or the request's age forgets it; another account never had it.
      if ([401, 403, 404].includes(err.status) || /^401/.test(err.message)) setView({ status: "gone" });
    }
  }, [handoff.id]);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    if (SETTLED.has(state)) return undefined;
    const delay = pollDelay(state, { isLast });
    const timer = delay ? setInterval(() => { if (document.visibilityState === "visible") refresh(); }, delay) : null;
    const onFocus = () => { refresh(); setRound((n) => n + 1); };
    window.addEventListener("focus", onFocus);
    return () => { if (timer) clearInterval(timer); window.removeEventListener("focus", onFocus); };
  }, [state, isLast, refresh]);
  useEffect(() => { onState(handoff.id, state); }, [handoff.id, state, onState]);

  function open() {
    setOpened(true);
    setRound((n) => n + 1);
    window.open(`${requestUrl(handoff.id)}/go`, "_blank", "noopener");
  }

  async function upload(file) {
    if (!file) return;
    if (!isPdfFile(file)) { setError(t("That isn't a PDF — download the paper's PDF and drop it here.")); return; }
    setWorking("upload");
    setError("");
    const form = new FormData();
    form.append("file", file, file.name || "paper.pdf");
    try {
      setView(await xhrUpload(`${requestUrl(handoff.id)}/pdf`, form));
    } catch (err) {
      if (err.status === 404) setView({ status: "gone" });
      else setError(err.message || t("Upload failed"));
    } finally {
      setWorking("");
    }
  }

  async function dismiss() {
    setWorking("dismiss");
    try { setView(await apiJson(requestUrl(handoff.id), { method: "DELETE" })); }
    catch { setView({ status: "gone" }); }
    finally { setWorking(""); }
  }

  const host = view?.host || handoff.host || "";
  const settled = SETTLED.has(state);
  const drop = settled || readOnly ? {} : {
    onDragOver: (e) => {
      if (!Array.from(e.dataTransfer?.types || []).includes("Files")) return;
      e.preventDefault(); e.stopPropagation(); setDragOver(true);
    },
    onDragLeave: (e) => { if (!e.currentTarget.contains(e.relatedTarget)) setDragOver(false); },
    onDrop: (e) => {
      if (!e.dataTransfer?.files?.length) return;
      // The card takes the file; the page underneath must not import it too.
      e.preventDefault(); e.stopPropagation(); setDragOver(false);
      upload(Array.from(e.dataTransfer.files).find(isPdfFile) || e.dataTransfer.files[0]);
    },
  };

  return (
    <div className={`chatHandoff ${state}${dragOver ? " dragOver" : ""}`} role="group"
      aria-label={t("Get this paper in your browser")} data-handoff={handoff.id} {...drop}>
      <div className="chatHandoffHead">
        {state === "done" ? <CheckIcon size={16} /> : <ShieldIcon size={16} />}
        <span>{state === "done" ? t("The PDF arrived") : wallHeadline(view?.wall || handoff.wall, host)}</span>
        {state === "watching" ? <span className="transferSpin inline" aria-hidden="true" /> : null}
      </div>
      <div className="chatHandoffText" aria-live="polite">
        {working === "upload" ? t("Reading the PDF…") : handoffHint(state, { pages: view?.pages || 0 })}
        {(state === "waiting" || state === "opened") && connectorNote(connector, { origin: window.location.origin })
          ? <> {connectorNote(connector, { origin: window.location.origin })}</> : null}
        {state === "watching" && watchNote(view?.note) ? <> {watchNote(view.note)}</> : null}
      </div>
      {handoff.source && !settled ? <div className="chatHandoffSource" title={handoff.source}>{handoff.source}</div> : null}
      {error ? <div className="chatHandoffError" role="alert">{error}</div> : null}
      {!settled && !readOnly ? (
        <div className="chatHandoffActions">
          <button type="button" className={`uiBtn sm${state === "waiting" ? " primary" : ""}`} onClick={open}
            title={t("Open the page in a new tab to sign in or pass the check")}>
            <ExternalLinkIcon size={14} />{state === "waiting" ? t("Open {host}", { host }) : t("Open again")}
          </button>
          <button type="button" className={`uiBtn sm${state === "opened" ? " primary" : ""}`} disabled={!!working}
            onClick={() => fileRef.current?.click()} title={t("Choose the PDF you downloaded")}>
            <UploadIcon size={14} />{t("Upload PDF")}
          </button>
          <button type="button" className="uiBtn sm ghost" disabled={!!working} onClick={dismiss}>{t("Dismiss")}</button>
          <input ref={fileRef} type="file" accept=".pdf,application/pdf" hidden
            onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ""; }} />
        </div>
      ) : null}
    </div>
  );
}

// Every request a reply opened, and the continuation once they are settled.
// `busy`: the chat is answering; `draft`: the composer holds something of
// the user's — either keeps the chat from sending in their name.
export default function FetchHandoffCards({ actions, isLast, busy, draft, readOnly, onContinue }) {
  const handoffs = useMemo(() => replyHandoffs(actions), [actions]);
  const [states, setStates] = useState({});
  const sawWaiting = useRef(false);
  const sent = useRef(false);
  const [, setSentShown] = useState(false);
  const onState = useCallback((id, state) => {
    if (!SETTLED.has(state) && state !== "loading") sawWaiting.current = true;
    setStates((prev) => (prev[id] === state ? prev : { ...prev, [id]: state }));
  }, []);
  const list = handoffs.map((h) => states[h.id] || "loading");
  const arrived = handoffs.filter((h) => states[h.id] === "done");
  const send = useCallback((prompt) => {
    if (sent.current) return;
    sent.current = true;
    setSentShown(true);
    onContinue?.(prompt);
  }, [onContinue]);
  const auto = !readOnly && shouldContinue({
    states: list, sawWaiting: sawWaiting.current, isLast, idle: !busy && !draft });
  const prompt = auto ? continuePrompt(arrived) : "";

  useEffect(() => { if (prompt) send(prompt); }, [prompt, send]);

  if (!handoffs.length) return null;
  const ready = !sent.current && !readOnly && isLast && arrived.length > 0 && list.every((s) => SETTLED.has(s));
  return (
    <div className="chatHandoffs">
      {handoffs.map((h) => <HandoffCard key={h.id} handoff={h} isLast={isLast} readOnly={readOnly} onState={onState} />)}
      {ready && !auto ? (
        <button type="button" className="uiBtn sm primary chatHandoffContinue" disabled={busy}
          onClick={() => send(continuePrompt(arrived))}>
          {t("Continue with the PDF")}
        </button>
      ) : null}
    </div>
  );
}
