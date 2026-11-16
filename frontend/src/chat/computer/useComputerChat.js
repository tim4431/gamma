// The chat's computer side, its state (docs/dev/local_agents.md "The chat's
// computer side"): the conversation with a computer's agent picked in the
// chat, which ChatDock draws through the chat's own transcript and
// composer. The conversation is the server's: a turn runs on this computer
// whatever the tab does, the server writes the agent's message as it goes,
// and the side reads it (GET /api/local-agents/chat) as the bucket's chat
// opens, after each action, when the window comes back, and every POLL_MS
// while a turn is queued or running. Closing the tab stops nothing; Stop is
// a request of its own. `useComputerSide` is what ChatDock reads of it; the
// plain functions under the imports read and change what History lists of
// the computer's agents.
import { useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { API, apiJson, makeId } from "../../shared/lib/utils";
import { GammaNavContext } from "../../shared/ui/Widgets";
import { t } from "../../shared/i18n/i18n.js";
import { FolderIcon } from "../../shared/ui/Icons";
import { stripFacts } from "../agents.js";
import {
  AWAY_POLL_MS, POLL_MS, SOURCES_MAX, TEXT_MAX, answerBody, askPath, awayFrom, canSend, chatPath, emptyLine, isRemote,
  lastEnded, liveTurn, modelChoice, modelDisplay, retryOf, shownMessages, sourcesBody, turnBody, workPlace,
} from "./computerChat.js";

const JSON_HEADERS = { "Content-Type": "application/json" };
const post = (body) => ({ method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body) });
// A bucket with the agent picked for it and the conversation it shows in
// place of its own (`shown`: a comment thread's, a co-author's; null for
// its own), as one key ("" for no bucket).
const seatOf = (bucket, agent, shown = null) => (bucket
  ? [bucket, agent, shown?.thread || "", shown?.conversation || ""].join("\n") : "");
const bucketOf = (seat) => seat.split("\n")[0];
const agentOf = (seat) => seat.split("\n")[1] || "";
const shownOf = (seat) => {
  const [, , thread, conversation] = seat.split("\n");
  return thread ? { thread } : conversation ? { conversation } : null;
};

// New chat in the bucket (POST /api/local-agents/chat/new): its active
// conversation with a computer's agent archived, whichever agent it has;
// throws the server's refusal (a turn of it still to end).
export function newComputerChat(bucket) {
  return apiJson(`${API}/local-agents/chat/new`, post({ bucket }));
}

// The bucket's conversations with the computer's agents, as History lists
// them (GET /api/local-agents/chat/history): {conversations, active}, the
// archived ones and the active one, of whichever agent.
export function computerHistory(bucket) {
  return apiJson(`${API}/local-agents/chat/history?bucket=${encodeURIComponent(bucket)}`);
}

// An archived conversation made its bucket's active one, the active one
// archived; throws the server's refusal (a turn of the active one still to
// end).
export function openComputerChat(id) {
  return apiJson(`${API}/local-agents/chat/history/${encodeURIComponent(id)}/open`, post({}));
}

// An archived conversation deleted, its session, grants and directory of
// its own with it.
export function deleteComputerChat(id) {
  return apiJson(`${API}/local-agents/chat/history/${encodeURIComponent(id)}`, { method: "DELETE" });
}

// The sessions the CLI of the agent `agent` keeps of the directory `dir`
// (computerChat.js sessionsDir), started in a terminal or an editor (GET
// /api/local-agents/sessions; `agent` this computer's by its name, another
// computer's by its id, asked there over its channel): {sessions: [{id,
// title, at, created, source, ours, busy}], away?}, the most recently used
// first, `away` why another computer answered nothing (sessionsAway).
export function computerSessions(agent, dir) {
  return apiJson(`${API}/local-agents/sessions?${new URLSearchParams({ agent, dir })}`);
}

// One of them opened in the bucket's chat ({bucket, agent, dir, session}):
// {conversation}, the bucket's active conversation now, whose next turn
// resumes the session; throws the server's refusal (another window has it
// open, another chat continues it, a turn still to end, the other computer
// offline or not answering).
export function openComputerSession(body) {
  return apiJson(`${API}/local-agents/sessions/open`, post(body));
}

// Where a conversation, active or archived, downloads from.
export const computerExportPath = (id) => `${API}/local-agents/chat/history/${encodeURIComponent(id)}/export`;

// The side's state for the bucket `pageId` (a page's id, a folder's, or
// "home"; "" while the side is not shown: nothing is read) with the agent
// picked for it (`agent`, chat/agents.js: its `id` asked for, this
// computer's agent's name or another computer's agent's id, its `label`
// what the words name before the read), or with `shown` another
// conversation in place of its own ({thread} that comment thread's, its
// Open in chat; {conversation} a co-author's with the reader's agent).
// `{computer,
// conversation, read,
// error, busy, reload, say, send, answer, stop, newChat, revokeGrants}`:
//   computer      the strip's facts as the server read them ({agent, label,
//                 ready, problem, folder, folder_id, paused, turn, models,
//                 grants, dir, session}, and for another computer's agent
//                 {computer, online, since, remote: true}), null until read;
//                 `models` the agent's [{id, label, efforts, effort,
//                 default}], `grants` the commands allowed in the
//                 conversation [{command, cwd, host}]
//   conversation  the bucket's active conversation ({id, title, agent,
//                 messages, version}, and `person`, the co-author's
//                 username, for a co-author's) or null; both are kept with the
//                 bucket and the agent they are of, so another one never
//                 shows the last one's
//   read          whether the conversation has been read (or failed)
//   error         why it could not be read, "" once it was; reload()
//                 reads it again
//   busy          an action is on its way
//   send(text, selections, choice) → whether the turn was made, `choice`
//   the conversation's {model, effort} (computerChat.modelChoice) and, for
//   a conversation that begins, `dir` where it works (computerChat
//   workPlace); answer(turnId, askId, decision, note) answers what the turn
//   asks, throwing a refusal for the card to say; stop(turnId) and
//   newChat() do what they say and read the conversation again;
//   revokeGrants() takes back what was allowed in the conversation;
//   say(text) is the status pill
// `setStatus` says what failed, naming the agent as the server read it, or
// before a read as the agent picked.
export function useComputerChat(pageId, agent, setStatus, shown = null) {
  const name = agent?.id || "";
  const known = agent?.label || "";
  // What is held is kept with the bucket, the agent and the conversation
  // shown (`seat`), so another one never shows the last one's.
  const seat = seatOf(pageId, name, shown);
  const [held, setHeld] = useState({ seat: "", computer: null, conversation: null, read: false, error: "" });
  const [busy, setBusy] = useState(false);
  const heldRef = useRef(held);
  heldRef.current = held;
  const open = useRef(seat);
  open.current = seat;
  const status = useRef(setStatus);
  status.current = setStatus;
  // Each read is numbered: only the newest asked lands, and an action's
  // own answer outranks a read already on its way.
  const asked = useRef(0);
  // The id of the turn being sent, minted at the first attempt and kept
  // until the server made it.
  const turnId = useRef("");

  const load = useCallback(async () => {
    const key = open.current;
    if (!key) return;
    const n = ++asked.current;
    const now = heldRef.current.seat === key ? heldRef.current : null;
    const mine = now?.conversation || null;
    try {
      const answer = await apiJson(`${API}${chatPath(bucketOf(key), mine ? mine.version : null, agentOf(key), shownOf(key))}`);
      if (n !== asked.current) return;
      setHeld((prev) => ({
        seat: key, computer: answer.computer || null, read: true, error: "",
        conversation: answer.same && prev.seat === key ? prev.conversation : answer.conversation || null,
      }));
    } catch (err) {
      if (n !== asked.current) return;
      // A poll that fails keeps what was read, and is tried again; a
      // conversation never read says why where it would be.
      const error = t("Could not load chat: {message}", { message: err.message });
      setHeld((prev) => (prev.seat === key && prev.read && !prev.error ? prev
        : { seat: key, computer: prev.seat === key ? prev.computer : null, conversation: null, read: true, error }));
    }
  }, []);

  // What an action's answer says of the conversation, kept at once.
  const keep = useCallback((key, conversation) => {
    asked.current++;
    setHeld((prev) => ({ ...(prev.seat === key ? prev : { computer: null }), seat: key, conversation, read: true, error: "" }));
  }, []);

  useEffect(() => {
    turnId.current = "";
    load();
  }, [seat, load]);

  // Another computer coming back or going away, or its agent getting ready
  // or not, as the page's computers say it once read again on the
  // account's nudge (vault/usePageComputer.js): read again at once, since
  // no message waits for a computer away and so no turn's poll would
  // notice.
  const presence = agent?.remote ? JSON.stringify([agent.online === true, agent.ready ?? null, agent.code || ""]) : "";
  const presenceSeen = useRef(presence);
  useEffect(() => {
    if (presenceSeen.current === presence) return;
    presenceSeen.current = presence;
    if (presence) load();
  }, [presence, load]);

  const state = held.seat === seat ? held : { computer: null, conversation: null, read: false, error: "" };
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

  // An action on the conversation: `busy` meanwhile, its failure said as
  // `failed` words it, and the conversation read again after.
  const act = useCallback(async (run, failed) => {
    const key = open.current;
    if (!key) return false;
    setBusy(true);
    try {
      await run(bucketOf(key), key);
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
  const send = useCallback((text, selections, choice = null) => act(async (page, key) => {
    if (!turnId.current) turnId.current = makeId();
    const body = turnBody(turnId.current, page, text, selections, { ...choice, agent: agentOf(key) });
    const answer = await apiJson(`${API}/local-agents/chat/turns`, post(body));
    turnId.current = "";
    keep(key, answer.conversation || null);
  }, (message) => t("Couldn't send to {agent}: {message}", { agent: label, message })), [act, keep, label]);

  // The person's answer to what the turn asks. One taken is left to the
  // next poll, which drops the ask: the card says what was answered until
  // then. A refusal (an ask answered already, or a turn that ended) the
  // card says itself, and the conversation is read again at once.
  const answer = useCallback(async (id, askId, decision, note = "") => {
    try {
      await apiJson(`${API}${askPath(id, askId)}`, post(answerBody(decision, note)));
    } catch (err) {
      load();
      throw err;
    }
  }, [load]);

  const stop = useCallback((id) => act(async () => {
    await apiJson(`${API}/local-agents/chat/turns/${encodeURIComponent(id)}/stop`, post({}));
  }, (message) => t("Couldn't stop {agent}: {message}", { agent: label, message })), [act, label]);

  const newChat = useCallback(() => act(async (page, key) => {
    await newComputerChat(page);
    keep(key, null);
  }, (message) => t("Couldn't start a new chat: {message}", { message })), [act, keep]);

  // Allowed in this chat taken back: the conversation's commands ask again.
  const revokeGrants = useCallback(() => act(async () => {
    const id = heldRef.current.conversation?.id;
    if (id) await apiJson(`${API}/local-agents/chat/grants/${encodeURIComponent(id)}`, { method: "DELETE" });
  }, (message) => t("Couldn't take back what was allowed: {message}", { message })), [act]);

  // What the side's own pieces say in the status pill (a link to a file
  // that names none).
  const say = useCallback((text) => status.current?.(text), []);

  return {
    computer: state.computer, conversation: state.conversation, read: state.read, error: state.error, busy,
    reload: load, say, send, answer, stop, newChat, revokeGrants,
  };
}

// The model and effort chosen for each conversation in this tab, by the
// bucket, the agent and the conversation's id ("" before its first turn); a
// turn's message keeps what it ran with, so a reopened conversation shows
// it.
const picks = new Map();
const pickKey = (bucket, agent, conversation) => `${bucket}\n${agent}\n${conversation?.id || ""}`;
// The directory each bucket's next conversation is to work in (an offered
// one's id, "" for one of its own), until its first turn.
const places = new Map();

// What ChatDock reads of the side for the bucket `bucket` ("" while no
// computer's agent is the one shown: nothing is read) and the agent picked
// (`agent`), with `page` whether the bucket is a page, `dirs` the
// directories this computer offers, and the PDF passages selected (App's,
// which go with the next message and are cleared once the server took it):
//   chat        useComputerChat's state
//   info        the strip's facts: the server's, else the picked agent's
//               (chat/agents.js stripFacts) until its read lands
//   label       the agent's name; `where` the other computer's that runs
//               it ("" for this one)
//   messages    the conversation as the transcript draws it (shownMessages)
//   stopId      the turn Stop stops: the conversation's own, or the one the
//               server says is queued for it before the conversation shows
//               it; "" for none, `working` while there is one (New chat and
//               History's other conversations wait for it)
//   ready(text) whether `text` can go now; send(text) → whether the turn
//               was made
//   retry(i)    Retry for the failed turn at the transcript's index `i`,
//               null when it is not the newest or nothing could go now
//   chip        the model chip's props (chat/AgentChip.jsx: label, text,
//               models, model, onModel, sections): the agent's models and
//               efforts and where it works, the choice the conversation's
//   grants      the Allowed in this chat chip's props (GrantChip), null
//               while nothing is allowed
//   place, pickPlace   where a conversation that begins works (workPlace)
//   sources     the passages' source lines (usePassageSources)
//   links       what a changed file's link resolves through (PathLink's
//               context), null outside a work folder
//   empty       {text, again, offer} the empty transcript's line, `again`
//               when it says why the conversation could not be read (Try
//               again), `offer` when this computer offers no directory to
//               work in (Offer a directory…); null until the read
//   shown       the conversation the side shows in place of the bucket's
//               own (`shown`, chatSide.js useChatShown: {thread} a comment
//               thread's, {conversation} a co-author's), null for its own:
//               nothing is sent from the chat then (a thread's replies are
//               its messages, a co-author's chat is theirs)
export function useComputerSide(bucket, agent, { page = false, dirs = [], pdfSelections = [], setPdfSelections, setStatus,
  shown = null }) {
  const chat = useComputerChat(bucket, bucket ? agent : null, setStatus, shown);
  const info = chat.computer || stripFacts(agent);
  const label = info?.label || agent?.label || "";
  const conversation = chat.conversation;
  const messages = useMemo(() => shownMessages(conversation, info), [conversation, info]);
  const [, redraw] = useState(0); // `picks` and `places` keep the choices
  const key = pickKey(bucket, agent?.id || "", conversation);
  const models = info?.models || [];
  const choice = modelChoice(models, conversation, picks.get(key) || null);
  const pickModel = (next) => {
    picks.set(key, next);
    redraw((n) => n + 1);
  };
  // Where a conversation that begins works, once the server said where it
  // stands (a vault's own directory is the vault's).
  const place = chat.computer ? workPlace(chat.computer, conversation, dirs, places.get(bucket) || "") : null;
  const pickPlace = (dir) => {
    places.set(bucket, dir);
    redraw((n) => n + 1);
  };
  const how = () => ({ ...choice, ...(place?.pick ? { dir: place.value } : {}) });
  const remote = isRemote(info);
  const where = remote ? String(info.computer || "") : "";
  // The source lines need the window to be the computer the folder is on.
  const sources = usePassageSources(remote || !page ? "" : bucket, pdfSelections, lastEnded(conversation));
  const stopId = liveTurn(conversation)?.turn_id || chat.computer?.turn?.turn_id || "";
  // Off until the conversation was read: one that failed may hold a turn.
  const landed = chat.read && !chat.error;
  const ready = (text) => landed && !shown && !chat.busy && canSend(info, conversation, text);

  async function send(text) {
    if (!ready(text)) return false;
    const sent = await chat.send(String(text).slice(0, TEXT_MAX), pdfSelections, how());
    if (sent) {
      setPdfSelections([]);
      // A first turn's picks are its conversation's now, and a new chat
      // starts from the agent's default and no folder.
      if (!conversation) {
        picks.delete(key);
        places.delete(bucket);
      }
    }
    return sent;
  }

  // Retry on a failed turn, the newest: the person's message again, with
  // its passages, while a message could go.
  const retry = (i) => {
    const again = retryOf(conversation, conversation?.messages?.[i]);
    if (!again || !ready(again.text)) return null;
    return () => chat.send(again.text, again.selections, how());
  };

  // A changed file's link opens it (PathLink): its path is the work
  // folder's (the server's `folder_id`), resolved from the folder's top.
  const nav = useContext(GammaNavContext);
  const folderId = chat.computer?.folder_id || "";
  const { say } = chat;
  const links = useMemo(() => (folderId && nav?.openPage ? {
    pageId: bucket,
    resolve: async (href) => {
      try {
        const d = await apiJson(`${API}/folders/${encodeURIComponent(folderId)}/at?${new URLSearchParams({ path: href })}`);
        return d.pages || [];
      } catch {
        return [];
      }
    },
    open: (id) => nav.openPage(id),
    say,
  } : null), [folderId, nav, bucket, say]);

  const empty = !conversation?.messages?.length && chat.error ? { text: chat.error, again: true }
    : chat.read ? { text: shown?.thread ? t("This comment thread has no conversation of yours with {agent}.", { agent: label })
      : shown ? t("This conversation is no longer one you can read.")
        : emptyLine(info, label, where, place, page), again: false,
    // this computer offers no directory: Settings → AI → Agents is where one is offered
    offer: !shown && !remote && !!place?.pick && place.options.length < 2 } : null;

  // The composer's model chip (chat/AgentChip.jsx): where the agent works
  // (`place`, while there is a choice: the conversation's once it has a
  // turn, shown and not to change), its models and its efforts as
  // sections, the choice the conversation's.
  const efforts = choice.shown?.efforts || [];
  const chip = {
    label: t("{agent}'s model", { agent: label }), text: modelDisplay(choice),
    models: models.map((m) => [m.id, m.label || m.id]), model: choice.model || choice.shown?.id || "",
    onModel: (model) => pickModel({ model, effort: choice.effort }),
    place: place?.options.length > 1 ? {
      label: t("Works in"), value: place.value, onChange: pickPlace, options: place.options.map(([value, name]) => [value, name, FolderIcon]),
      ...(place.pick ? {} : { disabled: true, title: t("Start a new chat to work elsewhere") }),
    } : null,
    sections: efforts.length ? [{
      label: t("Reasoning effort"), value: choice.effort, onChange: (effort) => pickModel({ model: choice.model, effort }),
      options: [["", choice.shown.effort ? t("Default ({effort})", { effort: choice.shown.effort }) : t("Default")],
        ...efforts.map((ef) => [ef, ef])],
    }] : [],
  };
  // The commands allowed in this chat (Gamma AI's GrantChip), null for none.
  const allowed = (chat.computer?.grants || []).map((g) => g.command).join(", ");
  const grants = allowed ? {
    list: allowed, onRevoke: chat.revokeGrants, disabled: chat.busy,
    title: t("{agent} runs these commands without asking in this chat: {list}. Click to make it ask again.", { agent: label, list: allowed }),
  } : null;

  return {
    chat, info, label, where, messages, stopId, working: !!stopId, ready, send, retry, chip, grants,
    place, pickPlace, sources, links, empty, shown,
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
