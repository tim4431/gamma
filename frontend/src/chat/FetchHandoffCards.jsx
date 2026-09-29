// The card under a reply whose fetch_paper was stopped by a CAPTCHA, a
// sign-in page or a paywall (chat/fetchHandoff.js has the rules). The card
// asks Gamma Connector (its bridge.js) whether it can fetch for the request
// and says why not when it cannot; "Open" then has the Connector open the
// publisher's page itself, which sends the PDF back from that tab once the
// user is through — or, with no Connector to take the tab, opens the page
// directly for the user to download the PDF and drop it on the card (the
// desktop app goes through /api/ai/handoffs/<id>/go, the address the system
// browser's Connector knows the tab by). With "Fetch blocked papers in the
// background" on, the card hands the request to the Connector by itself, to
// fetch out of sight; "Show the tab" brings that tab forward when it needs
// the user. The card follows the request on the server, and when the PDF has
// arrived the chat continues by itself (or offers Continue when it can't).
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API, apiJson, isPdfFile } from "../shared/lib/utils";
import { xhrUpload } from "../shared/lib/xhrUpload.js";
import { CheckIcon, ExternalLinkIcon, ShieldIcon, UploadIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";
import {
  NEEDS_YOU, SETTLED, autoOpens, connectorNote, continuePrompt, handoffHint, handoffState, openRoute, pollDelay,
  replyHandoffs, shouldContinue, wallHeadline, watchNote,
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

// Ask the Connector to open ("open", `background` for out of sight), show
// ("show") or let go of ("close") request `id`'s tab. Resolves to its
// answer's status ("opened", "queued", "shown", "none", "closed", or the
// request's status when it no longer waits), or "" when none came in time.
function askConnector(id, act, { background = false } = {}) {
  return new Promise((resolve) => {
    const done = (status) => { window.removeEventListener("message", onMessage); clearTimeout(timer); resolve(status); };
    const onMessage = (e) => {
      const d = e.data;
      if (e.source !== window || e.origin !== window.location.origin || d?.source !== "gamma-connector"
          || d.type !== "connector-tab" || d.id !== id || d.do !== act) return;
      done(String(d.status || ""));
    };
    const timer = setTimeout(() => done(""), 2000);
    window.addEventListener("message", onMessage);
    window.postMessage({ source: "gamma-app", type: "connector-tab", id, do: act, background }, window.location.origin);
  });
}

function HandoffCard({ handoff, isLast, readOnly, autoOpen, onState }) {
  const [view, setView] = useState(null);
  const [opened, setOpened] = useState(false);
  const [queued, setQueued] = useState(false);
  const [working, setWorking] = useState(""); // "upload" | "dismiss" | "open"
  const [error, setError] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef(null);
  const autoTried = useRef(false);
  const [round, setRound] = useState(0);
  const state = handoffState(view, { opened });
  const connector = useConnector(handoff.id, !SETTLED.has(state) && state !== "loading", round);

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

  // "Fetch blocked papers in the background": once per card, the Connector
  // takes the request without a click.
  useEffect(() => {
    if (autoTried.current || !autoOpens({ auto: autoOpen, connector, state, isLast, readOnly, note: view?.note })) return;
    autoTried.current = true;
    askConnector(handoff.id, "open", { background: true }).then((status) => {
      if (status !== "opened" && status !== "queued") return;
      setOpened(true);
      setQueued(status === "queued");
      refresh();
    });
  }, [autoOpen, connector, state, isLast, readOnly, view?.note, handoff.id, refresh]);

  async function open() {
    setError("");
    setRound((n) => n + 1);
    const route = openRoute(connector);
    if (route === "connector") {
      setWorking("open");
      const status = await askConnector(handoff.id, "open");
      setWorking("");
      if (status === "opened") { setOpened(true); setQueued(false); refresh(); return; }
      if (["done", "dismissed", "expired"].includes(status)) { refresh(); return; } // settled meanwhile
    }
    setOpened(true);
    setQueued(false);
    // No Connector answered in time: /go still lets one that loads late take the tab.
    const direct = route === "direct" && view?.url;
    window.open(direct ? view.url : `${requestUrl(handoff.id)}/go`, "_blank", "noopener");
  }

  // The tab the Connector works in, brought forward (one out of sight moves
  // next to this one); opened afresh when it is gone.
  async function show() {
    setError("");
    if (connector === "ok" && (await askConnector(handoff.id, "show")) === "shown") { refresh(); return; }
    open();
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
      if (connector === "ok") askConnector(handoff.id, "close");
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
    // A tab the Connector keeps out of sight for it closes.
    if (connector === "ok") askConnector(handoff.id, "close");
  }

  const host = view?.host || handoff.host || "";
  const settled = SETTLED.has(state);
  const note = view?.note || "";
  const background = state === "watching" && !!view?.background;
  // While the Connector works on it, the button shows its tab; the user's
  // turn (a sign-in, a check, a site that refused) makes it the main one.
  const showing = state === "watching" && connector === "ok";
  const yours = showing && NEEDS_YOU.has(note);
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
  const connectorText = (state === "waiting" || state === "opened") && !queued
    ? connectorNote(connector, { origin: window.location.origin }) : "";
  const noteText = state === "watching" || note === "closed" ? watchNote(note) : "";

  return (
    <div className={`chatHandoff ${state}${dragOver ? " dragOver" : ""}`} role="group"
      aria-label={t("Get this paper in your browser")} data-handoff={handoff.id} {...drop}>
      <div className="chatHandoffHead">
        {state === "done" ? <CheckIcon size={16} /> : <ShieldIcon size={16} />}
        <span>{state === "done" ? t("The PDF arrived") : wallHeadline(view?.wall || handoff.wall, host)}</span>
        {state === "watching" && !yours ? <span className="transferSpin inline" aria-hidden="true" /> : null}
      </div>
      <div className="chatHandoffText" aria-live="polite">
        {working === "upload" ? t("Reading the PDF…") : handoffHint(state, { pages: view?.pages || 0, background, queued })}
        {noteText ? <> {noteText}</> : null}
        {connectorText ? <> {connectorText}</> : null}
      </div>
      {handoff.source && !settled ? <div className="chatHandoffSource" title={handoff.source}>{handoff.source}</div> : null}
      {error ? <div className="chatHandoffError" role="alert">{error}</div> : null}
      {!settled && !readOnly ? (
        <div className="chatHandoffActions">
          {showing ? (
            <button type="button" className={`uiBtn sm${yours ? " primary" : ""}`} onClick={show}
              title={t("Bring the tab Gamma Connector works in forward")}>
              <ExternalLinkIcon size={14} />{t("Show the tab")}
            </button>
          ) : (
            <button type="button" className={`uiBtn sm${state === "waiting" ? " primary" : ""}`} onClick={open}
              disabled={working === "open"} title={t("Open the page in a new tab to sign in or pass the check")}>
              <ExternalLinkIcon size={14} />{state === "waiting" ? t("Open {host}", { host }) : t("Open again")}
            </button>
          )}
          <button type="button" className={`uiBtn sm${state === "opened" && !queued ? " primary" : ""}`} disabled={!!working}
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
// the user's — either keeps the chat from sending in their name. `autoOpen`:
// the "Fetch blocked papers in the background" setting.
export default function FetchHandoffCards({ actions, isLast, busy, draft, readOnly, autoOpen = false, onContinue }) {
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
      {handoffs.map((h) => (
        <HandoffCard key={h.id} handoff={h} isLast={isLast} readOnly={readOnly} autoOpen={autoOpen} onState={onState} />
      ))}
      {ready && !auto ? (
        <button type="button" className="uiBtn sm primary chatHandoffContinue" disabled={busy}
          onClick={() => send(continuePrompt(arrived))}>
          {t("Continue with the PDF")}
        </button>
      ) : null}
    </div>
  );
}
