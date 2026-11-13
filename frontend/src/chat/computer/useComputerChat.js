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
import { POLL_MS, chatPath, liveTurn, turnBody } from "./computerChat.js";

const JSON_HEADERS = { "Content-Type": "application/json" };
const post = (body) => ({ method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body) });

// Which side each page's chat shows, for the tab's life: "computer" for a
// page switched to it, else Gamma AI's.
const sides = new Map();

// [side, setSide] for the page's chat: "ai" or "computer", the latter only
// while the page has a computer side (`available`).
export function useChatSide(pageId, available) {
  const [, redraw] = useState(0);
  const side = available && pageId && sides.get(pageId) === "computer" ? "computer" : "ai";
  const setSide = useCallback((next) => {
    if (!pageId) return;
    if (next === "computer") sides.set(pageId, next);
    else sides.delete(pageId);
    redraw((n) => n + 1);
  }, [pageId]);
  return [side, setSide];
}

// The side's state for the page `pageId` ("" while the side is not shown:
// nothing is read). `{computer, conversation, read, busy, send, stop,
// newChat, history, readHistory, openHistory, deleteHistory}`:
//   computer      the strip's facts as the server read them ({agent, label,
//                 ready, problem, folder, paused, turn}), null until read
//   conversation  the page's active conversation ({id, title, agent,
//                 messages, version}) or null; both are kept with the page
//                 they are of, so a page switched to never shows the last
//                 page's
//   read          whether the page's conversation has been read (or failed)
//   busy          an action is on its way
//   send(text, selections) → whether the turn was made; stop(turnId),
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
  useEffect(() => {
    if (!live) return undefined;
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [live, load]);
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
  const send = useCallback((text, selections) => act(async (page) => {
    if (!turnId.current) turnId.current = makeId();
    const answer = await apiJson(`${API}/local-agents/chat/turns`, post(turnBody(turnId.current, page, text, selections)));
    turnId.current = "";
    keep(page, answer.conversation || null);
  }, (message) => t("Couldn't send to {agent}: {message}", { agent: label, message })), [act, keep, label]);

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
    send, stop, newChat, history, readHistory, openHistory, deleteHistory,
  };
}
