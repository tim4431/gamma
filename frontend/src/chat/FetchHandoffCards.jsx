// The papers a reply fetched, as cards among its steps (ChatDock places them
// at their fetch_paper steps; chat/chatPapers.js and chat/fetchHandoff.js
// have the rules): the file card of each document a fetch read
// (FetchedPaper.jsx), and a card for each one a CAPTCHA, a sign-in page or a
// paywall stopped. That card asks Gamma Connector (its bridge.js) whether it
// can fetch for the request and says why not when it cannot; "Open" then has
// the Connector open the publisher's page itself, which sends the PDF back
// from that tab once the user is through — or, with no Connector to take the
// tab, opens the page directly for the user to download the PDF and drop it
// on the card (the desktop app goes through /api/ai/handoffs/<id>/go, the
// address the system browser's Connector knows the tab by). With "Fetch
// blocked papers in the background" on, the card hands the request to the
// Connector by itself, to fetch in a background tab; "Show the tab" brings
// that tab forward when it needs the user. The card follows the request on
// the server; when the PDF arrives it says so for a moment and becomes the
// paper's file card, and the chat continues by itself (or offers Continue
// when it can't). A card still going on stays in view under the folded
// steps (PinnedFetches), so it moves between two places: what it learnt is
// kept per request (`requests`), not in the card.
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { API, apiJson, isPdfFile } from "../shared/lib/utils";
import { xhrUpload } from "../shared/lib/xhrUpload.js";
import { CheckIcon, ExternalLinkIcon, ShieldIcon, UploadIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";
import {
  ARRIVED_MS, NEEDS_YOU, SETTLED, autoOpens, cardLook, connectorNote, continuePrompt, handoffHint, handoffState,
  openRoute, pollDelay, replyHandoffs, shouldContinue, wallHeadline, watchNote,
} from "./fetchHandoff.js";
import { paperOf } from "./chatPapers.js";
import FetchedPaper from "./FetchedPaper.jsx";

const requestUrl = (id) => `${API}/ai/handoffs/${encodeURIComponent(id)}`;
// The desktop app opens links in the system browser, where a Connector may be.
const DESKTOP = /\bElectron\//.test(navigator.userAgent);

const FetchContext = createContext(null);

// What each request's card learnt, for as long as the page is open: the
// server's view, whether the user opened the page, the Connector's answer,
// whether it was ever seen going on, and when its PDF arrived.
const requests = new Map(); // request id → {view, opened, queued, autoTried, connector, live, arrivedAt}

function requestMemo(id) {
  if (!requests.has(id)) requests.set(id, {});
  return requests.get(id);
}

// A card state kept in its request's memo too.
function useKept(memo, field, initial) {
  const [value, setValue] = useState(() => memo[field] ?? initial);
  const set = useCallback((next) => { memo[field] = next; setValue(next); }, [memo, field]);
  return [value, set];
}

// Gamma Connector's word on request `id` (connectorNote), asked through
// window messages its content script answers. The script loads after the
// page, so the question is asked again before no answer counts as "missing";
// `round` asks afresh (the page opened again, the window came back into
// focus after signing the Connector in).
function useConnector(id, active, round, memo) {
  const [status, setStatus] = useState(() => (DESKTOP ? "desktop" : memo.connector || "unknown"));
  useEffect(() => {
    if (DESKTOP || !active) return undefined;
    let answered = false;
    const settle = (next) => { memo.connector = next; setStatus(next); };
    const onMessage = (e) => {
      const d = e.data;
      if (e.source !== window || e.origin !== window.location.origin || d?.source !== "gamma-connector"
          || d.type !== "connector-status" || d.id !== id) return;
      answered = true;
      settle(d.status);
    };
    window.addEventListener("message", onMessage);
    const ask = () => { if (!answered) window.postMessage({ source: "gamma-app", type: "connector-probe", id }, window.location.origin); };
    const timers = [0, 1500, 3000].map((ms) => setTimeout(ask, ms));
    timers.push(setTimeout(() => { if (!answered) settle("missing"); }, 4500));
    return () => { window.removeEventListener("message", onMessage); timers.forEach(clearTimeout); };
  }, [id, active, round, memo]);
  return status;
}

// Ask the Connector to open ("open", `background` for a background tab),
// show ("show") or let go of ("close") request `id`'s tab. Resolves to its
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

// One request's card. `pinned`: under the folded steps (only while it goes
// on, or its PDF just arrived); else at its step, where a delivered request
// is the paper's file card with `detail`, the step's tool output toggle.
function HandoffCard({ handoff, pinned = false, detail = null }) {
  const { isLast = false, readOnly = true, autoOpen = false, save = null, onState } = useContext(FetchContext) || {};
  const memo = useMemo(() => requestMemo(handoff.id), [handoff.id]);
  const [view, setView] = useKept(memo, "view", null);
  const [opened, setOpened] = useKept(memo, "opened", false);
  const [queued, setQueued] = useKept(memo, "queued", false);
  const [working, setWorking] = useState(""); // "upload" | "dismiss" | "open"
  const [error, setError] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef(null);
  const [round, setRound] = useState(0);
  const [, setTick] = useState(0);
  const state = handoffState(view, { opened });
  const connector = useConnector(handoff.id, !SETTLED.has(state) && state !== "loading", round, memo);
  // A PDF arriving while the card was seen waiting says so for a moment; a
  // request already done when the chat opened is its file card at once.
  if (state === "done") memo.arrivedAt ||= memo.live ? Date.now() : 0;
  else if (!SETTLED.has(state) && state !== "loading") memo.live = true;
  const look = cardLook(state, { arrivedAt: memo.arrivedAt, pinned });
  const pages = view?.pages || 0;
  const paper = useMemo(() => paperOf(handoff.source, { request: handoff.id, pdf: true, pages }),
    [handoff.source, handoff.id, pages]);

  const refresh = useCallback(async () => {
    try {
      setView(await apiJson(requestUrl(handoff.id)));
    } catch (err) {
      // A restart or the request's age forgets it; another account never had it.
      if ([401, 403, 404].includes(err.status) || /^401/.test(err.message)) setView({ status: "gone" });
    }
  }, [handoff.id, setView]);

  // A settled request is not asked about again when its card moves.
  useEffect(() => { if (!SETTLED.has(handoffState(memo.view))) refresh(); }, [refresh, memo]);
  useEffect(() => {
    if (SETTLED.has(state)) return undefined;
    const delay = pollDelay(state, { isLast });
    const timer = delay ? setInterval(() => { if (document.visibilityState === "visible") refresh(); }, delay) : null;
    const onFocus = () => { refresh(); setRound((n) => n + 1); };
    window.addEventListener("focus", onFocus);
    return () => { if (timer) clearInterval(timer); window.removeEventListener("focus", onFocus); };
  }, [state, isLast, refresh]);
  useEffect(() => { onState?.(handoff.id, state); }, [handoff.id, state, onState]);
  // "The PDF arrived" gives way to the file card.
  useEffect(() => {
    if (look !== "arrived") return undefined;
    const timer = setTimeout(() => setTick((n) => n + 1), Math.max(0, memo.arrivedAt + ARRIVED_MS - Date.now()) + 50);
    return () => clearTimeout(timer);
  }, [look, memo]);

  // "Fetch blocked papers in the background": once per request, the
  // Connector takes it without a click.
  useEffect(() => {
    if (memo.autoTried || !autoOpens({ auto: autoOpen, connector, state, isLast, readOnly, note: view?.note })) return;
    memo.autoTried = true;
    askConnector(handoff.id, "open", { background: true }).then((status) => {
      if (status !== "opened" && status !== "queued") return;
      setOpened(true);
      setQueued(status === "queued");
      refresh();
    });
  }, [autoOpen, connector, state, isLast, readOnly, view?.note, handoff.id, refresh, memo, setOpened, setQueued]);

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

  // The tab the Connector works in, brought forward; opened afresh when it is gone.
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
    // A background tab the Connector keeps for it closes.
    if (connector === "ok") askConnector(handoff.id, "close");
  }

  if (!look) return null;
  if (look === "file") return <FetchedPaper paper={paper} save={readOnly ? null : save} detail={detail} />;
  if (look === "loading") {
    return (
      <div className="chatPaper" aria-busy="true" data-handoff={handoff.id}>
        <span className="transferSpin inline" aria-hidden="true" />
        <span className="chatPaperTitle">{handoff.source || handoff.host}</span>
      </div>
    );
  }
  if (look === "arrived") {
    return (
      <div className="chatHandoff done" role="status" data-handoff={handoff.id}>
        <div className="chatHandoffHead"><CheckIcon size={16} /><span>{t("The PDF arrived")}</span></div>
        <div className="chatHandoffText">{handoffHint("done", { pages })}</div>
      </div>
    );
  }

  const host = view?.host || handoff.host || "";
  const settled = look === "settled";
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
        <ShieldIcon size={16} />
        <span>{wallHeadline(view?.wall || handoff.wall, host)}</span>
        {state === "watching" && !yours ? <span className="transferSpin inline" aria-hidden="true" /> : null}
      </div>
      <div className="chatHandoffText" aria-live="polite">
        {working === "upload" ? t("Reading the PDF…") : handoffHint(state, { pages, background, queued })}
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

// A reply's fetches: what its cards share, and the continuation once every
// request it opened is settled. `busy`: the chat is answering; `draft`: the
// composer holds something of the user's — either keeps the chat from
// sending in their name. `autoOpen`: the "Fetch blocked papers in the
// background" setting. `save`: how "Add to library" saves ({folder,
// options, onOpenPage, onLibraryChange}); null hides it.
export function ReplyFetches({ actions, isLast, busy, draft, readOnly, autoOpen = false, save = null, onContinue, children }) {
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

  const ready = !sent.current && !readOnly && isLast && arrived.length > 0 && list.every((s) => SETTLED.has(s)) && !auto;
  const continueText = ready ? continuePrompt(arrived) : "";
  const value = useMemo(() => ({
    isLast, readOnly, autoOpen, save, onState, handoffs, busy,
    onContinueNow: continueText ? () => send(continueText) : null,
  }), [isLast, readOnly, autoOpen, save, onState, handoffs, busy, continueText, send]);
  return <FetchContext.Provider value={value}>{children}</FetchContext.Provider>;
}

// Under the steps pill: with the steps folded, the requests still going on
// (and a PDF that just arrived), and the Continue button when the chat
// can't continue by itself.
export function PinnedFetches({ open }) {
  const ctx = useContext(FetchContext);
  if (!ctx) return null;
  return (
    <div className="chatHandoffs">
      {open ? null : ctx.handoffs.map((h) => <HandoffCard key={h.id} handoff={h} pinned />)}
      {ctx.onContinueNow ? (
        <button type="button" className="uiBtn sm primary chatHandoffContinue" disabled={ctx.busy} onClick={ctx.onContinueNow}>
          {t("Continue with the PDF")}
        </button>
      ) : null}
    </div>
  );
}

// A fetch_paper step with a card (chatPapers.replyFetches): the request's
// card, or the file card of what it read. `detail`: {open, onToggle, text},
// the step's tool output.
export function FetchStep({ item, detail = null }) {
  const ctx = useContext(FetchContext);
  return (
    <div className="chatToolAction chatFetchStep">
      {item.card
        ? <HandoffCard handoff={item.card} detail={detail} />
        : <FetchedPaper paper={item.paper} save={ctx?.readOnly ? null : ctx?.save} detail={detail} />}
      {detail?.open ? <pre className="chatToolDetail">{detail.text}</pre> : null}
    </div>
  );
}
