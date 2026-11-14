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
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { AutoGrowTextarea, ChatMarkdown, useCopied } from "../../shared/ui/Widgets";
import { IconSwap } from "../../shared/ui/MotionWidgets";
import { ContextMenu, MenuButton, MenuItem, MenuSelect, useMenuTrigger } from "../../shared/ui/Menus";
import { Popover } from "../../shared/ui/Surfaces";
import { menuPress } from "../../shared/ui/press.js";
import { copyText } from "../../shared/lib/utils";
import { Segmented } from "../../settings/SettingsKit";
import { formatRelativeTime } from "../../library/libraryUtils.js";
import { sendsOnEnter } from "../enterKey.js";
import { SelChip } from "../ChatDock";
import StepsPill from "../StepsPill";
import { askShell } from "../../vault/useVault.js";
import ComputerAsk from "./ComputerAsk.jsx";
import { usePassageSources } from "./useComputerChat.js";
import {
  ActivityIcon, AlertCircleIcon, ArrowUpIcon, BotIcon, CheckIcon, CopyIcon, FileTextIcon, FolderOpenIcon, HistoryIcon, MoreIcon, PencilIcon,
  PlusIcon, QuoteIcon, SearchIcon, SparklesIcon, StopIcon, TerminalIcon, TrashIcon,
} from "../../shared/ui/Icons";
import { T, t } from "../../shared/i18n/i18n.js";
import {
  TEXT_MAX, awayFrom, canContinue, canSend, changesLine, endText, freshLine, isLive, isRemote, lastEnded, liveTurn, modelChoice, modelDisplay,
  offersMenu, sourceNote, stepText, stripLine, turnLabel,
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
// is its icon alone (Segmented).
export function ChatSides({ side, onSide, label, where = "" }) {
  return (
    <Segmented value={side} onChange={onSide} options={[
      ["ai", t("Gamma AI"), SparklesIcon, t("Chat with Gamma AI")],
      ["computer", label, BotIcon, where
        ? t("Chat with {agent} on {computer}, in this work folder's directory there", { agent: label, computer: where })
        : t("Chat with {agent} on this computer, in this work folder's directory", { agent: label })],
    ]} />
  );
}

// The header's actions on this side (DockWindow's `actions`): History (the
// page's earlier conversations with the agent, opened or deleted) and New
// chat, which keeps the conversation in History. Neither while a turn is
// still to end: the server refuses them then.
export function computerActions({ chat, label, openPopover, setOpenPopover, askConfirm }) {
  const historyOpen = openPopover === "computerhistory";
  const working = !!liveTurn(chat.conversation) || !!chat.computer?.turn;
  return [
    { id: "history", icon: HistoryIcon, label: t("Chat history"), open: historyOpen,
      title: t("Chat history — earlier conversations with {agent} on this page", { agent: label }),
      onClick: () => setOpenPopover((p) => (p === "computerhistory" ? null : "computerhistory")),
      popover: { name: "computerhistory", content: historyOpen
        ? <ComputerHistory chat={chat} working={working} onOpen={() => setOpenPopover(null)} askConfirm={askConfirm} /> : null } },
    { id: "new", icon: PlusIcon, label: t("New chat"), onClick: () => chat.newChat(),
      disabled: working || chat.busy || !chat.conversation,
      title: t("New chat — keeps this conversation in history and starts a fresh one") },
  ];
}

// History's popover: the conversation shown now, then the earlier ones,
// read as it opens; a row's menu deletes it.
function ComputerHistory({ chat, working, onOpen, askConfirm }) {
  const [rowMenu, setRowMenu] = useState(null); // {x, y, row}: a history row's menu
  const { history, readHistory } = chat;
  useEffect(() => { if (history == null) readHistory(); }, [history, readHistory]);
  const active = chat.conversation ? [{ id: "", title: chat.conversation.title, active: true }] : [];
  const rows = [...active, ...(history || [])];
  const openRow = (row) => {
    if (row.active || working) return;
    onOpen();
    chat.openHistory(row.id);
  };
  const deleteRow = (row) => askConfirm({
    title: T("Delete conversation"),
    message: t("Delete “{title}” from this chat's history? This can't be undone.", { title: row.title || t("Untitled") }),
    confirmLabel: t("Delete"), danger: true, onConfirm: () => chat.deleteHistory(row.id),
  });
  const openRowMenu = (row) => (e) => { e.preventDefault(); setRowMenu({ x: e.clientX, y: e.clientY, row }); };
  return (
    <Popover className="chatHistoryPop">
      <div className="chatHistList">
        {rows.map((row) => (
          <div key={row.id} className={`chatHistRow rowMenuHost${row.active ? " active" : ""}`}
            role="button" tabIndex={0} aria-disabled={!row.active && working ? true : undefined}
            title={row.active ? t("The conversation shown now") : row.title}
            onClick={() => openRow(row)}
            onKeyDown={(e) => { if (e.key === "Enter" && e.target === e.currentTarget) openRow(row); }}
            {...(row.active ? {} : menuPress(openRowMenu(row)))}>
            <span className="chatHistTitle">{row.title || t("Untitled")}</span>
            <span className="chatHistAge">{row.active ? t("now") : formatRelativeTime(row.updated_at)}</span>
            {row.active ? null : <MenuButton open={openRowMenu(row)} />}
          </div>
        ))}
        {rowMenu ? (
          <ContextMenu x={rowMenu.x} y={rowMenu.y} onClose={() => setRowMenu(null)}>
            <MenuItem icon={TrashIcon} danger onClick={() => { setRowMenu(null); deleteRow(rowMenu.row); }}>{t("Delete")}</MenuItem>
          </ContextMenu>
        ) : null}
        {history == null ? <div className="popoverHint loadingHint">{t("Loading…")}</div>
          : !history.length ? <div className="popoverHint">{t("No earlier conversations — New chat keeps the current one here.")}</div>
          : null}
      </div>
    </Popover>
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

// The agent's message: the turn's steps in one pill that unfolds to each
// (StepsPill, as Gamma AI's replies), a card for each thing it asks and
// waits on, the reply, what changed, and how the turn ended when it did
// not answer. A turn that had ended when it was first shown (a
// conversation opened from History, read again) is `still`: its pill is
// not read out.
function AgentMessage({ message, label, away, copied, onCopy, onAnswer }) {
  const [open, setOpen] = useState(false);
  const live = isLive(message);
  const [still] = useState(!live);
  const steps = message.steps || [];
  const counts = {
    failed: steps.filter((s) => s.status === "failed").length,
    declined: steps.filter((s) => s.status === "declined").length,
  };
  const changes = changesLine(message);
  const ended = endText(message);
  return (
    <div className="chatBubbleRow ai">
      <div className="chatMsgCol">
        <div className="chatBubble ai">
          {message.fresh ? <div className="computerFresh">{freshLine(message.label || label)}</div> : null}
          <StepsPill label={turnLabel(message, label, away)} live={live} waiting={live && !!message.asks?.length} still={still}
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
          {live ? (message.asks || []).map((ask) => (
            <ComputerAsk key={ask.id} ask={ask} label={message.label || label}
              onAnswer={(decision, note) => onAnswer(message.turn_id, ask.id, decision, note)} />
          )) : null}
          {message.text ? <ChatMarkdown text={message.text} copyBlocks /> : null}
          {changes ? <div className="computerChanges">{changes}</div> : null}
          {ended ? (
            <div className={`computerEnded${message.state === "failed" ? " err" : ""}`}>
              {message.state === "failed" ? <AlertCircleIcon size={14} /> : <StopIcon size={14} />}
              <span>{ended}</span>
            </div>
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
  const ready = canSend(info, conversation, draft) && !chat.busy;
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
        {!messages.length && chat.read ? (
          <div className="chatEmpty">
            {where
              ? t("Ask {agent} about this file or to change it: it works in the work folder's directory on {computer}.", { agent: label, computer: where })
              : t("Ask {agent} about this file or to change it: it works in the work folder's directory on this computer.", { agent: label })}
          </div>
        ) : null}
        {messages.map((m, i) => (m.role === "user"
          ? <PersonMessage key={m.id || i} message={m} />
          : <AgentMessage key={m.id || i} message={m} label={label} away={away} copied={copied === i} onAnswer={chat.answer}
              onCopy={async () => { if (await copyText(m.text || "")) flashCopied(i); }} />))}
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
