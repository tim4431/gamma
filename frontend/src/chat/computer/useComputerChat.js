// The chat's computer side, its state (docs/dev/local_agents.md "The chat's
// computer side"): the owner of the side's part of the chat window, so
// ChatDock only switches to it. The conversation is the server's: a turn
// runs on this computer whatever the tab does, the server writes the
// agent's message as it goes, and the side reads it (GET
// /api/local-agents/chat) as the page's chat opens, after each action,
// when the window comes back, and every POLL_MS while a turn is queued or
// running. Closing the tab stops nothing; Stop is a request of its own.
import { useCallback, useEffect, useRef, useState } from "react";
import { API, apiJson, makeId } from "../../shared/lib/utils";
import { t } from "../../shared/i18n/i18n.js";
import {
  AWAY_POLL_MS, POLL_MS, SOURCES_MAX, answerBody, askPath, awayFrom, chatPath, liveTurn, sourcesBody, turnBody,
} from "./computerChat.js";

const JSON_HEADERS = { "Content-Type": "application/json" };
const post = (body) => ({ method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body) });

// The side's state for the page `pageId` ("" while the side is not shown:
// nothing is read). `{computer, conversation, read, busy, send, answer,
// stop, newChat, history, readHistory, openHistory, deleteHistory}`:
//   computer      the strip's facts as the server read them ({agent, label,
//                 ready, problem, folder, paused, turn, models}, and for
//                 another computer's agent {computer, online, since,
//                 remote: true}), null until read; `models` the agent's
//                 [{id, label, efforts, effort, default}]
//   conversation  the page's active conversation ({id, title, agent,
//                 messages, version}) or null; both are kept with the page
//                 they are of, so a page switched to never shows the last
//                 page's
//   read          whether the page's conversation has been read (or failed)
//   busy          an action is on its way
//   send(text, selections, choice) → whether the turn was made, `choice`
//   the conversation's {model, effort} (computerChat.modelChoice); answer(turnId,
//   askId, decision, note) answers what the turn asks, throwing a
//   refusal for the card to say; stop(turnId),
//   newChat(), openHistory(id), deleteHistory(id) do what they say and read
//   the conversation again; `history` the archived conversations
//   ([{id, title, agent, updated_at, turns}], null until readHistory())
// `setStatus` says what failed, naming the agent as the server read it, or
// before a read has `known` (the vault's label, "Codex").
export function useComputerChat(pageId, setStatus, known = "") {
  const [held, setHeld] = useState({ pageId: "", computer: null, conversation: null, read: false });
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState(null);
  const heldRef = useRef(held);
  heldRef.current = held;
  const open = useRef(pageId);
  open.current = pageId;
  const status = useRef(setStatus);
  status.current = setStatus;
  // Each read is numbered: only the newest asked lands, and an action's
  // own answer outranks a read already on its way.
  const asked = useRef(0);
  // The id of the turn being sent, minted at the first attempt and kept
  // until the server made it.
  const turnId = useRef("");

  const load = useCallback(async () => {
    const page = open.current;
    if (!page) return;
    const n = ++asked.current;
    const now = heldRef.current.pageId === page ? heldRef.current : null;
    const mine = now?.conversation || null;
    try {
      const answer = await apiJson(`${API}${chatPath(page, mine ? mine.version : null)}`);
      if (n !== asked.current) return;
      setHeld((prev) => ({
        pageId: page, computer: answer.computer || null, read: true,
        conversation: answer.same && prev.pageId === page ? prev.conversation : answer.conversation || null,
      }));
    } catch (err) {
      if (n !== asked.current) return;
      setHeld((prev) => (prev.pageId === page ? { ...prev, read: true } : { pageId: page, computer: null, conversation: null, read: true }));
      // Said once, as the page's chat opens: a poll that fails is tried again.
      if (!now?.read) status.current?.(t("Could not load chat: {message}", { message: err.message }));
    }
  }, []);

  // What an action's answer says of the conversation, kept at once.
  const keep = useCallback((page, conversation) => {
    asked.current++;
    setHeld((prev) => ({ ...(prev.pageId === page ? prev : { computer: null }), pageId: page, conversation, read: true }));
  }, []);

  useEffect(() => {
    turnId.current = "";
    setHistory(null);
    load();
  }, [pageId, load]);

  const state = held.pageId === pageId ? held : { computer: null, conversation: null, read: false };
  const live = !!pageId && (!!state.computer?.turn || !!liveTurn(state.conversation));
  // A turn waiting for another computer that is away is read less often.
  const every = awayFrom(state.computer) ? AWAY_POLL_MS : POLL_MS;
  useEffect(() => {
    if (!live) return undefined;
    const timer = setInterval(load, every);
    return () => clearInterval(timer);
  }, [live, every, load]);
  useEffect(() => {
    const again = () => { if (document.visibilityState !== "hidden") load(); };
    window.addEventListener("focus", again);
    document.addEventListener("visibilitychange", again);
    return () => {
      window.removeEventListener("focus", again);
      document.removeEventListener("visibilitychange", again);
    };
  }, [load]);

  // An action on the page's conversation: `busy` meanwhile, its failure
  // said as `failed` words it, and the conversation read again after.
  const act = useCallback(async (run, failed) => {
    const page = open.current;
    if (!page) return false;
    setBusy(true);
    try {
      await run(page);
      return true;
    } catch (err) {
      status.current?.(failed(err.message || String(err)));
      return false;
    } finally {
      setBusy(false);
      load();
    }
  }, [load]);

  const label = state.computer?.label || known;
  const send = useCallback((text, selections, choice = null) => act(async (page) => {
    if (!turnId.current) turnId.current = makeId();
    const answer = await apiJson(`${API}/local-agents/chat/turns`, post(turnBody(turnId.current, page, text, selections, choice)));
    turnId.current = "";
    keep(page, answer.conversation || null);
  }, (message) => t("Couldn't send to {agent}: {message}", { agent: label, message })), [act, keep, label]);

  // The person's answer to what the turn asks: the card says a refusal
  // itself (an ask answered already, or a turn that ended), and the
  // conversation is read again either way, the ask leaving it.
  const answer = useCallback(async (id, askId, decision, note = "") => {
    try {
      await apiJson(`${API}${askPath(id, askId)}`, post(answerBody(decision, note)));
    } finally {
      load();
    }
  }, [load]);

  const stop = useCallback((id) => act(async () => {
    await apiJson(`${API}/local-agents/chat/turns/${encodeURIComponent(id)}/stop`, post({}));
  }, (message) => t("Couldn't stop {agent}: {message}", { agent: label, message })), [act, label]);

  const newChat = useCallback(() => act(async (page) => {
    await apiJson(`${API}/local-agents/chat/new`, post({ page }));
    keep(page, null);
    setHistory(null);
  }, (message) => t("Couldn't start a new chat: {message}", { message })), [act, keep]);

  const readHistory = useCallback(async () => {
    const page = open.current;
    if (!page) return;
    try {
      const answer = await apiJson(`${API}/local-agents/chat/history?page=${encodeURIComponent(page)}`);
      if (open.current === page) setHistory(answer.conversations || []);
    } catch (err) {
      if (open.current === page) setHistory([]);
      status.current?.(t("Chat history: {message}", { message: err.message }));
    }
  }, []);

  const openHistory = useCallback((id) => act(async (page) => {
    const answer = await apiJson(`${API}/local-agents/chat/history/${encodeURIComponent(id)}/open`, post({}));
    keep(page, answer.conversation || null);
    setHistory(null);
  }, (message) => t("Couldn't open the conversation: {message}", { message })), [act, keep]);

  // The row leaves the list at once; a refusal has the list read again.
  const deleteHistory = useCallback((id) => act(async () => {
    setHistory((rows) => (rows ? rows.filter((r) => r.id !== id) : rows));
    try {
      await apiJson(`${API}/local-agents/chat/history/${encodeURIComponent(id)}`, { method: "DELETE" });
    } catch (err) {
      setHistory(null);
      throw err;
    }
  }, (message) => t("Couldn't delete the conversation: {message}", { message })), [act]);

  return {
    computer: state.computer, conversation: state.conversation, read: state.read, busy,
    send, answer, stop, newChat, history, readHistory, openHistory, deleteHistory,
  };
}

// The source lines of the passages selected, as the chips note them: one
// entry a passage, in order, its {file, line, end?}, or null while it is
// not known or has none. Asked (POST /api/local-agents/chat/sources) a
// moment after the passages change, those not known yet, and kept a
// minute, and only until a turn of the conversation ends (`ended`, its
// last ending: the agent may have moved the lines); a failed ask is asked
// again once the passages change.
const sourcesKnown = new Map(); // passage key → {source or null, at}
const SOURCES_WAIT_MS = 400;
const SOURCES_FRESH_MS = 60_000;
const SOURCES_KEPT = 500;
const passageKey = (page, s, ended) => JSON.stringify([page, s.text, s.page || null, s.box || null, ended || null]);
const knownSource = (key) => {
  const known = sourcesKnown.get(key);
  return known && Date.now() - known.at < SOURCES_FRESH_MS ? known : null;
};

export function usePassageSources(pageId, selections, ended = null) {
  const [, redraw] = useState(0);
  const shown = (selections || []).filter((s) => s?.text);
  const missing = pageId ? shown.filter((s) => !knownSource(passageKey(pageId, s, ended))).slice(0, SOURCES_MAX) : [];
  const wanted = missing.map((s) => passageKey(pageId, s, ended)).join("\n");
  const missingRef = useRef(missing);
  missingRef.current = missing;
  useEffect(() => {
    if (!wanted) return undefined;
    let left = false;
    const timer = setTimeout(async () => {
      const asked = missingRef.current;
      try {
        const answer = await apiJson(`${API}/local-agents/chat/sources`, post(sourcesBody(pageId, asked)));
        if (sourcesKnown.size > SOURCES_KEPT) sourcesKnown.clear();
        const at = Date.now();
        asked.forEach((s, i) => sourcesKnown.set(passageKey(pageId, s, ended), { source: answer.sources?.[i] || null, at }));
        if (!left) redraw((n) => n + 1);
      } catch { /* the chips keep their page numbers */ }
    }, SOURCES_WAIT_MS);
    return () => { left = true; clearTimeout(timer); };
  }, [pageId, wanted, ended]);
  // One kept over a minute is shown while it is asked again.
  return (selections || []).map((s) => (pageId && s?.text ? sourcesKnown.get(passageKey(pageId, s, ended))?.source || null : null));
}
