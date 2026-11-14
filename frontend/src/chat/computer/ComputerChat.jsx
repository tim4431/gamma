// The chat's computer side (docs/dev/local_agents.md "The chat's computer
// side"): on a work folder's file whose vault this account made, with an
// agent on, the chat window talks to that agent on this computer instead
// of Gamma AI (on a work folder of another server, in any window of its
// maker, to the agent on the computer that keeps it). ChatDock draws the
// switch between the two first in its header (ChatSides) and, on this
// side, the side's History and New chat
// (computerActions) and its body (ComputerChat): the strip naming the agent
// and what it is at, the transcript, and a composer that sends the message
// with the PDF passages selected, their source lines noted, and the agent's
// model chip. The strip's menu asks the desktop shell to continue the
// conversation in a terminal or to show the file in its folder. The state
// is useComputerChat's, the words computerChat.js's.
import { useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { API, apiJson, copyText } from "../../shared/lib/utils";
import { AutoGrowTextarea, ChatMarkdown, GammaNavContext, useCopied } from "../../shared/ui/Widgets";
import { IconSwap } from "../../shared/ui/MotionWidgets";
import { ContextMenu, MenuItem, MenuSelect, useMenuTrigger } from "../../shared/ui/Menus";
import { Segmented } from "../../settings/SettingsKit";
import { PathLink } from "../../editor/PathLink.jsx";
import { PathLinkContext } from "../../editor/pathLinkContext.js";
import { sendsOnEnter } from "../enterKey.js";
import { SelChip } from "../ChatDock";
import StepsPill from "../StepsPill";
import ChatErrorCard from "../ChatErrorCard";
import ChatHistoryPop, { deleteQuestion, matchRows } from "../ChatHistoryPop";
import GrantChip from "../GrantChip";
import { ChangesCard } from "../AgentChanges";
import { askShell } from "../../vault/useVault.js";
import ComputerAsk from "./ComputerAsk.jsx";
import { usePassageSources } from "./useComputerChat.js";
import {
  ActivityIcon, ArrowUpIcon, BotIcon, CheckIcon, CopyIcon, FileTextIcon, FolderOpenIcon, HistoryIcon, MoreIcon, PencilIcon,
  PlusIcon, QuoteIcon, SearchIcon, SparklesIcon, StopIcon, TerminalIcon, TrashIcon,
} from "../../shared/ui/Icons";
import { t, tn } from "../../shared/i18n/i18n.js";
import {
  TEXT_MAX, awayFrom, canContinue, canSend, drawsAsks, endText, freshLine, isLive, isRemote, lastEnded, liveTurn, modelChoice, modelDisplay,
  offersMenu, retryOf, sourceNote, stepText, stripLine, turnChanges, turnLabel,
} from "./computerChat.js";
import "./computer.css";

// A step's icon, by its kind (never the Gamma AI chips' ACTION_ICONS: these
// are files of the folder, not notes or pages).
const STEP_ICONS = { read: FileTextIcon, edit: PencilIcon, run: TerminalIcon, search: SearchIcon };

// What each page's composer holds, kept while the side is switched away
// and back, or the dock mounted again.
const drafts = new Map();
// The model and effort chosen for each conversation in this tab, by the
// page and the conversation's id ("" before its first turn); a turn's
// message keeps what it ran with, so a reopened conversation shows it.
const picks = new Map();
const pickKey = (pageId, conversation) => `${pageId}\n${conversation?.id || ""}`;

// The strip's menu (the chat header's menu look), the desktop shell's to
// do: Continue in a terminal, the conversation's session resumed in the
// folder where the person's own agent settings apply, while it has one;
// and Show in folder. The page only asks: the shell reads the session and
// the folder from its own server, never a command from the page.
function StripMenu({ pageId, label, session }) {
  const menus = useMenuTrigger();
  const ask = (type) => { menus.close(); askShell(type, { page: pageId }); };
  const title = t("More");
  return (
    <>
      <button type="button" className="ctlBtn computerStripMenu" title={title} aria-label={title} {...menus.trigger("strip")}>
        <MoreIcon size={16} />
      </button>
      {menus.menu ? (
        <ContextMenu {...menus.menu}>
          {session ? (
            <MenuItem icon={TerminalIcon} onClick={() => ask("continue-in-terminal")}
              title={t("Opens a terminal in the work folder with this conversation's {agent} session, under your own {agent} settings", { agent: label })}>
              {t("Continue in a terminal")}
            </MenuItem>
          ) : null}
          <MenuItem icon={FolderOpenIcon} onClick={() => ask("reveal-on-disk")}>{t("Show in folder")}</MenuItem>
        </ContextMenu>
      ) : null}
    </>
  );
}

// The composer's model chip on this side: the agent's own models (the
// server's `computer.models`), headed by its name, with the chosen model's
// efforts as a section; none chosen is the agent's default.
function ModelChip({ label, models, choice, onPick }) {
  const efforts = choice.shown?.efforts || [];
  return (
    <span className="chatModelChip">
      <MenuSelect
        up
        label={t("{agent}'s model", { agent: label })}
        heading={label}
        value={choice.model || choice.shown?.id || ""}
        onChange={(model) => onPick({ model, effort: choice.effort })}
        display={<span className="chatModelName">{modelDisplay(choice)}</span>}
        options={models.map((m) => [m.id, m.label || m.id])}
        sections={efforts.length ? [{
          label: t("Reasoning effort"),
          value: choice.effort,
          onChange: (effort) => onPick({ model: choice.model, effort }),
          options: [["", choice.shown?.effort ? t("Default ({effort})", { effort: choice.shown.effort }) : t("Default")],
            ...efforts.map((ef) => [ef, ef])],
        }] : []}
      />
    </span>
  );
}

// The switch first in the chat's header: Gamma AI or the agent on this
// computer (`label`, "Codex"), or on the computer that keeps the work
// folder (`where`, its name, "" for this one). In a narrow window each side
// is its icon alone (Segmented). `mark` (computerChat.js sideMark): the
// agent's side spins while it works and has the accent dot while it waits
// for the person, said in its title, so either side shows it.
export function ChatSides({ side, onSide, label, where = "", mark = null }) {
  const tip = where
    ? t("Chat with {agent} on {computer}, in this work folder's directory there", { agent: label, computer: where })
    : t("Chat with {agent} on this computer, in this work folder's directory", { agent: label });
  const sign = mark?.kind === "working" ? <span className="transferSpin inline" aria-hidden="true" />
    : mark?.kind === "waiting" ? <span className="noticeDot info inline" aria-hidden="true" /> : null;
  return (
    <Segmented value={side} onChange={onSide} options={[
      ["ai", t("Gamma AI"), SparklesIcon, t("Chat with Gamma AI")],
      ["computer", label, BotIcon, mark?.text ? `${tip}\n${mark.text}` : tip, sign],
    ]} />
  );
}

// The header's actions on this side (DockWindow's `actions`): History (the
// page's earlier conversations with the agent, opened or deleted) and New
// chat, which keeps the conversation in History. Neither while a turn is
// still to end: the server refuses them then, and their titles say why.
export function computerActions({ chat, label, openPopover, setOpenPopover, askConfirm }) {
  const historyOpen = openPopover === "computerhistory";
  const working = !!liveTurn(chat.conversation) || !!chat.computer?.turn;
  return [
    { id: "history", icon: HistoryIcon, label: t("Chat history"), open: historyOpen,
      title: t("Chat history — earlier conversations with {agent} on this page", { agent: label }),
      onClick: () => setOpenPopover((p) => (p === "computerhistory" ? null : "computerhistory")),
      popover: { name: "computerhistory", content: historyOpen ? (
        <ComputerHistory chat={chat} onClose={() => setOpenPopover(null)} askConfirm={askConfirm}
          disabledReason={working ? t("Stop {agent} or wait for it to finish to open another conversation", { agent: label }) : ""} />
      ) : null } },
    { id: "new", icon: PlusIcon, label: t("New chat"), onClick: () => chat.newChat(),
      disabled: working || chat.busy || !chat.conversation,
      title: working ? t("Stop {agent} or wait for it to finish to start a new chat", { agent: label })
        : t("New chat — keeps this conversation in history and starts a fresh one") },
  ];
}

// History's popover (the chat's own, ChatHistoryPop): the conversation
// shown now, then the earlier ones, read as it opens; a row's menu deletes
// it.
function ComputerHistory({ chat, onClose, askConfirm, disabledReason }) {
  const [query, setQuery] = useState("");
  const { history, readHistory } = chat;
  useEffect(() => { if (history == null) readHistory(); }, [history, readHistory]);
  const active = chat.conversation ? [{ id: "", title: chat.conversation.title, active: true }] : [];
  return (
    <ChatHistoryPop query={query} onQuery={setQuery} onClose={onClose} loading={history == null}
      rows={matchRows([...active, ...(history || [])], query)} disabledReason={disabledReason}
      onOpen={(row) => { onClose(); chat.openHistory(row.id); }}
      menuItems={(row, close) => (row.active ? null : (
        <MenuItem icon={TrashIcon} danger onClick={() => {
          close();
          askConfirm(deleteQuestion(row, () => chat.deleteHistory(row.id)));
        }}>{t("Delete")}</MenuItem>
      ))} />
  );
}

// A person's message: its text, under the passages it carried.
function PersonMessage({ message }) {
  return (
    <div className="chatBubbleRow user">
      <div className="chatMsgCol">
        <div className="chatBubble user">
          {message.selections?.length ? (
            <div className="chatMsgPdfs">
              {message.selections.map((s, j) => (
                <span key={j} className="chatPdfChip" title={s.text}>
                  <QuoteIcon size={14} />
                  {s.page ? t("p. {page}", { page: s.page }) : t("PDF passage")}
                </span>
              ))}
            </div>
          ) : null}
          <div className="chatUserText">{message.text}</div>
        </div>
      </div>
    </div>
  );
}

// What a turn changed, in the card Gamma AI lists a reply's changes in
// (AgentChanges' ChangesCard): a row a file, each its path as the link that
// opens it (PathLink), the first few and then "and N more" unfolding to the
// rest, and "The PDF was rebuilt" last.
function TurnChanges({ changes }) {
  const { shown, rest, more, n, built } = changes;
  const file = (path) => <PathLink href={path}>{path}</PathLink>;
  const row = (key, icon, body) => (
    <div key={key} className="chatChange">{icon}<span className="chatChangeText">{body}</span></div>
  );
  const edited = <PencilIcon size={14} />;
  return (
    <ChangesCard className="computerChanges" head={n ? t("Changed in the work folder · {n}", { n }) : t("Changed in the work folder")}>
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

// The agent's message: the turn's steps in one pill that unfolds to each
// (StepsPill, as Gamma AI's replies), a card for each thing it asks and
// waits on (`asks`: for the agent on this computer only; another
// computer's, `computer` its name, are answered there), the reply, what
// changed, and how the turn ended when it did not answer: a failed turn
// the chat's error card, with Retry while it is
// the newest (`onRetry`), a stopped one a quiet line. A turn that had
// ended when it was first shown (a conversation opened from History, read
// again) is `still`: its pill is not read out.
function AgentMessage({ message, label, away, computer, asks, copied, onCopy, onAnswer, onRetry }) {
  const [open, setOpen] = useState(false);
  const live = isLive(message);
  const [still] = useState(!live);
  const steps = message.steps || [];
  const counts = {
    failed: steps.filter((s) => s.status === "failed").length,
    declined: steps.filter((s) => s.status === "declined").length,
  };
  const changes = turnChanges(message);
  const ended = endText(message);
  return (
    <div className="chatBubbleRow ai">
      <div className="chatMsgCol">
        <div className="chatBubble ai">
          {message.fresh ? <div className="computerFresh">{freshLine(message.label || label)}</div> : null}
          <StepsPill label={turnLabel(message, label, away, computer)} live={live}
            waiting={live && (!!message.asks?.length || message.waiting === true)} still={still}
            steps={steps.length} counts={counts} title={t("Show every step")} open={open} onToggle={() => setOpen((v) => !v)}>
            <div className="chatToolActions">
              {steps.map((step, j) => {
                const Icon = STEP_ICONS[step.kind] || ActivityIcon;
                return (
                  <div key={step.id || j} className={`chatToolActionHead plain${step.status === "failed" ? " err" : ""}`} title={stepText(step)}>
                    {step.status === "started" && live ? <span className="transferSpin inline" aria-hidden="true" /> : <Icon size={14} />}
                    <span>{stepText(step)}</span>
                  </div>
                );
              })}
            </div>
          </StepsPill>
          {live && asks ? (message.asks || []).map((ask) => (
            <ComputerAsk key={ask.id} ask={ask} label={message.label || label}
              onAnswer={(decision, note) => onAnswer(message.turn_id, ask.id, decision, note)} />
          )) : null}
          {message.text ? <ChatMarkdown text={message.text} copyBlocks /> : null}
          {changes ? <TurnChanges changes={changes} /> : null}
          {ended && message.state === "failed" ? (
            <ChatErrorCard headline={ended} actions={onRetry ? [
              <button key="retry" type="button" className="uiBtn sm" onClick={onRetry}>{t("Retry")}</button>,
            ] : []} />
          ) : ended ? (
            <div className="computerEnded"><StopIcon size={14} /><span>{ended}</span></div>
          ) : null}
        </div>
        {!live && message.text ? (
          <div className="chatMsgFoot">
            <div className="chatMsgActions">
              <button type="button" className="chatMsgActionBtn" title={t("Copy message")} onClick={onCopy}>
                <IconSwap swap={copied} from={<CopyIcon size={14} />} to={<CheckIcon size={14} />} />
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

// The side's body for the page `pageId` (ChatDock keys it by the page):
// `computer` the page's agent as the vault says (vaultState
// computerAgent), or as the page's computers say (keeperComputer), until
// the side's own read names it; the PDF passages selected are App's, the
// Gamma AI side's too, and go with the next message. Another computer's
// agent (`remote`) is named by that computer, and the strip offers no menu
// and the passages no source lines: they need the window to be it.
export default function ComputerChat({ chat, computer, pageId, pdfSelections, setPdfSelections }) {
  const info = chat.computer || computer;
  const label = info?.label || computer?.label || "";
  const remote = isRemote(info);
  const where = remote ? String(info.computer || "") : "";
  const away = awayFrom(info);
  const conversation = chat.conversation;
  const messages = conversation?.messages || [];
  const live = liveTurn(conversation);
  const strip = stripLine(info, live);
  const [draft, setDraft] = useState(() => drafts.get(pageId) || "");
  useEffect(() => { drafts.set(pageId, draft); }, [pageId, draft]);
  const [copied, flashCopied] = useCopied(1200);
  // Off until the conversation was read: one that failed may hold a turn.
  const landed = chat.read && !chat.error;
  const ready = landed && canSend(info, conversation, draft) && !chat.busy;
  const models = chat.computer?.models || [];
  const [, redrawPick] = useState(0); // `picks` keeps the choice
  const choice = modelChoice(models, conversation, picks.get(pickKey(pageId, conversation)) || null);
  const pick = (next) => {
    picks.set(pickKey(pageId, conversation), next);
    redrawPick((n) => n + 1);
  };
  const sources = usePassageSources(remote ? "" : pageId, pdfSelections, lastEnded(conversation));
  // The turn Stop stops: the conversation's own, or the one the server
  // says is queued for it before the conversation shows it.
  const stopId = live?.turn_id || chat.computer?.turn?.turn_id || "";

  async function send() {
    if (!ready) return;
    const text = draft.slice(0, TEXT_MAX);
    const before = pickKey(pageId, conversation);
    if (await chat.send(text, pdfSelections, choice)) {
      setDraft("");
      setPdfSelections([]);
      // A first turn's pick is its message's now, and a new chat starts
      // from the agent's default.
      if (!conversation) picks.delete(before);
    }
  }

  // Retry on a failed turn, the newest: the person's message again, with
  // its passages, while a message could go.
  const retry = (message) => {
    const again = retryOf(conversation, message);
    if (!again || !landed || chat.busy || !canSend(info, conversation, again.text)) return null;
    return () => chat.send(again.text, again.selections, choice);
  };
  const grants = chat.computer?.grants || [];

  // A changed file's link opens it (PathLink): its path is the work
  // folder's (the server's `folder_id`), resolved from the folder's top.
  const nav = useContext(GammaNavContext);
  const folderId = chat.computer?.folder_id || "";
  const { say } = chat;
  const pathLinks = useMemo(() => (folderId && nav?.openPage ? {
    pageId,
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
  } : null), [folderId, nav, pageId, say]);

  // Follow the newest message while the transcript is scrolled to its end.
  const scrollRef = useRef(null);
  const stuck = useRef(true);
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el && stuck.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  return (
    <div className="chatPanel chatWindow">
      {/* A plain line, not a live region: the turn's pill in the transcript
          is the one read out as the agent works. */}
      {strip.text ? (
        <div className={`chatHealthStrip computerStrip ${strip.kind}`}>
          {strip.kind === "working" || strip.kind === "waiting" ? <span className="transferSpin inline" aria-hidden="true" /> : null}
          <span className="chatHealthText">{strip.text}</span>
          {offersMenu(info) ? <StripMenu pageId={pageId} label={label} session={canContinue(info, conversation)} /> : null}
        </div>
      ) : null}
      <div className="chatMessages" ref={scrollRef}
        onScroll={(e) => { const el = e.currentTarget; stuck.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40; }}>
        {!messages.length && chat.error ? (
          <div className="chatEmpty">
            {chat.error}
            <div className="computerAgain"><button type="button" className="uiBtn sm" disabled={chat.busy} onClick={chat.reload}>{t("Try again")}</button></div>
          </div>
        ) : !messages.length && chat.read ? (
          <div className="chatEmpty">
            {where
              ? t("Ask {agent} about this file or to change it: it works in the work folder's directory on {computer}.", { agent: label, computer: where })
              : t("Ask {agent} about this file or to change it: it works in the work folder's directory on this computer.", { agent: label })}
          </div>
        ) : null}
        <PathLinkContext.Provider value={pathLinks}>
          {messages.map((m, i) => (m.role === "user"
            ? <PersonMessage key={m.id || i} message={m} />
            : <AgentMessage key={m.id || i} message={m} label={label} away={away} computer={where} asks={drawsAsks(info)}
                copied={copied === i} onAnswer={chat.answer}
                onRetry={retry(m)} onCopy={async () => { if (await copyText(m.text || "")) flashCopied(i); }} />))}
        </PathLinkContext.Provider>
      </div>
      <form className="chatComposer" onSubmit={(e) => { e.preventDefault(); send(); }}
        onMouseDown={(e) => { if (e.target.closest("button")) e.preventDefault(); }}>
        {pdfSelections.length ? (
          <div className="chatSelChips">
            {pdfSelections.map((s, i) => (
              <SelChip key={`p${i}`} text={s.text} icon="passage"
                label={pdfSelections.length > 1 ? t("PDF passage {i}", { i: i + 1 }) : t("PDF passage")}
                note={sourceNote(sources[i]) || (s.page ? t("p. {page}", { page: s.page }) : "")}
                title={[s.text.slice(0, 2000), t("Goes to {agent} with the message", { agent: label })].join("\n\n")}
                onRemove={() => setPdfSelections((prev) => prev.filter((_, j) => j !== i))}
                removeTitle={t("Remove this passage")} />
            ))}
          </div>
        ) : null}
        <AutoGrowTextarea className="chatInput chatInputArea" rows={1} value={draft} maxLength={TEXT_MAX}
          aria-label={t("Message {agent}", { agent: label })}
          placeholder={where ? t("Message {agent} on {computer}…", { agent: label, computer: where }) : t("Message {agent} on this computer…", { agent: label })}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (sendsOnEnter(e)) { e.preventDefault(); send(); } }} />
        <div className="chatComposerBar">
          <span className="chatComposerSpacer" />
          {grants.length ? (
            <GrantChip list={grants.map((g) => g.command).join(", ")} onRevoke={chat.revokeGrants} disabled={chat.busy}
              title={t("{agent} runs these commands without asking in this chat: {list}. Click to make it ask again.",
                { agent: label, list: grants.map((g) => g.command).join(", ") })} />
          ) : null}
          {models.length ? <ModelChip label={label} models={models} choice={choice} onPick={pick} /> : null}
          {stopId ? (
            <button className="uiBtn chatCircleBtn chatStopBtn" type="button" onClick={() => chat.stop(stopId)}
              title={t("Stop {agent}", { agent: label })} aria-label={t("Stop {agent}", { agent: label })}>
              <StopIcon size={14} />
            </button>
          ) : (
            <button className="uiBtn primary chatCircleBtn" type="submit" disabled={!ready} title={t("Send")} aria-label={t("Send")}>
              <ArrowUpIcon size={14} />
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
