// The AI chat window, self-contained: per-page conversation state (loaded/
// saved on the backend), ChatGPT-style message actions (copy/edit/find/stop),
// pasted figures, the "+" context picker, and the per-message PDF attach.
// App provides context (open paper, library, selections) and the model/effort/
// prompt preferences it also needs elsewhere. The agent picked in the
// composer's chip is Gamma AI or a computer's (docs/dev/ai.md "The picker"):
// one transcript (ChatTranscript), one composer and one History draw both,
// a computer agent's conversation read through chat/computer/.
import React, { useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { API, apiJson, copyText, downloadFetched, isPdfFile, makeId, readNdjson } from "../shared/lib/utils";
import { DEFAULT_PICTURE_BUDGET, addPictures, historyPictures, isPictureFile, pictureAlt, pictureUrl } from "./chatPictures.js";
import { pictureSrc } from "./pictureSrc.js";
import { postFile } from "../transfers/FileChip";
import { stepList } from "../shared/ui/listKeys.js";
import { DockWindow, GammaNavContext, useCopied, useTextScale } from "../shared/ui/Widgets";
import { IconSwap } from "../shared/ui/MotionWidgets";
import PaperMentionInput from "./PaperMentionInput";
import ChatTranscript, { SpeedGlyph, UsageLine, knobsLabel } from "./ChatTranscript";
import ChatHistoryPop, { deleteQuestion, matchRows } from "./ChatHistoryPop";
import GrantChip from "./GrantChip";
import { refusalCopy, sendRevert } from "./AgentChanges";
import RevertChanges from "./RevertChanges";
import { forReplay, markReverted, revertRefusal, revertible } from "./aiRevert.js";
import { noteReview } from "./noteReview.js";
import { GRANTS_KEY, conversationId, grantsIn, readGrants, waitsForPapers, withGrant,
  withoutGrants, withoutPaperWait, writeGrants } from "./approvals.js";
import { MAX_CHAT_REFERENCES } from "./paperMentions";
import { READ_TOOLS, WRITE_TOOLS, permState, toolsForKind } from "./chatSettings";
import { addUsage, contextUsed, conversationUsage, fmtTokens, usageDetail } from "./tokenUsage";
import { charCount, keptNote, keptTitle, passageShares } from "./passageBudget.js";
import { recencySections } from "../library/PageOption";
import { rankLibraryPages } from "../library/librarySearch";
import { filedIn, topPageOf } from "../library/libraryUtils";
import { fileHash, isPdf, pageDocument, pdfFileHash } from "../documents/pageDocument.js";
import { ActionMenu, MenuItem } from "../shared/ui/Menus";
import { backdropPress } from "../shared/ui/press.js";
import { useAltVariant } from "../shared/ui/altVariant.js";
import { touchTyping } from "../shared/lib/pointer.js";
import { pickFiles } from "../shared/lib/pickFiles.js";
import { composing } from "../shared/lib/hotkeys.js";
import { chatFailure, failureCopy, failureFields, fixLabel } from "./chatErrors";
import { BLOCK_TOOLS, changePlace, isChange, withHelper } from "./agentSteps";
import { guideEvents } from "../guide/events.js";
import { gammaLinksIn } from "../shared/model/gammaLinks.js";
import { linesLabel } from "../shared/model/blockModel.js";
import { CharSlider, Toggle, approxPages } from "../settings/SettingsKit";
import { AgentToolPicker, changePermission, chatKindName, permissionLabel } from "../settings/AssistantTools";
import { aiServiceTiles } from "../settings/providerEditor.js";
import { renderKatex } from "../editor/LatexEditor";
import { PathLinkContext } from "../editor/pathLinkContext.js";
import FileThemeIcon from "../shared/ui/FileIcon.jsx";
import { formatOf } from "../documents/formats.js";
import { chipSegments } from "./chipText";
import { effortFor, modelLabel, providerModels, speedFor } from "./modelPrefs";
import { AlertCircleIcon, ArrowLeftIcon, ArrowUpIcon, BanIcon, BookIcon, BotIcon, ChevronDownIcon, ChevronUpIcon, DownloadIcon, EyeIcon, FileIcon,
  HighlightIcon, HistoryIcon, MessageSquareIcon, MicIcon, OutlineIcon, PaperclipIcon, PenIcon, PlusIcon, QuoteIcon, SearchIcon,
  SettingsIcon, SparklesIcon, SquareCheckIcon, StopIcon, TextCursorIcon, TrashIcon, XIcon } from "../shared/ui/Icons";
import { T, getLocale, t, tn } from "../shared/i18n/i18n.js";
import { DialogOverlay, Popover } from "../shared/ui/Surfaces";
import { UsageRing } from "../shared/ui/UsageRing";
import { showInChat, useChatAgent, useChatShown } from "./computer/chatSide.js";
import {
  computerExportPath, computerHistory, computerSessions, deleteComputerChat, newComputerChat, openComputerChat,
  openComputerSession, useComputerSide,
} from "./computer/useComputerChat.js";
import { historyRows, sessionRows, sessionsAway, sessionsDir, sessionsWords, sourceNote } from "./computer/computerChat.js";
import ComputerStrip from "./computer/ComputerStrip.jsx";
import AgentChip, { agentIcon } from "./AgentChip.jsx";
import { GAMMA, agentChoice, agentLabel, asksNewChat, gammaAgent, newChatQuestion, pickAgent } from "./agents.js";
import { canRecord, createRecorder, gestureContext } from "../recording/recorder.js";
import { extensionFor } from "../recording/recording.js";

const JSON_HEADERS = { "Content-Type": "application/json" };
// A conversation this tab has nothing of yet (one array, never written).
const NO_MESSAGES = Object.freeze([]);
const NO_DIRS = Object.freeze([]);

// What a remount (an iPad turning, the dock closed and opened again) must
// not lose: the composer's draft, with the conversation it was typed in, and
// the id of the last ask from App already sent. One dock is mounted at a time.
const kept = { key: "", draft: "", asked: 0 };

// A conversation's display name when the user never named it: the first
// user message's first non-quote line (mirrors derive_title in
// gamma/routers/chats.py, which names entries when they are archived).
function deriveTitle(messages) {
  const first = (messages || []).find((m) => m.role === "user");
  const line = (first?.text || "").split("\n").map((l) => l.trim()).find((l) => l && !l.startsWith(">"));
  return (line || "").replace(/\s+/g, " ").slice(0, 80);
}

// The History row for a conversation the server just filed under `id` (the
// archive and the open answer with it), as the list's next read gives it.
function filedRow(id, { title, messages }) {
  const first = messages.find((m) => m.role === "user");
  return {
    id, title: title || deriveTitle(messages), preview: (first?.text || "").replace(/\s+/g, " ").trim().slice(0, 120),
    count: messages.length, updated_at: new Date().toISOString(),
  };
}

// What the server knows about a chat model, asked once per model per page
// load (GET /api/ai/model-info): its context window in tokens, the
// reasoning-effort levels it takes and the service tiers it can run at,
// read from the provider's own model listing, else the public models.dev
// catalog. Null while unknown, and so is any field no source knows —
// nothing is guessed from the name.
const modelInfos = new Map(); // model id -> Promise<{context_window, efforts, speeds, images} | null>
// A reply's thinking, round by round ({"reasoning"} lines: {wire field:
// text}). It is kept with the reply and sent back with it on later turns,
// where a thinking model wants it (DeepSeek refuses tools without it).
function addReasoning(kept, round) {
  const out = { ...(kept || {}) };
  for (const [field, text] of Object.entries(round || {})) {
    if (typeof text === "string" && text) out[field] = out[field] ? `${out[field]}\n\n${text}` : text;
  }
  return out;
}

function useModelInfo(modelId) {
  const [known, setKnown] = useState({ id: "", info: null });
  useEffect(() => {
    if (!modelId) return undefined;
    if (!modelInfos.has(modelId)) {
      modelInfos.set(modelId, apiJson(`${API}/ai/model-info?model=${encodeURIComponent(modelId)}`)
        .catch(() => { modelInfos.delete(modelId); return null; }));
    }
    let live = true;
    modelInfos.get(modelId).then((info) => { if (live) setKnown({ id: modelId, info }); });
    return () => { live = false; };
  }, [modelId]);
  return known.id === modelId ? known.info : null;
}

// One chip in the composer's strip: a PDF passage, the cursor block, an
// attached block, selected note text or selected handwriting (its label
// alone, "Selected handwriting · 4 strokes"). Two lines: what it is, in words
// (`label`, plus a muted `note` — the PDF page, a passage's source line
// with its file's icon (`file`, its path in the synced folder), or how to
// leave an automatic one out — and `cut`, what the message keeps of a passage too long to go
// whole), over a plain-text preview of the text (chat/chipText.js: the
// markdown dropped, inline math typeset). `auto` marks context nobody
// attached — it rides along because of where the cursor is — with a dashed
// border; `kind` is the modifier class (isCursor / isBlock / isNote /
// isInk; none for a PDF passage).
const SEL_CHIP_ICONS = { cursor: TextCursorIcon, selection: HighlightIcon, passage: QuoteIcon, block: OutlineIcon, ink: PenIcon };

function ChipPreview({ text }) {
  const segments = useMemo(() => chipSegments((text || "").slice(0, 600)), [text]);
  return (
    <span className="chatSelChipText">
      {segments.map((seg, i) => {
        if (seg.text) return <React.Fragment key={i}>{seg.text}</React.Fragment>;
        const html = renderKatex(seg.math, false);
        return html ? <span key={i} className="chatSelChipMath" dangerouslySetInnerHTML={{ __html: html }} />
          : <code key={i}>{seg.math}</code>;
      })}
    </span>
  );
}

export function SelChip({ kind, icon, label, note, file = "", cut, auto = false, text, title, onRemove, removeTitle }) {
  const Glyph = SEL_CHIP_ICONS[icon];
  return (
    <div className={`chatSelChip${kind ? ` ${kind}` : ""}${auto ? " auto" : ""}`} title={title ?? text}>
      <div className="chatSelChipHead">
        <span className="chatSelChipLabel"><Glyph size={14} aria-hidden="true" />{label}</span>
        {note ? (
          <span className="chatSelChipNote">· {file ? <FileThemeIcon path={file} format={formatOf(file).format} size={14} /> : null}{note}</span>
        ) : null}
        {cut ? <span className="chatSelChipNote cut">· {cut}</span> : null}
        <button type="button" className="uiClose uiCloseSm chatSelChipClose" onClick={onRemove} title={removeTitle} aria-label={removeTitle}><XIcon size={14} /></button>
      </div>
      <ChipPreview text={text} />
    </div>
  );
}

// No AI connected: what the chat is for, then one tile per way to connect
// (settings/providerEditor.js aiServiceTiles over GET /api/ai/settings). A tile
// opens Settings → Connections with the connect dialog set to that service.
// An account that can't store keys (a guest) can only ask for a shared one;
// an admin may also share one with the whole server. `intent`: the handlers
// that fetch Settings as the pointer or the focus reaches what opens it.
// Beside them the agents found on a computer, which need no connection,
// each led by its icon as in the composer's chip: those the account turned
// on (`agents`, the chat's computer agents) are picked with a click
// (`onPick`), and one found here but off (`found`, GET /api/local-agents'
// agents) opens Settings → AI → Agents, where it is turned on.
function ChatSetupCard({ info, isAdmin, onConnect, openSettings, intent, agents = [], found = [], onPick }) {
  const tiles = info.failed ? [] : aiServiceTiles(info, { long: true });
  return (
    <div className="chatSetup" data-guide="chat.setup">
      <span className="chatSetupIcon" aria-hidden="true"><SparklesIcon size={20} /></span>
      <div className="chatSetupTitle">{t("Chat with your papers")}</div>
      <p className="chatSetupText">
        {t("Ask about the paper you are reading and get answers that cite the page they come from. The assistant can also search, summarize and organize your library.")}
      </p>
      {info.failed ? (
        <button type="button" className="uiBtn primary chatSetupRetry" {...intent} onClick={() => onConnect()}>{t("Set up AI…")}</button>
      ) : !info.can_edit ? (
        <p className="chatSetupText">{t("No AI service is shared on this server — ask your administrator.")}</p>
      ) : (
        <>
          <div className="chatSetupTiles" role="group" aria-label={t("Connect an AI service")}>
            {tiles.map((tile) => (
              <button key={tile.value} type="button" className="chatSetupTile" {...intent} onClick={() => onConnect(tile.value)}>
                <span className="chatSetupTileName">{tile.label}</span>
                <span className="chatSetupTileHint">{tile.hint}</span>
              </button>
            ))}
          </div>
          <p className="chatSetupNote">{t("Keys are stored on this server and never shown again. Pick one to connect — it takes about a minute.")}</p>
        </>
      )}
      {agents.length || found.length ? (
        <>
          <div className="chatSetupText">{t("Or chat with an agent on your computer:")}</div>
          <div className="chatSetupTiles" role="group" aria-label={t("Agents on your computer")}>
            {agents.map((a) => {
              const Icon = agentIcon(a.id);
              return (
                <button key={a.id} type="button" className="chatSetupTile" onClick={() => onPick(a.id)}>
                  <span className="chatSetupTileName"><Icon size={16} />{agentChoice(a)}</span>
                  <span className="chatSetupTileHint">{a.ready === false ? <span className="agentProblem">{a.problem}</span>
                    : a.where ? t("Works on {computer}, with no AI service", { computer: a.where }) : t("Works on this computer, with no AI service")}</span>
                </button>
              );
            })}
            {openSettings ? found.map((a) => {
              const Icon = agentIcon(a.name);
              return (
                <button key={a.name} type="button" className="chatSetupTile" {...intent} onClick={() => openSettings("agents")}>
                  <span className="chatSetupTileName"><Icon size={16} />{agentLabel(a.name, a.label)}</span>
                  <span className="chatSetupTileHint">{t("Found on this computer: turn it on in Settings → AI → Agents")}</span>
                </button>
              );
            }) : null}
          </div>
        </>
      ) : null}
      {isAdmin && openSettings ? (
        <p className="chatSetupNote">
          {t("Or share one with everyone on this server: {link}", {
            link: <button type="button" className="chatEmptyLink" {...intent} onClick={() => openSettings("server")}>{t("Settings › Server › Shared AI provider")}</button>,
          })}
        </p>
      ) : null}
    </div>
  );
}

// The stored file of a page's PDF (a copy's is its original's), "" for one
// read from an address alone, null for a page without a PDF.
function pdfFileOf(page) {
  const doc = pageDocument(page);
  return isPdf(doc) ? fileHash(doc) : null;
}

export default function ChatDock({
  session,
  readOnly = false,
  // A workspace viewer asks the AI too, but chats are the workspace's and
  // only editors change them: the conversation stays in this tab (no
  // history, New chat starts over locally). App's session saves nothing then.
  canSave = true,
  // The open page's document (documents/pageDocument.js pageDocument).
  // `homeBlocks`: every page row App knows, the listing's and those read
  // beside it (a work folder's files: explorer/knownPages.js), which the
  // chips and the context name; `pickerPages` the ones the @ and context
  // pickers list (App's pickerPages: the library's, and the files of the
  // open tabs and recents).
  doc, focusedBlockId, homeBlocks, pickerPages = homeBlocks, libraryTree, pageTitle, openTabs,
  // App's recents queue ([{id, at}]): with the open tabs, what the page
  // pickers list first (rankLibraryPages).
  recentViews,
  pdfSelections, setPdfSelections,
  // Note chips ([{kind: "block", id, text} | {kind: "note", id, from, to,
  // text}], App state like pdfSelections) and the block row the user's
  // cursor is on ({id, text, sel?: {from, to, text}} — the text selected in
  // its editor) — both ride with the next message so "this block" and "the
  // selection" mean something. onSelectionSent drops the sent selection.
  chatNotes, setChatNotes, focusedNote, onSelectionSent,
  // chatImages: the pictures pending send (chat/chatPictures.js); the budget
  // they share with the context's own pictures (Settings → AI → Chat →
  // "Pictures per message"); onAttachView attaches the visible part of the
  // open paper, with the handwriting on it (App), when a paper is open.
  chatImages, setChatImages, chatPictureBudget = DEFAULT_PICTURE_BUDGET, onAttachView,
  chatModel, setChatModel, chatEffort, setChatEffort, chatSpeed, setChatSpeed, chatSystem,
  dictationModel, dictationLang,
  chatContextChars, setChatContextChars, multiContextChars,
  // openAiKeysEditor({service, entry}?) opens Settings → Connections: with
  // `service` the connect dialog set to it (the setup card), with `entry`
  // that connection's form (an error card's "Update key", the login check's
  // Fix…). focusSignal: bumped by App when a connection made from the setup
  // card is ready — the message box takes the focus once the chat is enabled.
  // preloadSettings fetches the Settings module ahead, as the pointer or the
  // focus reaches a button that opens it, so Settings opens with the click;
  // onSetupInfo hands App the masked key list the setup card read (GET
  // /api/ai/settings, the one Connections shows), so a tile's connect dialog
  // can open with Settings instead of a round trip later. aiInfoFailed: App's
  // read of /api/ai/models failed, so aiInfo may never come.
  aiInfo, aiInfoFailed = false, aiProvider, openAiKeysEditor, openSettings, preloadSettings, onSetupInfo, isAdmin = false, focusSignal = 0,
  aiHealth, dismissAiHealth,
  openPopover, setOpenPopover,
  setStatus, askConfirm,
  // A model that can't call tools on its connection (its registry row's
  // `tools` false): checkModelTools(model) checks it again (App's
  // testAiProvider with the model, resolving to its result), and
  // refreshAiModels reads /api/ai/models again, when a reply brings a
  // verdict the registry didn't have.
  checkModelTools, refreshAiModels,
  // Home/folder view: the folder being viewed (its id; "" = library root) —
  // enables the folder-agent tools; null in the paper view. agentPerms is the
  // Settings per-chat-kind permission map ({folder, pdf, notes} → {list,
  // read, block_read, search, rename, move, delete, organize, organize_delete,
  // block_edit, …}: chat/chatSettings.js has them all); setAgentPerms edits
  // it from the ⚙ popover), toolRounds the round budget and toolRoundsUnlimited whether it is lifted
  // (Settings → Tool limits); addTask / removeTask put a running Unlimited reply in Background tasks
  // (tasks/useTasks.js addLocal / removeLocal). When the AI applied changes, onLibraryChange
  // refreshes the home feed and onNotesChange reloads touched pages' notes.
  organizeFolder = null, toolRounds, toolRoundsUnlimited = false, addTask, removeTask,
  agentReadChars, agentPerms, setAgentPerms, agentSystem,
  agentEnabled, setAgentEnabled, onLibraryChange, onNotesChange, onAgentEvent,
  // Opens a page the reply links to (/?page=<id>) in place.
  onOpenPage,
  // A blocked fetch's card hands it to Gamma Connector by itself, to fetch
  // in a background tab (Settings → AI → Tools); paperSave: {allowOa, saveCopy,
  // fetchMetadata}, how a reply's "Save to library" and save_paper save
  // (Settings → Reading).
  fetchInBackground = false, delegateReads = true, paperSave = {},
  // {id, text}: a message App asks the chat to send (a handwriting block's
  // "Transcribe with AI"), with whatever is attached at that moment.
  askSignal = null,
  // The agents the chat talks to (chat/agents.js chatAgents: Gamma AI, then
  // the computer's agents the account turned on, or on a page another
  // computer keeps that computer's), which the composer's chip picks from,
  // and `localAgents` this computer's listing (GET /api/local-agents: the
  // directories it offers them, and the agents found that the setup card
  // offers to turn on). A computer's agent picked, the chat talks to it
  // through chat/computer/.
  agents = null, localAgents = null,
  onGrip, onFold, collapsed, onClose,
}) {
  const [loadedMessages, setChatMessages] = useState(NO_MESSAGES);
  const [shownFor, setShownFor] = useState(null); // the bucket loadedMessages are of
  const [loadError, setLoadError] = useState("");
  // Chat history is per page; the home view buckets per folder (the folder's
  // id, "home" at the library root) — switching folders switches
  // conversations, so the organizer never drags one folder's context into
  // another. A rename or a move keeps the bucket; deleting the folder files
  // its conversations into "home"'s history (DELETE /api/folders/{id}).
  const chatKey = focusedBlockId || organizeFolder || "home";
  // Which agent the bucket's chat talks to: the one picked for it in this
  // tab (chat/computer/chatSide.js), else chat/agents.js pickAgent's. A
  // computer's agent's conversation is the server's, read by `side`
  // (chat/computer/useComputerChat.js useComputerSide) and drawn by the
  // same transcript and composer; the draft stays across a pick. A viewer
  // talks to Gamma AI.
  const [wanted, setWanted] = useChatAgent(chatKey);
  // A comment thread's Open in chat, or a co-author's conversation opened
  // from History or Background tasks, shows that conversation in place of
  // the page's own (chat/computer/chatSide.js), until Back to the chat.
  const [shownChat, leaveShown] = useChatShown(chatKey);
  const gammaNav = useContext(GammaNavContext);
  const agentList = useMemo(() => (readOnly || !agents?.length ? [gammaAgent(aiInfo)] : agents), [agents, readOnly, aiInfo]);
  const agent = pickAgent(agentList, wanted);
  const onComputer = agent?.kind === "computer";
  // What only Gamma AI takes with a message: notes and the cursor's block,
  // library pages, files and pictures, the PDF itself.
  const attaches = !onComputer;
  // A changed file's link in a computer agent's reply resolves through its
  // work folder (side.links); Gamma AI's replies keep the page's own.
  const outerLinks = useContext(PathLinkContext);
  const side = useComputerSide(onComputer ? chatKey : "", agent, {
    page: !!focusedBlockId, dirs: localAgents?.dirs || NO_DIRS, pdfSelections, setPdfSelections, setStatus,
    shown: onComputer ? shownChat : null,
  });
  // The draft comes back after a remount in the conversation it was typed
  // in; loading another conversation empties it (below).
  const [chatInput, setChatInput] = useState(() => (kept.key === chatKey ? kept.draft : ""));
  useEffect(() => { kept.draft = chatInput; }, [chatInput]);
  const sessionState = useSyncExternalStore(session.subscribe, session.getSnapshot);
  // What the dock shows of the bucket: this tab's own copy (a reply sent
  // here), else what came from the server; a switch renders, from its first
  // render, what the dock last showed of the bucket switched to
  // (session.recall: only what was read or made here), never the
  // conversation before it.
  const recalled = shownFor === chatKey ? null : session.recall(chatKey);
  const loadedHere = shownFor === chatKey ? loadedMessages : recalled?.messages || NO_MESSAGES;
  const chatMessages = sessionState.replies.get(chatKey)?.messages || loadedHere;
  // A conversation loaded, switched to or read again (not this tab's own
  // reply) shows still: no message rises, no steps pill opens, shuts or
  // morphs, and no label is read out.
  const fromLoad = chatMessages === loadedHere;
  // A reply is streaming into THIS conversation. Other buckets stream on
  // their own — asking one paper never waits for another's answer.
  const busyHere = session.isActive(chatKey);
  // The conversation's saves keep failing (chatSession retried what was
  // worth retrying): it lives only in this tab until one goes through.
  const saveError = session.saveError(chatKey);
  // Where a reply's "Save to library" files papers: the folder the chat is
  // about — the one viewed, else the first of the open paper's (a
  // sub-page's are its top page's).
  const homeById = useMemo(() => new Map(homeBlocks.map((b) => [b.id, b])), [homeBlocks]);
  const paperFolder = organizeFolder != null ? organizeFolder
    : filedIn(libraryTree.folders, topPageOf(homeById, focusedBlockId)?.properties?.folders)[0] || "";
  // No AI connected (known once /api/ai/models answered): the setup card
  // takes the empty state, the composer is disabled and the header's tools
  // go — a send could only fail. The card's tiles come from the settings
  // payload, read alongside /api/ai/models while that is unknown (and again
  // whenever the AI goes off), so the card lands whole. Until the chat knows
  // which it is (`aiKnown`), the window is a plain frame: no header tools,
  // no empty-state text. A failed read counts as known: the chat then shows
  // as it would with an AI, and a send says what is wrong. None of this
  // closes a conversation with a computer's agent, which needs no AI
  // service (`closed`): only what is Gamma AI's goes.
  const aiOff = !readOnly && !!aiInfo && !aiInfo.enabled;
  const closed = aiOff && !onComputer;
  const wantsSetup = !readOnly && !aiInfo?.enabled;
  const [setupInfo, setSetupInfo] = useState(null);
  useEffect(() => {
    if (!wantsSetup) return undefined;
    let live = true;
    apiJson(`${API}/ai/settings`)
      .then((info) => { if (live) { setSetupInfo(info); onSetupInfo?.(info); } })
      .catch(() => { if (live) setSetupInfo({ failed: true }); });
    return () => { live = false; };
  }, [wantsSetup]);
  const aiKnown = readOnly || (aiInfo ? aiInfo.enabled || !!setupInfo : aiInfoFailed);
  const settingsIntent = preloadSettings
    ? { onPointerEnter: preloadSettings, onPointerDown: preloadSettings, onFocus: preloadSettings } : {};
  const composerRef = useRef(null);
  const focusPendingRef = useRef(false);
  useEffect(() => { if (focusSignal) focusPendingRef.current = true; }, [focusSignal]);
  useEffect(() => {
    if (!focusPendingRef.current || aiOff || !aiInfo) return;
    focusPendingRef.current = false;
    composerRef.current?.querySelector("textarea")?.focus();
  }, [focusSignal, aiOff, aiInfo]);
  const folderChat = organizeFolder != null;
  // Which of the three chat kinds this is — each has its own tool permission
  // map in Settings → AI → Tool usage (app/prefDefs.js CHAT_KINDS): the folder chat, a
  // page with a PDF, a page of notes.
  const chatKind = folderChat ? "folder" : isPdf(doc) ? "pdf" : "notes";
  // The stored file of the open page's PDF, which a copy shares with its
  // original: what a message records it carried (pdfDocs).
  const pdfFile = pdfFileHash(doc);
  const chatKindLabel = chatKindName(chatKind);
  // The chat settings shortcut edits the same global preferences as Settings.
  const chatToolPerms = agentPerms?.[chatKind] || {};
  const toolsEnabled = !!agentEnabled;
  const perm = (key) => permState(chatToolPerms, key) !== "off";
  // "Allow in this chat" (chat/approvals.js): the permissions this
  // conversation stopped asking for, kept per account and conversation in
  // this browser; another tab's change arrives as a storage event.
  const [grantStore, setGrantStore] = useState(readGrants);
  useEffect(() => {
    const onStorage = (e) => { if (e.key === GRANTS_KEY) setGrantStore(readGrants()); };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);
  const activeUser = () => { try { return localStorage.getItem("gamma-active-user") || ""; } catch { return ""; } };
  const grantedHere = grantsIn(grantStore, activeUser(), conversationId(chatMessages))
    .filter((key) => permState(chatToolPerms, key) === "ask");
  const updateGrants = (change) => setGrantStore((store) => {
    const next = change(store);
    writeGrants(next);
    return next;
  });
  // Approvals answered here, id → decision: the steps pill names the running
  // call again while it runs, instead of waiting, and a card answered from
  // the note its edit is drawn in says so as if answered on it.
  const [answeredApprovals, setAnsweredApprovals] = useState(() => new Map());
  // What the agent may do here after applying the shared permissions.
  const agentReads = READ_TOOLS.some(perm);
  const agentWrites = WRITE_TOOLS.some(perm);
  // Agent fields riding on /api/ai/chat ({} = plain chat): folder chats reach
  // the folder's pages, page chats get the read + note-block tools for their
  // own page.
  const agentPayload = (prevMessages) => {
    if (!toolsEnabled) return {};
    const scope = organizeFolder != null && (agentReads || agentWrites)
      ? { agent_scope: "folder", folder: organizeFolder }
      : focusedBlockId && toolsForKind("pdf").some(perm)
        ? { agent_scope: "page", page_id: focusedBlockId }
        : null;
    return scope
      ? { ...scope, tool_rounds: toolRounds || 0, read_char_limit: agentReadChars || 0,
          ...(toolRoundsUnlimited ? { tool_rounds_unlimited: true } : {}),
          permissions: chatToolPerms, agent_system: agentSystem || "",
          granted: grantsIn(readGrants(), activeUser(), conversationId(prevMessages)),
          // Whether a blocked paper holds the reply open on its card, or
          // leaves the card under it ("Skip, and don't wait in this chat").
          paper_wait: waitsForPapers(readGrants(), activeUser(), conversationId(prevMessages)),
          delegate_reads: delegateReads !== false,
          // save_paper stores a paper the way the reply's Save to library does.
          paper_save: { allow_oa: paperSave.allowOa !== false, save_copy: paperSave.saveCopy !== false,
                        fetch_metadata: paperSave.fetchMetadata !== false } }
      : {};
  };
  // "Skip, and don't wait in this chat": later blocked papers of this
  // conversation leave their card under the reply instead of holding it.
  const neverWaitHere = () =>
    updateGrants((store) => withoutPaperWait(store, activeUser(), conversationId(chatMessages)));
  // The user's answer on an approval card, or on the bar of the note the
  // card's edit is drawn in (chat/noteReview.js): the server runs the call or
  // leaves it; "Allow in this chat" is kept for the conversation and
  // "Always allow" sets the permission in Settings — once the server took it.
  async function decideApproval(approval, decision, note = "") {
    await apiJson(`${API}/ai/approvals/${encodeURIComponent(approval.id)}`, {
      method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ decision, ...(note ? { note } : {}) }),
    });
    setAnsweredApprovals((done) => new Map(done).set(approval.id, decision));
    noteReview.settle(approval.id);
    if (decision === "chat") updateGrants((store) => withGrant(store, activeUser(), conversationId(chatMessages), approval.perm));
    if (decision === "always") changePermission(setAgentPerms, chatKind, approval.perm, "allow");
  }
  // The note's bar answers through the latest render's, as the card does:
  // the reply's own closure would file a grant under the conversation as it
  // stood before its first message.
  const decideRef = useRef(decideApproval);
  decideRef.current = decideApproval;
  // The block the user's cursor is on shows as a "Cursor" chip (like a PDF
  // selection) and rides with the message as focus_block_id; its × leaves it
  // out for that block until the cursor moves.
  const [cursorOff, setCursorOff] = useState(null);
  useEffect(() => { setCursorOff(null); }, [focusedNote?.id]);
  const cursorChip = focusedNote && focusedNote.id !== cursorOff ? focusedNote : null;
  // What the message quotes of each passage it points at — the PDF passages,
  // then the selected note text, the editor's first, sharing the
  // single-paper budget the way the server cuts them (chat/passageBudget.js).
  // A chip whose passage won't go whole says what it keeps.
  const quotedShares = passageShares([...pdfSelections, cursorChip?.sel,
    ...(chatNotes || []).filter((n) => n.kind === "note")], chatContextChars);
  const keptOf = (p) => keptNote(quotedShares.get(p), charCount(p.text));
  const keptHint = (p) => keptTitle(quotedShares.get(p), charCount(p.text), chatContextChars);
  // Empty-state intro and input placeholder for an agent-enabled home chat.
  const agentScopeName = organizeFolder ? t("this folder") : t("your library");
  const agentIntro = organizeFolder == null || !toolsEnabled ? null
    : agentWrites
      ? t("Ask AI anything — it can {and}organize {agentScopeName} (rename pages, file them into folders{notes})…", { and: agentReads ? t("read, search and ") : "", agentScopeName, notes: perm("block_edit") ? t(", edit notes") : "" })
      : agentReads
        ? t("Ask AI across {scope} — it can read, search and summarize them…", { scope: organizeFolder ? t("this folder's pages") : t("your library") })
        : null;
  const agentAsk = agentIntro ? (agentWrites ? t("Ask, or organize {scope}…", { scope: agentScopeName }) : t("Ask across {scope}…", { scope: agentScopeName })) : null;
  const chatKeyRef = useRef(chatKey);
  chatKeyRef.current = chatKey;
  // chatImages (pasted/area-selection figures pending send) lives in App —
  // like pdfSelections — so the PDF viewer can attach into it.
  const [editingMsg, setEditingMsg] = useState(null); // {idx, text} — editing a sent user message
  const [copiedMsgIdx, flashCopiedMsg] = useCopied(1200);
  const [openActions, setOpenActions] = useState(() => new Set()); // expanded tool chips, "<msg>:<action>"
  const toggleAction = (key) => setOpenActions((prev) => {
    const next = new Set(prev);
    next.has(key) ? next.delete(key) : next.add(key);
    return next;
  });
  const [chatFindOpen, setChatFindOpen] = useState(false);
  const [chatFind, setChatFind] = useState("");
  const [chatFindIdx, setChatFindIdx] = useState(0);
  // On by default: every message then carries the PDF, which the server
  // keeps at the head of the conversation so the provider's prompt cache
  // serves it after the first message. Derived from the loaded history
  // below: on while the latest message carried this document.
  const [attachPdf, setAttachPdf] = useState(false);
  // Whether the active model's provider takes the PDF file itself. Where it
  // doesn't, the server falls back to extracted text, so the PDF button
  // must not default on, and turning it on by hand gets a warning, not silence.
  const activeModel = (aiInfo?.models || []).find((m) => m.id === chatModel) || null;
  // The header's model list is scoped to the active key (Settings → AI ›
  // Connections); the first key's when none is selected or the selected one is gone.
  const headerModels = providerModels(aiInfo?.models, aiProvider);
  const headerModel = headerModels.find((m) => m.id === chatModel) || headerModels[0] || null;
  // The model can't call tools on its connection: its chats go without them,
  // which the composer's No tools chip says (a click checks it again, on an
  // own connection or by an administrator), and the agent's intro and
  // placeholder give way to the plain chat's.
  const noTools = headerModel?.tools === false;
  const canCheckTools = !!checkModelTools && (!headerModel?.shared || isAdmin);
  const [checkingTools, setCheckingTools] = useState(false);
  const noToolsHint = noTools ? (canCheckTools
    ? t("{model} can't call tools on this connection ({reason}), so its chats answer without them. Click to check again.",
      { model: headerModel.model, reason: headerModel.tools_reason || "" })
    : t("{model} can't call tools on this connection ({reason}), so its chats answer without them. An administrator can check it again in Settings → Server.",
      { model: headerModel.model, reason: headerModel.tools_reason || "" })) : "";
  async function checkTools() {
    const m = headerModel;
    if (!canCheckTools) { setStatus(noToolsHint); return; }
    setCheckingTools(true);
    try {
      const r = await checkModelTools(m);
      setStatus(!r?.tools ? t("Couldn't check {model}: {headline}", { model: m.model,
        headline: failureCopy(r?.kind, { provider: m.provider_name }).headline })
        : r.tools.ok ? t("{model} can use tools", { model: m.model })
        : t("{model} still can't use tools: {reason}", { model: m.model, reason: r.tools.reason }));
    } finally {
      setCheckingTools(false);
    }
  }
  const modelInfo = useModelInfo(!aiOff && aiInfo ? headerModel?.id : "");
  // The context ring: the latest reply's size against the model's window.
  const ctxUsed = contextUsed(chatMessages);
  const ctxWindow = ctxUsed ? modelInfo?.context_window || null : null;
  // The reasoning efforts the picked model takes ([] = none); a model no
  // source knows gets the generic ones. The preference stays as chosen and
  // is sent as the nearest level this model takes (chat/modelPrefs.js), so a
  // switch to a model without "xhigh" and back keeps it.
  const effortLevels = Array.isArray(modelInfo?.efforts) ? modelInfo.efforts : (aiInfo?.efforts || ["low", "medium", "high"]);
  const effort = effortFor(chatEffort, effortLevels);
  // The service tiers this model can run at ([] = none, and none at all
  // until model-info answers — a speed is never guessed, it costs money).
  const speedTiers = Array.isArray(modelInfo?.speeds) ? modelInfo.speeds : [];
  const speed = speedFor(chatSpeed, speedTiers);
  const nativePdf = activeModel ? activeModel.native_pdf !== false : true;
  // The mic shows only when a connection can transcribe (an OpenAI-protocol
  // key — /api/ai/models `transcribe`); without one dictation can only fail.
  const canDictate = !aiOff && !!aiInfo?.transcribe;
  const nativePdfNote = nativePdf ? "" :
    t("{provider} does not accept PDF files — the PDF is sent as extracted text instead (first {chatContextChars} characters; Settings / AI / Chat / Context size).", { provider: activeModel?.provider_name || t("This provider"), chatContextChars: (chatContextChars || 0).toLocaleString() });
  const attachPdfManualRef = useRef(false); // the user toggled the PDF button themselves
  useEffect(() => {
    // A provider without native PDF input: drop the automatic "send the file
    // once" default. A deliberate manual switch-on stays (with its warning).
    if (!nativePdf && !attachPdfManualRef.current) setAttachPdf(false);
  }, [nativePdf]);
  // Extra chat context: selected PDF pages + whether to include notes/highlights.
  const [chatDocs, setChatDocs] = useState([]);
  // The PDF switch shows only while a PDF is in context: the open
  // page's, or an attached page's.
  const pdfInContext = isPdf(doc) || chatDocs.some((id) => isPdf(pageDocument(homeBlocks.find((b) => b.id === id))));
  const [chatIncludeNotes, setChatIncludeNotes] = useState(false);
  const [chatFiles, setChatFiles] = useState([]); // uploaded PDFs ({name, data}) pending send
  const [docPicker, setDocPicker] = useState(false); // "Add pages" modal
  const [docPickerQuery, setDocPickerQuery] = useState("");
  const chatScrollRef = useRef(null);
  // Ctrl+scroll over the transcript: session-only text size (useTextScale).
  const chatTextScale = useTextScale();
  const chatScrollRefCb = useCallback((el) => { chatScrollRef.current = el; chatTextScale.ref(el); }, [chatTextScale.ref]);
  const [newChatAlt, newChatAltProps] = useAltVariant();
  // Voice dictation (ChatGPT-style): mic records (live waveform), ■ transcribes
  // into the input via /api/ai/transcribe, ↑ transcribes and sends, × discards.
  const [dictation, setDictation] = useState(""); // "" | "rec" | "busy"
  const [recSecs, setRecSecs] = useState(0);
  const recRef = useRef(null); // {recorder, blob, canceled, autoSend, baseText, analyser, amps}
  const waveCanvasRef = useRef(null);
  // The latest render's send to the agent shown, for the record-view ↑
  // button — the recorder's onstop fires from the render that started it,
  // and a page switch mid-recording would otherwise send through a stale
  // conversation closure — and its Gamma AI send, for App's asks.
  const sendTextRef = useRef(null);
  const sendChatRef = useRef(null);

  // The active conversation's user-given name ("" = derived from its first
  // message). Renames go to the server directly; the autosave never sends
  // it, so a debounced save can't roll a rename back.
  const [chatTitle, setChatTitle] = useState("");

  // A change under a reply was reverted, or redone (`reverted` false —
  // chat/AgentChanges.jsx): its action is marked or unmarked in the saved
  // conversation, so the model hears of it on the next turn, and the pages
  // it touched reload where no live socket brought the ops (a folder or
  // label put back or deleted again: the library listing). The
  // conversation is the one the reply belongs to, even if the dock moved
  // on while the revert dialog's run went on.
  const loadedRef = useRef(loadedHere);
  loadedRef.current = loadedHere;
  // The reply whose "Revert…" opened the dialog that reverts several of the
  // conversation's changes at once (chat/RevertChanges.jsx); it closes when
  // the dock shows another conversation. A reply offers it while the
  // conversation has two changes or more to take back.
  const [revertFrom, setRevertFrom] = useState(null);
  useEffect(() => setRevertFrom(null), [chatKey]);
  const revertibleCount = useMemo(() => revertible(chatMessages).length, [chatMessages]);
  const changesReverted = (id, indexes, answer, reverted = true) => {
    const current = session.getSnapshot().replies.get(chatKey)?.messages
      || (chatKeyRef.current === chatKey ? loadedRef.current : null);
    const message = current?.find((m) => m.id === id);
    if (message) {
      const next = markReverted(current, id, indexes, reverted, answer?.kept ? { kept: answer.kept } : {});
      if (next !== current) {
        session.edit(chatKey, next, chatTitle)?.catch((err) => setStatus(t("Couldn't save the conversation: {message}", { message: err.message })));
      }
    }
    const touched = indexes.flatMap((i) => [message?.actions?.[i]?.page_id, message?.actions?.[i]?.src_page_id]);
    const pages = [...new Set([answer?.page_id, ...touched].filter(Boolean))];
    if (pages.length) onNotesChange?.(pages);
    if (indexes.some((i) => message?.actions?.[i] && changePlace(message.actions[i]) === "library")) onLibraryChange?.();
    // A note showing the change as tracked changes stops: the row says it now.
    for (const i of indexes) if (message?.actions?.[i]?.block_id) noteReview.forget(message.actions[i].block_id);
  };
  // The bar under a note that shows the AI's change (chat/noteReview.js)
  // reverts one step of it as a row of its reply does, and marks it in the
  // conversation the reply belongs to, whichever the dock shows now. A
  // refusal comes back as the row would say it; one whose row would show
  // what reverting anyway writes sends the user there.
  const notesChangeRef = useRef(onNotesChange);
  notesChangeRef.current = onNotesChange;
  useEffect(() => noteReview.connect(async (step, { force }) => {
    let answer;
    try {
      answer = await sendRevert(step.action, { force });
    } catch (err) {
      const refusal = revertRefusal(err);
      if (refusal.preview?.diff?.length) {
        return { text: t("The note changed since. Its row in the chat shows what reverting anyway would do."), force: "" };
      }
      const copy = refusalCopy(refusal, false);
      return { text: copy.text, force: copy.force || "" };
    }
    const messages = session.getSnapshot().replies.get(step.key)?.messages
      || (chatKeyRef.current === step.key ? loadedRef.current : null);
    const next = messages && markReverted(messages, step.reply, [step.index], true);
    if (next && next !== messages) {
      session.edit(step.key, next)?.catch((err) => setStatus(t("Couldn't save the conversation: {message}", { message: err.message })));
    }
    notesChangeRef.current?.([answer?.page_id || step.action.page_id].filter(Boolean));
    return null;
  }), [session]); // eslint-disable-line react-hooks/exhaustive-deps -- the latest state is read through refs

  // Show a conversation that came from the server (bucket switch, or a
  // history entry opened). PDF button: on for a conversation without
  // messages, else as the latest message left it — on when it carried this
  // document. Messages record the doc ids they carried (pdfDocs); older
  // saves only have display names — treat any sent PDF as covering the
  // current one. The composer is left alone: a bucket switch empties it up
  // front, so a draft typed while the stored copy loads stays. The session
  // keeps what was shown (`remember`), for a return to this bucket.
  function showLoaded(msgs, title, known = true) {
    setChatMessages(msgs);
    setShownFor(chatKey);
    // nothing shown of a bucket not read yet is remembered: coming back to
    // it before its read lands, the dock does not take it for empty
    if (known) session.remember(chatKey, msgs, title || "");
    const lastUser = [...msgs].reverse().find((m) => m.role === "user");
    const references = [...new Set((lastUser?.contextPages || []).map((p) => p.id))].slice(0, MAX_CHAT_REFERENCES);
    setChatDocs(references);
    setChatIncludeNotes(!!lastUser?.includeNotes);
    setChatTitle(title || "");
    const carried = !lastUser || (lastUser.pdfDocs
      ? (pdfFile && lastUser.pdfDocs.includes(pdfFile)) || lastUser.pdfDocs.includes(focusedBlockId)
        || (!pdfFile && references.some((id) => lastUser.pdfDocs.includes(id)
          || lastUser.pdfDocs.includes(pdfFileOf(homeBlocks.find((b) => b.id === id)))))
      : !!lastUser.pdfs?.length);
    attachPdfManualRef.current = false;
    setAttachPdf(carried && nativePdf);
  }

  // The bucket whose stored conversation has been read (or failed to): an
  // ask from App waits for it, so it never starts over the history.
  const [loadedFor, setLoadedFor] = useState("");
  // Load chat from backend whenever the chat bucket changes. What this tab
  // holds of it shows at once, before the frame is painted: its own copy
  // (a reply sent here), else what the dock last showed of it; the stored
  // copy then replaces it in place. A bucket never seen shows nothing until
  // it is read, not even what an empty one shows.
  useLayoutEffect(() => {
    let cancelled = false;
    setChatDocs([]);
    setChatIncludeNotes(false);
    if (kept.key !== chatKey) { kept.key = chatKey; setChatInput(""); }
    setDocPicker(false);
    const reply = session.getSnapshot().replies.get(chatKey);
    const reloadSaved = session.isSaved(chatKey);
    const shown = reply || session.recall(chatKey);
    showLoaded(shown?.messages || NO_MESSAGES, shown?.title, !!shown);
    setLoadError("");
    apiJson(`${API}/chats/${encodeURIComponent(chatKey)}`)
      .then(data => {
        if (cancelled) return;
        const latest = session.getSnapshot().replies.get(chatKey);
        // Once a reply was saved before this GET, the server is authoritative
        // again (another tab or a folder rename may have changed the bucket).
        // Whenever the stored copy is what's shown, later saves are
        // conditional on its version (chatSession); a copy of our own that
        // isn't saved yet keeps the version its last save left.
        if (reloadSaved && latest === reply && session.isSaved(chatKey)) {
          showLoaded(data.messages || [], data.title);
          session.forget(chatKey);
          session.seen(chatKey, data.messages || [], data.updated_at);
        } else {
          showLoaded(latest?.messages || data.messages || [], data.title);
          if (!latest) session.seen(chatKey, data.messages || [], data.updated_at);
        }
        setLoadedFor(chatKey);
      })
      .catch((err) => {
        if (cancelled) return;
        if (!session.getSnapshot().replies.has(chatKey)) setLoadError(t("Could not load chat: {message}", { message: err.message }));
        setLoadedFor(chatKey);
      });
    return () => { cancelled = true; };
  }, [chatKey, pdfFile, readOnly, session]);

  // Back in this tab: another tab or member may have moved the conversation
  // on (a newer answer, a New chat), so the stored copy is read again and
  // shown when its version is not the one this tab holds. Only while this
  // tab's copy is saved and no reply streams here: otherwise the next save's
  // merge brings the stored copy in. The draft in the composer stays.
  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState === "hidden") return;
      const key = chatKeyRef.current;
      const reply = session.getSnapshot().replies.get(key);
      if (session.isActive(key) || (reply && !session.isSaved(key))) return;
      const version = session.version(key);
      apiJson(`${API}/chats/${encodeURIComponent(key)}`).then((data) => {
        if (data.updated_at === undefined || data.updated_at === version) return; // a share view has no version
        if (chatKeyRef.current !== key || session.isActive(key)
          || session.getSnapshot().replies.get(key) !== reply || session.version(key) !== version) return;
        session.forget(key);
        session.seen(key, data.messages || [], data.updated_at);
        session.remember(key, data.messages || [], data.title || "");
        setChatMessages(data.messages || []);
        setChatTitle(data.title || "");
        setHistoryRead("");
      }).catch(() => {});
    };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [session]);

  // History: the bucket's earlier conversations (server `chat_history`).
  // "New chat" archives the current one there instead of deleting it, and
  // opening an entry swaps it with the current one. Both send the client's
  // copy of the conversation, so a reply still sitting in the autosave
  // debounce is kept. The list loads lazily when the popover opens. Each
  // bucket's list as last read stays (`histories`), and an archive or an
  // open made here changes it as the server did. After any change (a bucket
  // switch, an archive, an open, another agent picked) the popover opens on
  // it at once and reads it again in place (`historyRead`, the bucket read
  // since). The computer agents' conversations of the bucket are listed
  // with Gamma AI's, by time (`agentHistories`, GET
  // /api/local-agents/chat/history, read beside it whatever agents are on:
  // chat/computer/computerChat.js historyRows); a row opens with its agent
  // picked.
  const [histories, setHistories] = useState(() => new Map()); // bucket -> its entries
  const [agentHistories, setAgentHistories] = useState(() => new Map()); // bucket -> {conversations, active}
  const [historyRead, setHistoryRead] = useState("");
  const history = histories.get(chatKey) ?? null; // null = never read here
  // A change to the bucket's list as last read; a list never read stays so.
  const editHistory = (change) => setHistories((all) => (all.has(chatKey) ? new Map(all).set(chatKey, change(all.get(chatKey))) : all));
  const dropAgentRows = (ids) => setAgentHistories((all) => {
    const was = all.get(chatKey);
    return was ? new Map(all).set(chatKey, { ...was, conversations: was.conversations.filter((c) => !ids.includes(c.id)) }) : all;
  });
  const [historyQuery, setHistoryQuery] = useState("");
  const [renaming, setRenaming] = useState(null); // {id: "" = the active chat | entry id, text}
  const [picked, setPicked] = useState(() => new Set()); // history entries ticked for deleting
  const renameCancelRef = useRef(false);
  const historyOpen = openPopover === "chathistory";
  useEffect(() => { setHistoryRead(""); setHistoryQuery(""); setRenaming(null); }, [chatKey]);
  // Another agent picked, or another of its conversations shown: which one
  // is shown changed.
  useEffect(() => { setHistoryRead(""); }, [agent?.id, side.chat.conversation?.id]);
  // The selection belongs to the open popover: closing it drops the ticks.
  useEffect(() => { if (!historyOpen) setPicked(new Set()); }, [historyOpen]);
  // History's "Codex sessions in paper": the sessions the picked agent's
  // CLI keeps of the directory it works in on its computer, this one or
  // another of the reader's (asked there over its channel), started in a
  // terminal or an editor (computerChat.js sessionsDir; none for a
  // co-author's agent or a conversation shown in place of the bucket's
  // own), read again each time History opens and on the caption's refresh,
  // since they change outside Gamma, and never while it is closed
  // (`sessionsList`: {key, sessions, error, away, reading}, `sessions` null
  // while first read, the ones read before kept while `reading` them again,
  // `away` why another computer answered nothing: sessionsAway).
  const sessionsAt = onComputer && !side.shown ? sessionsDir(side.info, side.place) : "";
  const sessionsKey = sessionsAt ? `${agent.id}\n${sessionsAt}` : "";
  const [sessionsList, setSessionsList] = useState({ key: "", sessions: null, error: "", away: "", reading: false });
  const [sessionsAsked, setSessionsAsked] = useState(0); // a read asked again (a refresh, an open that failed)
  const [openingSession, setOpeningSession] = useState(""); // the session whose open is on its way: one at a time
  const openingSessionRef = useRef(false);
  useEffect(() => {
    if (!historyOpen || !sessionsKey) return undefined;
    let cancelled = false;
    setSessionsList((was) => (was.key === sessionsKey && was.sessions && !was.error ? { ...was, reading: true }
      : { key: sessionsKey, sessions: null, error: "", away: "", reading: false }));
    const [name, dir] = sessionsKey.split("\n");
    computerSessions(name, dir)
      .then((data) => {
        if (!cancelled) {
          setSessionsList({ key: sessionsKey, sessions: data.sessions || [], error: "", away: data.away || "", reading: false });
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setSessionsList({ key: sessionsKey, sessions: [], away: "", reading: false,
            error: t("Couldn't list the sessions: {message}", { message: err.message }) });
        }
      });
    return () => { cancelled = true; };
  }, [historyOpen, sessionsKey, sessionsAsked]);
  const togglePicked = (id) => setPicked((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  // A row pressed while rows are ticked ticks it (an agent's current
  // conversation is never ticked); else it opens.
  const pickOrOpen = (row) => (picked.size ? (row.current || togglePicked(row.id)) : openRow(row));
  useEffect(() => {
    if (readOnly || !historyOpen || historyRead === chatKey) return;
    let cancelled = false;
    const key = chatKey;
    // The account's conversations with the computer agents, whatever
    // became of those agents; a list that cannot be read lists nothing.
    const theirs = computerHistory(key).catch(() => null);
    apiJson(`${API}/chat-history?bucket=${encodeURIComponent(key)}`)
      .then(async (data) => {
        const agents = await theirs;
        if (cancelled) return;
        setHistories((all) => new Map(all).set(key, data.sessions || []));
        setAgentHistories((all) => new Map(all).set(key, agents));
        setHistoryRead(key);
      })
      .catch((err) => {
        if (cancelled) return;
        setHistories((all) => (all.has(key) ? all : new Map(all).set(key, [])));
        setHistoryRead(key);
        setStatus(t("Chat history: {message}", { message: err.message }));
      });
    return () => { cancelled = true; };
  }, [historyOpen, historyRead, chatKey, readOnly]);
  const activeTitle = chatTitle || deriveTitle(chatMessages) || t("Untitled");
  // The page an agent step names (a citation pill gets its paper from
  // GammaNavContext's citeSource).
  const pageTitleOf = (id) => homeBlocks.find((b) => b.id === id)?.content || (id === focusedBlockId ? pageTitle : "") || "";
  // Reserve the reply's bubble before the first stream event. This placeholder
  // is display-only; tool activity and answer text replace it in the same row.
  const visibleMessages = busyHere && (!chatMessages.length || chatMessages.at(-1).role === "user")
    ? [...chatMessages, { role: "ai", text: "", partial: true }]
    : chatMessages;
  // What the transcript draws: Gamma AI's conversation with the reply's
  // placeholder, or the computer agent's in the same shape.
  const transcriptMessages = onComputer ? side.messages : visibleMessages;
  // The conversation as this tab holds it — after a flush, which may have
  // merged in what another tab saved — and the version it is based on, so
  // the server archives a newer stored copy instead of dropping it.
  const currentPayload = () => ({
    bucket: chatKey, title: chatTitle, updated_at: session.version(chatKey),
    messages: session.getSnapshot().replies.get(chatKey)?.messages ?? chatMessages,
  });

  // `discard` (Alt+click): the conversation is dropped, not kept in history.
  async function newChat(discard = false) {
    if (busyHere) return;
    // A viewer's conversation was never saved: starting over is local.
    if (canSave) {
      const kept = chatMessages.length > 0;
      try {
        await session.flush(chatKey);
        const payload = currentPayload();
        const { id } = await apiJson(`${API}/chat-history/archive`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ ...payload, discard }) });
        // The conversation kept leads the list (one discarded is not listed;
        // a newer stored copy filed with it comes with the next read).
        if (id && !discard) editHistory((rows) => [filedRow(id, payload), ...rows]);
      } catch (err) {
        setStatus(discard ? t("Couldn't discard the conversation: {message}", { message: err.message })
          : t("Couldn't keep the conversation in history: {message}", { message: err.message }));
        return;
      }
      // A conversation went into History (or was thrown away): the guide's
      // hint names History and the Alt+click.
      if (kept) guideEvents.emit("chat.archived", { discard });
    }
    session.forget(chatKey);
    if (chatKeyRef.current !== chatKey) return;
    setChatMessages([]);
    setChatDocs([]);
    setChatInput("");
    setChatIncludeNotes(false);
    setChatTitle("");
    attachPdfManualRef.current = false;
    setAttachPdf(nativePdf); // new chat: the full PDF goes again (where the provider takes it)
    session.remember(chatKey, [], "");
    setHistoryRead("");
  }

  // → whether the entry is the conversation shown now.
  async function openHistory(id) {
    if (busyHere) return false;
    try {
      await session.flush(chatKey);
      const payload = currentPayload();
      const data = await apiJson(`${API}/chat-history/${id}/open`,
        { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(payload) });
      // The swap: the entry opened leaves the list, the conversation it
      // replaced leads it.
      const filed = data.archived ? [filedRow(data.archived, payload)] : [];
      editHistory((rows) => [...filed, ...rows.filter((s) => s.id !== id)]);
      session.forget(chatKey);
      session.seen(chatKey, data.messages || [], data.updated_at);
      if (chatKeyRef.current !== chatKey) return false;
      showLoaded(data.messages || [], data.title);
      setChatInput("");
      setHistoryRead("");
      setOpenPopover(null);
      chatStickRef.current = true;
      return true;
    } catch (err) {
      setStatus(t("Couldn't open the conversation: {message}", { message: err.message }));
      setHistoryRead(""); // the list may be out of date (another tab opened or deleted the entry)
      return false;
    }
  }

  // A History row opened, with its agent picked: a co-author's
  // conversation is shown in place of the bucket's own (chatSide.js
  // showInChat); any other leaves what was shown so: another agent's
  // current conversation is shown by picking that agent; an archived one of
  // a computer's agent becomes the bucket's active one first (the one there
  // archived), as Gamma AI's swaps with the one shown.
  async function openRow(row) {
    if (row.lent) {
      setOpenPopover(null);
      setHistoryRead("");
      chatStickRef.current = true;
      showInChat(chatKey, { conversation: row.id }, row.agent);
      return;
    }
    leaveShown();
    if (row.current) {
      setOpenPopover(null);
      setWanted(row.agent);
      return;
    }
    if (row.agent === GAMMA) {
      if (await openHistory(row.id)) setWanted(GAMMA);
      return;
    }
    try {
      await openComputerChat(row.id);
    } catch (err) {
      setStatus(t("Couldn't open the conversation: {message}", { message: err.message }));
      setHistoryRead("");
      return;
    }
    setOpenPopover(null);
    setHistoryRead("");
    chatStickRef.current = true;
    if (agent?.id === row.agent || (agent?.agentId && agent.agentId === row.agent)) side.chat.reload();
    else setWanted(row.agent);
  }

  // A session of History's sessions opened: the bucket's active
  // conversation now (the server's: its turns read, bound to the session),
  // shown as an opened History row is; one Gamma continues already is that
  // conversation. One at a time: a second open of the same session would
  // bind it twice. Another computer's answers over its channel, up to 30 s:
  // meanwhile the row spins in its age's place and the status pill says
  // "Opening the session…" until the conversation is read.
  async function openSession(row) {
    if (openingSessionRef.current) return;
    const [name, dir] = sessionsKey.split("\n");
    openingSessionRef.current = true;
    setOpeningSession(row.id);
    setStatus(t("Opening the session…"));
    try {
      await openComputerSession({ bucket: chatKey, agent: name, dir, session: row.id });
    } catch (err) {
      setStatus(t("Couldn't open the session: {message}", { message: err.message }));
      setSessionsAsked((n) => n + 1);
      return;
    } finally {
      openingSessionRef.current = false;
      setOpeningSession("");
    }
    setOpenPopover(null);
    setHistoryRead("");
    chatStickRef.current = true;
    await side.chat.reload();
    setStatus("");
  }

  // A History row's conversation as a file to keep: Gamma AI's (the one
  // shown or current from this tab's copy), or a computer agent's from its
  // server.
  function exportRow(row) {
    if (row.agent === GAMMA) {
      exportConversation(row.active || row.current ? null : row);
      return;
    }
    const id = row.active ? side.chat.conversation?.id : row.id;
    if (id) {
      exportWith(() => downloadFetched(computerExportPath(id), {}, `${row.title || t("Untitled")}.md`),
        (message) => t("Couldn't export the conversation: {message}", { message }));
    }
  }

  // Rename commits on blur (Enter just blurs; Escape flags a cancel first),
  // so there is exactly one commit path for the input's disappearance.
  async function commitRename() {
    const edit = renaming;
    setRenaming(null);
    if (!edit || renameCancelRef.current) { renameCancelRef.current = false; return; }
    const title = edit.text.trim();
    try {
      if (edit.id === "") {
        // The title alone: the messages go through the session's saves.
        setChatTitle(title);
        await session.flush(chatKey);
        await apiJson(`${API}/chats/${encodeURIComponent(chatKey)}`,
          { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ title }) });
      } else {
        editHistory((prev) => prev.map((s) => (s.id === edit.id ? { ...s, title } : s)));
        await apiJson(`${API}/chat-history/${edit.id}`,
          { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ title }) });
      }
    } catch (err) {
      setStatus(t("Couldn't rename the conversation: {message}", { message: err.message }));
    }
  }

  function deleteHistory(entry) {
    const run = async () => {
      editHistory((prev) => prev.filter((s) => s.id !== entry.id));
      dropAgentRows([entry.id]);
      setPicked((prev) => { const next = new Set(prev); next.delete(entry.id); return next; });
      try {
        if (entry.agent === GAMMA) await apiJson(`${API}/chat-history/${entry.id}`, { method: "DELETE" });
        else await deleteComputerChat(entry.id);
      } catch (err) {
        setStatus(t("Couldn't delete the conversation: {message}", { message: err.message }));
        setHistoryRead("");
      }
    };
    askConfirm(deleteQuestion(entry, run));
  }

  // The ticked entries: Gamma AI's in one call (POST /chat-history/delete),
  // a computer agent's each. The conversations shown or current are never
  // among them: they have no history row to delete.
  function deletePicked() {
    const ids = [...picked];
    if (!ids.length) return;
    const theirs = archivedRows.filter((s) => picked.has(s.id) && s.agent !== GAMMA).map((s) => s.id);
    const ours = ids.filter((id) => !theirs.includes(id));
    const run = async () => {
      editHistory((prev) => prev.filter((s) => !ids.includes(s.id)));
      dropAgentRows(theirs);
      setPicked(new Set());
      try {
        await Promise.all([
          ours.length ? apiJson(`${API}/chat-history/delete`,
            { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ ids: ours }) }) : null,
          ...theirs.map(deleteComputerChat),
        ]);
      } catch (err) {
        setStatus(t("Couldn't delete the conversations: {message}", { message: err.message }));
        setHistoryRead("");
      }
    };
    askConfirm({
      title: T("Delete conversations"),
      message: tn("Delete {n} conversation from this chat's history? This can't be undone.",
        "Delete {n} conversations from this chat's history? This can't be undone.", ids.length),
      confirmLabel: t("Delete"), danger: true, onConfirm: run,
    });
  }

  // Rows of the history popover: the conversation shown first, then every
  // agent's others newest-first, each named by its agent while the chat has
  // several, filtered by the search box.
  const shownComputer = onComputer ? side.chat.conversation : null;
  const gammaVersion = session.version(chatKey) || "";
  const historyList = useMemo(() => matchRows(historyRows({
    agent: agent?.id || GAMMA, shownId: shownComputer?.id || "",
    shown: !onComputer ? { title: activeTitle, preview: deriveTitle(chatMessages) }
      : shownComputer ? { title: shownComputer.title || t("Untitled"), preview: "" } : null,
    gamma: history || [], computer: agentHistories.get(chatKey) || null,
    gammaNow: chatMessages.length ? { title: activeTitle, preview: deriveTitle(chatMessages), updated_at: gammaVersion } : null,
    agents: agentList,
  }), historyQuery), [history, agentHistories, chatKey, historyQuery, activeTitle, chatMessages, agent?.id, onComputer,
    shownComputer, gammaVersion, agentList]);
  // The rows a selection can hold: the listed archived ones ("Select all"
  // takes the search's results, not the whole history). A tick survives
  // only while its row is listed, so Delete never takes a hidden one.
  const archivedRows = useMemo(() => historyList.filter((s) => !s.active && !s.current && !s.lent), [historyList]);
  const listedKey = archivedRows.map((s) => s.id).join(",");
  useEffect(() => {
    const listed = new Set(listedKey ? listedKey.split(",") : []);
    setPicked((prev) => (prev.size && [...prev].some((id) => !listed.has(id))
      ? new Set([...prev].filter((id) => listed.has(id))) : prev));
  }, [listedKey]);

  // Attach picked/pasted files: pictures are stored at once, sized for the
  // model (POST /api/ai/pictures — the message keeps the URL, so the
  // picture outlives this tab and stays in the conversation), PDFs become
  // native attachments of the next message only (the library PDF button's
  // files go with every message instead).
  function addChatFiles(files, { pasted = false } = {}) {
    for (const f of files) {
      if (isPdfFile(f)) {
        if (f.size > 15 * 1024 * 1024) { setStatus(t("\"{name}\" is too large to attach (max 15 MB).", { name: f.name })); continue; }
        const reader = new FileReader();
        reader.onload = () => setChatFiles((prev) => prev.length >= 4 ? prev : [...prev, { name: f.name || "file.pdf", data: reader.result }]);
        reader.readAsDataURL(f);
      } else if (f.type?.startsWith("image/")) {
        if (!isPictureFile(f)) { setStatus(t("Image too large to attach (max 25 MB).")); continue; }
        postFile("/api/ai/pictures", f).then((stored) => {
          if (!stored?.url) { setStatus(t("Couldn't attach \"{name}\".", { name: f.name || t("image") })); return; }
          const picture = { kind: pasted ? "pasted" : "file", url: stored.url, width: stored.width, height: stored.height,
            ...(pasted || !f.name ? {} : { name: f.name }) };
          setChatImages((prev) => addPictures(prev, [picture], chatPictureBudget));
        });
      } else {
        setStatus(t("Can't attach \"{name}\" — only images and PDFs are supported.", { name: f.name }));
      }
    }
  }

  // Paste a figure (screenshot/image) into the chat input → attach it.
  function handleChatPaste(e) {
    const files = Array.from(e.clipboardData?.items || [])
      .filter((it) => it.type?.startsWith("image/"))
      .map((it) => it.getAsFile())
      .filter(Boolean);
    if (!files.length) return;
    e.preventDefault();
    addChatFiles(files, { pasted: true });
  }

  const chatFindMatches = useMemo(() => {
    const q = chatFind.trim().toLowerCase();
    if (!q) return [];
    return transcriptMessages.map((m, i) => ((m.text || "").toLowerCase().includes(q) ? i : -1)).filter((i) => i >= 0);
  }, [chatFind, transcriptMessages]);
  useEffect(() => { setChatFindIdx(0); }, [chatFind]);

  function gotoChatFind(n) {
    if (!chatFindMatches.length) return;
    const idx = ((n % chatFindMatches.length) + chatFindMatches.length) % chatFindMatches.length;
    setChatFindIdx(idx);
    const el = chatScrollRef.current?.querySelector(`[data-msg-idx="${chatFindMatches[idx]}"]`);
    el?.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  // Ctrl+F while focus is inside the chat opens find-in-chat (App's global
  // handler defers to us in that case) — not from the page picker, whose
  // own search box takes it.
  useEffect(() => {
    function onKey(e) {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "f"
          && document.activeElement?.closest?.(".chatPanel")
          && !document.activeElement.closest(".reportOverlay")) {
        e.preventDefault();
        setChatFindOpen(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Escape closes the page-picker modal wherever focus is.
  useEffect(() => {
    if (!docPicker) return;
    function onKey(e) { if (e.key === "Escape") setDocPicker(false); }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [docPicker]);

  // Follow the newest message only while stuck to the bottom (ChatGPT-style):
  // ANY upward scroll — wheel, scrollbar drag, touch — unsticks immediately,
  // even a small one; scrolling back down to the bottom re-sticks. Our own
  // scroll-to-bottom writes are flagged so their scroll events can't re-stick.
  const chatStickRef = useRef(true);
  const chatProgScrollRef = useRef(false);
  const chatLastScrollTopRef = useRef(0);
  useEffect(() => { chatStickRef.current = true; }, [chatKey]);
  // Another agent picked shows another conversation, which follows its end
  // as the one before did.
  useEffect(() => {
    const el = chatScrollRef.current;
    if (!el || !chatStickRef.current) return;
    const target = el.scrollHeight - el.clientHeight;
    if (el.scrollTop < target - 1) {
      chatProgScrollRef.current = true;
      el.scrollTop = target;
    }
  }, [transcriptMessages, busyHere, onComputer]);

  // What the message points at inside the notes: the chips, and the cursor
  // block's selection when it has one (first, as the server labels it S1).
  function pendingNotes() {
    const notes = chatNotes || [];
    const cursorSel = cursorChip?.sel ? [{ id: cursorChip.id, ...cursorChip.sel }] : [];
    return { notes, cursorSel };
  }

  // The /api/ai/chat request for `text` after `prevMessages`, from what the
  // composer holds now — the send and the context export build the same one.
  function chatRequest(text, prevMessages, { selectedDocs = chatDocs, includeNotes = chatIncludeNotes, attach = false, model = "", key = chatKey } = {}) {
    const { notes, cursorSel } = pendingNotes();
    const contextIds = [...new Set([focusedBlockId, ...selectedDocs].filter(Boolean))];
    return {
      prompt: text,
      page_id: focusedBlockId || "",
      // Only what the server replays: the text and the tool calls of
      // each turn, and a user message's pictures by URL (the server reads
      // the newest back within the picture budget; a data URL of an old
      // chat is never re-sent) — not the reports and counts saved with
      // them, nor the texts kept for reverting a change (failed replies
      // aren't answers).
      history: prevMessages.filter((m) => !m.error).map(({ role, text: turnText, actions: turnActions, reasoning, images: turnImages }) => {
        const sent = historyPictures(turnImages);
        return { role, text: turnText, ...(turnActions?.length ? { actions: forReplay(turnActions) } : {}),
          ...(reasoning ? { reasoning } : {}), ...(sent.length ? { images: sent } : {}) };
      }),
      chat_key: key, // the conversation, for the provider's prompt cache
      model: model || chatModel || "",
      selections: pdfSelections,
      focus_block_id: cursorChip ? cursorChip.id : "",
      // Note chips: attached blocks go as ids (the server serves their
      // current text, id-labelled, so the agent can edit them); selected
      // note text as exact source ranges — edit_block mode "selection"
      // rewrites only that.
      context_blocks: notes.filter((n) => n.kind === "block").map((n) => n.id),
      note_selections: [...cursorSel, ...notes.filter((n) => n.kind === "note")]
        .map((n) => ({ block_id: n.id, from: n.from, to: n.to, text: n.text })),
      // Handwriting selected on a page (the selection menu's Transcribe):
      // the strokes themselves, saved or not, in their group's space; the
      // server draws them where they lie (ai_context.request_ink_selections).
      ink_selections: notes.filter((n) => n.kind === "ink")
        .map((n) => ({ page_id: n.page_id, block_id: n.id, space: n.space, strokes: n.strokes })),
      attach_pdf: attach,
      effort,
      speed,
      system: chatSystem || "",
      pages: selectedDocs.length ? contextIds : [],
      include_notes: includeNotes,
      images: chatImages,
      max_pictures: chatPictureBudget,
      files: chatFiles,
      context_char_limit: chatContextChars,
      multi_context_char_limit: multiContextChars,
      ...agentPayload(prevMessages),
    };
  }

  // The header's download menu: the conversation as a file to keep, or the
  // context the model is sent.
  const [exporting, setExporting] = useState(false);
  const fileBase = activeTitle.replace(/[\\/:*?"<>|\s]+/g, " ").trim().slice(0, 80) || "chat";
  async function exportWith(run, failed) {
    setExporting(true);
    try {
      await run();
    } catch (err) {
      setStatus(failed(err.message));
    } finally {
      setExporting(false);
    }
  }

  // Download what the model would be sent now — system prompt, tools, every
  // turn with the pages' context, the draft as the last message — as Markdown.
  const exportContext = () => exportWith(() => downloadFetched(`${API}/ai/chat/context`, {
    method: "POST", headers: JSON_HEADERS,
    body: JSON.stringify({ ...chatRequest(chatInput.trim(), chatMessages), title: activeTitle }),
  }, `${fileBase} - context.md`), (message) => t("Couldn't export the chat context: {message}", { message }));

  // A conversation as a file to keep: the transcript as Markdown with every
  // picture the chat shows beside it (a .zip with assets/; the bare .md when
  // it has none — docs/dev/chat_history.md "Exporting a conversation"). The
  // open one goes from this tab's copy, so a viewer's unsaved chat and a
  // reply still in the autosave debounce come along; an archived one by id.
  const exportConversation = (entry = null) => exportWith(() => (entry
    ? downloadFetched(`${API}/chat-history/${encodeURIComponent(entry.id)}/export`, {}, `${entry.title || "chat"}.md`)
    : downloadFetched(`${API}/chats/export`, {
      method: "POST", headers: JSON_HEADERS,
      body: JSON.stringify({ bucket: chatKey, title: chatTitle, messages: chatMessages }),
    }, `${fileBase}.md`)), (message) => t("Couldn't export the conversation: {message}", { message }));

  // Anything of the user's waiting in the composer: a fetch handoff's card
  // does not send its continuation over it (chat/FetchHandoffCards.jsx).
  const composerHasDraft = !!(chatInput.trim() || chatImages.length || chatFiles.length
    || pdfSelections.length || chatNotes?.length);

  // Core chat send. baseMessages overrides the history (used when re-sending
  // an edited message: everything after the edited message is discarded,
  // ChatGPT-style).
  async function sendChat(rawText, { baseMessages, referenceMessage, model } = {}) {
    if (readOnly || aiOff) return;
    const text = (rawText || "").trim();
    if (!text || busyHere) return;
    // pdf: the whole file goes with this message (the PDF switch's hint).
    guideEvents.emit("chat.sent", { pdf: !!(pdfInContext && attachPdf && nativePdf) });
    const selectedDocs = referenceMessage ? (referenceMessage.contextPages || []).map((p) => p.id) : chatDocs;
    const includeNotes = referenceMessage ? !!referenceMessage.includeNotes : chatIncludeNotes;
    if (referenceMessage) { setChatDocs(selectedDocs); setChatIncludeNotes(includeNotes); }
    const selections = pdfSelections;
    const selection = selections.map((s) => s.text).join("\n\n---\n\n");
    setPdfSelections([]);
    const { notes, cursorSel } = pendingNotes();
    setChatNotes?.([]);
    if (cursorSel.length) onSelectionSent?.();
    const images = chatImages;
    setChatImages([]);
    const files = chatFiles;
    setChatFiles([]);
    const prevMessages = baseMessages ?? chatMessages;
    const quoted = [selection, ...cursorSel.map((n) => n.text), ...notes.map((n) => n.text)].filter(Boolean).join("\n\n---\n\n");
    const shown = quoted ? `${text}\n\n> ${quoted.slice(0, 280)}${quoted.length > 280 ? "…" : ""}` : text;
    // Names of PDFs that ride along with THIS message (displayed in the bubble)
    const contextIds = [...new Set([focusedBlockId, ...selectedDocs].filter(Boolean))];
    const pdfPages = contextIds.flatMap((id) => {
      const page = homeBlocks.find((b) => b.id === id);
      const file = id === focusedBlockId ? (isPdf(doc) ? pdfFile : null) : pdfFileOf(page);
      return file != null ? [{ id: file, title: page?.content || (id === focusedBlockId ? pageTitle : "") || t("Untitled") }] : [];
    });
    const sendingPdf = attachPdf && pdfPages.length > 0;
    const pdfNames = [
      ...files.map((f) => f.name),
      ...(sendingPdf ? pdfPages.map((p) => p.title) : []),
    ];
    const contextPages = selectedDocs.map((id) => ({ id, title: homeBlocks.find((b) => b.id === id)?.content || t("Untitled") }));
    // Messages carry an id: two copies of one conversation (another tab, a
    // member) merge by it (chatSession.mergeChats), a streamed reply's
    // versions included.
    const userMsg = {
      id: makeId(),
      contextPages,
      includeNotes,
      role: "user",
      text: shown,
      ...(images.length ? { images } : {}),
      // pdfDocs records WHICH documents rode along, so reloading the page
      // can tell whether this document was already sent in the conversation
      // (uploaded files aren't library docs — they contribute names only).
      ...(pdfNames.length ? { pdfs: pdfNames, pdfDocs: sendingPdf ? pdfPages.map((p) => p.id) : [] } : {}),
    };
    const sendKey = chatKey; // reply belongs to THIS conversation, even if the user navigates away
    const showReply = (aiMsg, final) => {
      const saved = session.update(sendKey, [...prevMessages, userMsg, aiMsg], final);
      saved?.catch((err) => setStatus(t("Couldn't save the conversation: {message}", { message: err.message })));
    };
    chatStickRef.current = true; // sending always snaps back to the bottom
    const ctrl = new AbortController();
    if (!session.start(sendKey, [...prevMessages, userMsg], chatTitle, ctrl)) return;
    noteReview.replying(sendKey, true);
    // Under Unlimited a reply runs until it is done or stopped. A row in
    // Background tasks shows it, and its Stop reaches it from any folder or
    // page (stopChat only stops the conversation on screen). The row follows
    // what the request asks: a chat with no tools in scope sends a plain chat.
    const taskId = agentPayload(prevMessages).tool_rounds_unlimited ? addTask?.({
      kind: "ai", info: t("Unlimited rounds"), cancel: () => session.stop(sendKey),
      name: t("AI reply — {title}", {
        title: (chatTitle || deriveTitle([...prevMessages, userMsg]) || t("Untitled")).slice(0, 48) }),
    }) : null;
    let acc = ""; // streamed reply so far — kept on Stop
    const actions = []; // organizer mutations streamed for this reply
    let coverage = null; // {"context": [...]} — what the model was given, per document
    let answered = null; // {"model": {id, name, effort, speed}} — which model answers, how
    let usage = null; // the provider's token report, summed over the reply's rounds
    let lastRound = null; // the latest round's report alone — the context ring's figure
    let liveChars = 0; // characters received since the last report — the running estimate
    let running = null; // the tool call running now ({"step"} line), until its action lands
    let helpers = []; // the helpers that call started ({"helper"} lines), each one's latest state
    let approval = null; // its approval card ({"approval"} line), while the user decides
    const handoffs = []; // blocked papers this reply waits on ({"handoff"} lines)
    let trimmed = null; // {"trimmed": {turns}} — oldest messages left out to fit the window
    let shortened = null; // {"shortened": {items, budget, asked}} — passages that went short
    let truncated = false; // {"truncated": true} — the reply hit the output limit
    let limit = null; // {"limit": {kind, rounds, changes, withheld?}} — a limit stopped the reply
    let reasoning = null; // {"reasoning"} lines — the model's thinking, sent back on later turns
    const liveArgs = new Map(); // tool call id -> argument chars previewed so far (cumulative)
    const replyId = makeId(); // every version of this reply, partial or final, is one message
    const aiMsg = (extra = {}) => ({
      id: replyId, role: "ai", text: acc,
      ...(actions.length ? { actions: [...actions] } : {}),
      ...(coverage ? { context: coverage } : {}),
      ...(answered ? { model: answered.name, ...(answered.effort ? { effort: answered.effort } : {}),
        ...(answered.speed ? { speed: answered.speed } : {}),
        ...(answered.tools ? { tools: true } : {}) } : {}),
      ...(answered?.tools_off ? { toolsOff: answered.tools_off } : {}),
      ...(usage ? { usage } : {}),
      ...(lastRound ? { context_tokens: (lastRound.input || 0) + (lastRound.output || 0) } : {}),
      ...(trimmed ? { trimmed } : {}),
      ...(shortened ? { shortened } : {}),
      ...(truncated ? { truncated: true } : {}),
      ...(limit ? { limit } : {}),
      ...(reasoning ? { reasoning } : {}),
      ...extra,
    });
    try {
      const res = await fetch(`${API}/ai/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        signal: ctrl.signal,
        // chatRequest reads this render's composer state — the clears above
        // only change it for the next render.
        body: JSON.stringify({
          ...chatRequest(text, prevMessages, { selectedDocs, includeNotes, attach: sendingPdf, model, key: sendKey }),
          stream: true,
        }),
      });
      if (!res.ok) {
        // The body names the failure's kind beside its detail (the server's
        // failure_kind) — the error card's headline and fix come from it.
        let body = {};
        try { body = await res.json(); } catch {}
        throw chatFailure(body.detail || `${res.status} ${res.statusText}`, body);
      }
      // NDJSON stream: {"delta": "…"} per chunk, {"error": "…"} on failure.
      await readNdjson(res, (events) => {
        for (const ev of events) {
          if (ev.error) throw chatFailure(ev.error, ev);
          if (ev.step) {
            running = ev.step;
            helpers = [];
          } else if (ev.helper) {
            helpers = withHelper(helpers, ev.helper);
          } else if (ev.approval) {
            approval = ev.approval;
            // A note edit's card: the note draws the edit too, and its bar
            // answers the card with the card's four answers.
            const asked = ev.approval;
            noteReview.ask(asked, (decision, note) => decideRef.current(asked, decision, note),
              { permission: permissionLabel(asked.perm), kind: chatKindLabel });
          } else if (ev.handoff) {
            // A fetch met a wall: the reply waits on this card until the
            // user's browser sends the PDF, or they skip it.
            handoffs.push(ev.handoff);
          } else if (ev.action) {
            running = null;
            helpers = [];
            if (approval) noteReview.settle(approval.id);
            approval = null;
            actions.push(ev.action);
            // Live: the notes panel lights up the block the agent just
            // read/edited (and reloads the tree for an applied edit), and the
            // note shows what an edit changed until the user keeps or
            // reverts it (chat/noteReview.js).
            onAgentEvent?.({ type: "action", action: ev.action });
            noteReview.landed({ action: ev.action, key: sendKey, reply: replyId, index: actions.length - 1 });
          } else if (ev.progress) {
            // The agent is still writing a note edit — the block types it in.
            onAgentEvent?.({ type: "progress", ...ev.progress });
            const seen = liveArgs.get(ev.progress.id) || 0;
            const now = (ev.progress.content || "").length;
            if (now > seen) { liveChars += now - seen; liveArgs.set(ev.progress.id, now); }
          } else if (ev.context) {
            coverage = ev.context;
          } else if (ev.model) {
            answered = ev.model;
            // The first chat with tools found the model can't call them: the
            // chip and the menu label say so from now on.
            if (ev.model.tools_off && (aiInfo?.models || []).find((x) => x.id === ev.model.id)?.tools !== false) {
              refreshAiModels?.();
            }
          } else if (ev.trimmed) {
            trimmed = ev.trimmed;
          } else if (ev.shortened) {
            shortened = ev.shortened;
          } else if (ev.truncated) {
            truncated = true;
          } else if (ev.limit) {
            limit = ev.limit;
          } else if (ev.reasoning) {
            reasoning = addReasoning(reasoning, ev.reasoning);
          } else if (ev.usage) {
            // The round is counted for real now; the estimate starts over.
            usage = addUsage(usage, ev.usage);
            lastRound = ev.usage;
            liveChars = 0;
            liveArgs.clear();
          } else {
            acc += ev.delta || "";
            liveChars += (ev.delta || "").length;
          }
        }
        if (acc || actions.length || usage || running || handoffs.length) {
          showReply(aiMsg({ partial: true, live: liveChars, ...(running ? { step: running } : {}),
            ...(helpers.length ? { helpers } : {}),
            ...(approval ? { approval } : {}),
            ...(handoffs.length ? { handoffs: [...handoffs] } : {}) }));
        }
      });
      showReply(aiMsg({ text: acc || (actions.length ? "" : t("(no response)")) }), true);
      if (gammaLinksIn(acc).some((link) => link.kind === "citation")) guideEvents.emit("chat.cited");
      // The reply changed the library or notes: its list of changes, each
      // with its own undo, is on screen.
      if (actions.some(isChange)) guideEvents.emit("chat.changed");
    } catch (err) {
      const stopped = err?.name === "AbortError";
      // fetch's own TypeError ("Failed to fetch", or the body reader's
      // "network error") means the connection to the server was lost, not
      // that the provider failed — the server says that in-band.
      const reason = err?.name === "TypeError"
        ? t("lost the connection to the server ({message})", { message: err.message }) : err.message;
      // A reply that never started is an error (`error: true`): shown as a
      // card and saved so the failure is visible after a reload, but never
      // replayed to the model as an answer. A reply cut off mid-stream keeps
      // its text and gets the compact card under it. errorKind & co. carry
      // what the card says (chat/chatErrors.js).
      showReply(aiMsg(stopped
        ? { text: acc ? `${acc}\n\n*(stopped)*` : "*(stopped)*" }
        : { text: acc || `Error: ${reason}`, ...(acc ? {} : { error: true }), ...failureFields(err) }), true);
    } finally {
      if (taskId) removeTask?.(taskId);
      session.finish(sendKey);
      onAgentEvent?.({ type: "done", key: sendKey });
      // A card the reply left waiting (stopped, or the stream failed) goes from its note too.
      if (approval) noteReview.settle(approval.id);
      noteReview.replying(sendKey, false);
      // Agent tools changed the library — reload the home feed. Read-only
      // tool calls (list/read/search) render as chips but change nothing.
      if (actions.some(isChange)) onLibraryChange?.();
      // Note-block edits carry the page(s) they touched, so the open page's
      // block tree can reload and show the change.
      const notePages = [...new Set(actions
        .filter((a) => !a.error && BLOCK_TOOLS.includes(a.tool))
        .flatMap((a) => [a.page_id, a.src_page_id].filter(Boolean)))];
      if (notePages.length) onNotesChange?.(notePages);
    }
  }

  sendChatRef.current = sendChat;

  // App's asks go out once the chat knows its AI and holds its stored
  // conversation: sent as the user's message, or left in the composer while
  // a reply is still streaming here. Each ask goes once, however often the
  // dock mounts.
  useEffect(() => {
    if (!askSignal?.id || askSignal.id === kept.asked || !aiInfo || loadedFor !== chatKey) return;
    kept.asked = askSignal.id;
    if (aiOff || readOnly) return;
    setWanted(GAMMA); // an ask from App is Gamma AI's
    if (busyHere) setChatInput(askSignal.text);
    else sendChatRef.current?.(askSignal.text);
  }, [askSignal, aiInfo, aiOff, readOnly, busyHere, loadedFor, chatKey]);

  // The composer's message, to the agent shown: a computer's agent takes
  // it with the PDF passages selected, and the draft stays until the server
  // made its turn.
  async function sendChatMessage() {
    const text = chatInput;
    if (onComputer) {
      chatStickRef.current = true;
      if (await side.send(text)) setChatInput("");
      return;
    }
    if (!text.trim() || busyHere) return;
    setChatInput("");
    sendChat(text);
  }
  sendTextRef.current = onComputer ? side.send : sendChat;

  function stopChat() {
    session.stop(chatKey);
  }

  // The recordings' recorder (recording/recorder.js), in one file: the
  // browser's container, or WAV on an iPad or an iPhone.
  async function startDictation() {
    if (dictation) return;
    if (!canRecord()) {
      setStatus(t("Voice input needs HTTPS or localhost — the browser blocks the microphone on plain HTTP."));
      return;
    }
    const context = gestureContext(); // inside the tap, before the wait (recorder.js)
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      context?.close().catch(() => {});
      setStatus(t("Microphone access was denied."));
      return;
    }
    const rec = {
      blob: null, canceled: false, autoSend: false,
      baseText: chatInput, // composer text at record start (the input is hidden while recording)
      amps: [], peak: 0,
    };
    rec.recorder = createRecorder(stream, { context, segmentMs: 0, onSegment: ({ blob }) => { rec.blob = blob; } });
    rec.recorder.start();
    rec.analyser = rec.recorder.analyser; // feeds the live waveform; recording works without it
    recRef.current = rec;
    setRecSecs(0);
    setDictation("rec");
  }

  // mode: "cancel" discards, "insert" transcribes into the input (■),
  // "send" transcribes and sends immediately (↑) — ChatGPT's dictation pair.
  function finishDictation(mode) {
    const rec = recRef.current;
    if (!rec || dictation !== "rec") return;
    rec.canceled = mode === "cancel";
    rec.autoSend = mode === "send";
    setDictation(rec.canceled ? "" : "busy");
    rec.recorder.stop().then(() => {
      if (!rec.canceled) transcribeDictation(rec.blob || new Blob([], { type: rec.recorder.mime }), rec);
    });
    if (rec.canceled) recRef.current = null;
  }

  async function transcribeDictation(blob, rec) {
    try {
      if (blob.size > 24 * 1024 * 1024) throw new Error("recording too long (max ~25 MB)");
      const form = new FormData();
      form.append("file", blob, `dictation${extensionFor(blob.type)}`);
      if (dictationModel) form.append("model", dictationModel);
      const language = dictationLang === "auto" ? "" : dictationLang || getLocale();
      if (language) form.append("language", language);
      if (chatModel) form.append("model_hint", chatModel);
      const data = await apiJson(`${API}/ai/transcribe`, { method: "POST", body: form });
      const text = (data.text || "").trim();
      if (text && rec.autoSend) {
        setChatInput("");
        sendTextRef.current?.([rec.baseText.trim(), text].filter(Boolean).join(" "));
      } else if (text) {
        setChatInput((prev) => (prev.trim() ? `${prev.replace(/\s+$/, "")} ${text}` : text));
      }
    } catch (e) {
      setStatus(t("Voice input: {message}", { message: e.message }));
    } finally {
      setDictation("");
      recRef.current = null;
    }
  }

  // Live waveform, drawn ChatGPT-style: newest audio as bars at the right
  // edge next to a cursor line, history scrolling left, silence as dots.
  useEffect(() => {
    if (dictation !== "rec") return;
    const canvas = waveCanvasRef.current;
    const rec = recRef.current;
    if (!canvas || !rec?.analyser) return;
    const ctx = canvas.getContext("2d");
    const data = new Uint8Array(rec.analyser.fftSize);
    const barColor = getComputedStyle(canvas).color; // fixed for the recording
    let raf, lastPush = 0;
    const draw = (now) => {
      raf = requestAnimationFrame(draw);
      rec.analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (let i = 0; i < data.length; i++) { const v = (data[i] - 128) / 128; sum += v * v; }
      rec.peak = Math.max(rec.peak, Math.sqrt(sum / data.length));
      if (now - lastPush >= 50) { // one slot ≈ 50 ms of audio
        rec.amps.push(rec.peak);
        rec.peak = 0;
        lastPush = now;
      }
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth, h = canvas.clientHeight;
      if (!w || !h) return;
      if (canvas.width !== Math.round(w * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      ctx.strokeStyle = barColor;
      ctx.lineWidth = 2;
      ctx.lineCap = "round";
      const cursorX = w - 3;
      ctx.globalAlpha = 0.9;
      ctx.beginPath(); ctx.moveTo(cursorX, h * 0.12); ctx.lineTo(cursorX, h * 0.88); ctx.stroke();
      const pitch = 5;
      for (let i = 0; i < Math.floor((cursorX - 4) / pitch); i++) {
        const amp = rec.amps[rec.amps.length - 1 - i];
        if (amp === undefined) break;
        const x = cursorX - (i + 1) * pitch;
        // sqrt lifts normal speech into view; the floor keeps a dotted
        // baseline running through silence.
        const len = Math.max(2, Math.min(1, Math.sqrt(amp) * 2.2) * h * 0.78);
        ctx.globalAlpha = len <= 2.5 ? 0.3 : 0.7;
        ctx.beginPath();
        ctx.moveTo(x, (h - len) / 2);
        ctx.lineTo(x, (h + len) / 2);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [dictation]);

  // Recording timer + drop the mic on unmount (stops the tracks so the
  // browser's recording indicator doesn't linger).
  useEffect(() => {
    if (dictation !== "rec") return;
    const timer = setInterval(() => setRecSecs((s) => s + 1), 1000);
    return () => clearInterval(timer);
  }, [dictation]);
  useEffect(() => () => {
    const rec = recRef.current;
    if (rec) {
      rec.canceled = true;
      rec.recorder.stop();
    }
  }, []);

  // Edit-and-resend (ChatGPT-style): user message `idx` goes again as
  // `text`, and it and everything after it are replaced; `model` overrides
  // the chat's pick.
  function resendFrom(idx, text, model) {
    sendChat(text, { baseMessages: chatMessages.slice(0, idx), referenceMessage: chatMessages[idx], model });
  }

  // Re-send the user message a failed reply answered; `model` retries with
  // another model.
  function retryReply(idx, model) {
    const asked = chatMessages[idx - 1];
    if (asked?.role !== "user" || busyHere) return;
    resendFrom(idx - 1, asked.text, model);
  }

  // An error card's buttons: the fix for its kind (open the failing
  // connection's form, connect a service, start over), and on the latest
  // reply Retry and a model switch that retries with the model picked. A
  // computer agent's failed turn has Retry alone, on the newest.
  function errorActions(m, idx) {
    if (onComputer) {
      const retry = side.retry(idx);
      return retry ? [<button key="retry" type="button" className="uiBtn sm" onClick={retry}>{t("Retry")}</button>] : [];
    }
    const copy = failureCopy(m.errorKind, { provider: m.errorProvider, auth: m.errorAuth });
    const latest = idx === chatMessages.length - 1 && !busyHere && !readOnly;
    const out = [];
    const openEntry = () => openAiKeysEditor({ entry: m.errorProviderId });
    const fix = {
      connect: openAiKeysEditor, own_key: openAiKeysEditor, key: openEntry, signin: openEntry,
      connection: openEntry, proxy: isAdmin && openSettings ? () => openSettings("proxy") : null,
      new_chat: latest ? newChat : null,
    }[copy.fix];
    if (fix && !readOnly) {
      out.push(<button key="fix" type="button" className="uiBtn sm primary" {...(fix === newChat ? {} : settingsIntent)}
        onClick={() => fix()}>{fixLabel(copy.fix)}</button>);
    }
    if (latest) {
      out.push(<button key="retry" type="button" className="uiBtn sm" onClick={() => retryReply(idx)}>{t("Retry")}</button>);
      const others = copy.switchModel ? headerModels.filter((x) => x.id !== chatModel) : [];
      if (others.length) {
        out.push(<ActionMenu key="switch" label={t("Switch model")} items={others.map((x) => ({
          label: x.model,
          title: t("Switch to this model and retry"),
          onClick: () => { setChatModel(x.id); retryReply(idx, x.id); },
        }))} />);
      }
    }
    return out;
  }

  async function copyChatMessage(idx, text) {
    if (await copyText(text || "")) flashCopiedMsg(idx);
  }

  // Header: one icon strip (the PDF zoom column's buttons, laid flat), the
  // window's actions (DockWindow), which fold into its "⋯" when it is too
  // narrow for them — Export (the conversation or its context), the context
  // ring (opens the settings popover, whose Tokens section spells it out),
  // ⚙ chat settings (context size, the Tools switch and the tools'
  // permissions, tokens — the same prefs Settings / AI edits, in a popover;
  // the model and effort are the composer's model chip), Find, History, New
  // chat. Find, History and New chat fold last, New chat the very last
  // (`keep`).
  const settingsOpen = openPopover === "chatsettings";
  const findAction = {
    id: "find", icon: SearchIcon, label: t("Find in this conversation"), on: chatFindOpen, keep: 1,
    onClick: () => { setChatFindOpen((v) => !v); setChatFind(""); },
  };
  // No AI connected: the setup card, alone in Gamma AI's empty transcript
  // or after an earlier conversation, once the tiles it offers are known,
  // with the agents found on a computer beside them: those the chat has
  // (picked with a click) and those found here but not turned on.
  const setupCard = closed && setupInfo ? (
    <ChatSetupCard info={setupInfo} isAdmin={isAdmin} openSettings={openSettings} intent={settingsIntent}
      onConnect={(service) => openAiKeysEditor({ service })}
      agents={agentList.filter((a) => a.kind === "computer")} onPick={setWanted}
      found={(localAgents?.agents || []).filter((a) => a.found && a.code !== "missing" && !a.enabled)} />
  ) : null;
  // Alt over New chat turns it into Discard: only when there is a saved
  // conversation to lose (a viewer's chat is never kept, an empty one has
  // nothing to drop).
  const discardArmed = newChatAlt && canSave && chatMessages.length > 0;
  // Another agent picked in the composer's chip (AgentChip). A conversation
  // keeps the agent it began with: in one with messages the pick asks
  // first, and on yes the agent picked starts a new conversation (its
  // side's active one archived) while the one left stays where it was.
  function chooseAgent(id) {
    const next = agentList.find((a) => a.id === id);
    const shown = onComputer ? side.chat.conversation?.messages?.length > 0 : chatMessages.length > 0;
    if (!next || next.id === agent?.id) return;
    if (!asksNewChat(agent, next, shown)) { setWanted(next.id); return; }
    askConfirm({
      ...newChatQuestion(next), confirmLabel: t("New chat"),
      onConfirm: async () => {
        if (next.kind === "computer") {
          try {
            await newComputerChat(chatKey);
          } catch (err) {
            setStatus(t("Couldn't start a new chat: {message}", { message: err.message }));
            return;
          }
        }
        setWanted(next.id);
        if (next.kind === "gamma" && chatMessages.length) await newChat();
      },
    });
  }
  const totalUsage = conversationUsage(chatMessages);
  const usageTitle = totalUsage ? t("; this conversation: {input} tokens in, {output} out", { input: fmtTokens(totalUsage.input), output: fmtTokens(totalUsage.output) }) : "";
  const ctxText = !ctxUsed ? ""
    : ctxWindow ? t("Context: {used} of {size} tokens ({percent}%)", { used: fmtTokens(ctxUsed), size: fmtTokens(ctxWindow), percent: Math.round((ctxUsed / ctxWindow) * 100) })
    : t("Context: {used} tokens", { used: fmtTokens(ctxUsed) });
  const toggleSettings = () => setOpenPopover((p) => (p === "chatsettings" ? null : "chatsettings"));
  // History: every agent's conversations of the bucket (historyList). A row
  // opens its conversation with its agent picked; while rows are ticked it
  // ticks instead. Its menu renames (Gamma AI's), exports, selects and
  // deletes; another agent's current conversation and a co-author's
  // (`lent`, named by whose it is) are only opened or exported, and a
  // computer agent's other rows wait while its turn is to end, which the
  // server would refuse.
  const waits = (row) => (!row.current && !row.lent && row.agent !== GAMMA && side.working
    ? t("Stop {agent} or wait for it to finish to open another conversation", { agent: side.label }) : "");
  const listedSessions = sessionsKey && sessionsList.key === sessionsKey ? sessionsList
    : { sessions: null, error: "", away: "", reading: false };
  // The sessions under "Codex sessions in paper" (" on TIM-PC" for another
  // computer's agent): a press on one that cannot open says why in the
  // status pill, a busy one says it under its title too, and the caption's
  // refresh reads them again in place (a terminal closed meanwhile).
  const readSessions = () => setSessionsAsked((n) => n + 1);
  const sessionsSaid = sessionsKey ? sessionsWords(side.info, side.label, side.place, side.where) : null;
  const sessionsSection = sessionsKey ? {
    label: sessionsSaid.caption, empty: sessionsSaid.empty,
    rows: matchRows(sessionRows(listedSessions.sessions, side.label), historyQuery),
    loading: listedSessions.sessions == null, reading: listedSessions.reading, onRefresh: readSessions,
    onOpen: openSession, opening: openingSession,
    error: listedSessions.error || sessionsAway(listedSessions.away, side.where),
    onRetry: readSessions, icon: agentList.length > 1 ? agentIcon(agent.id) : null,
    disabledReason: (row) => row.busy || (side.working
      ? t("Stop {agent} or wait for it to finish to open another conversation", { agent: side.label })
      : picked.size > 0 ? t("Clear the selection to open a session")
      : openingSession ? t("Opening the session…") : ""),
  } : null;
  const historyAction = canSave && {
    id: "history", icon: HistoryIcon, label: t("Chat history"), guide: "chat.history", keep: 2, open: historyOpen,
    title: t("Chat history — earlier conversations of {scope}", { scope: folderChat ? t("this folder") : t("this page") }),
    onClick: () => setOpenPopover((p) => (p === "chathistory" ? null : "chathistory")),
    popover: { name: "chathistory", content: historyOpen ? (
      <ChatHistoryPop query={historyQuery} onQuery={setHistoryQuery} onClose={() => setOpenPopover(null)}
        rows={historyList} loading={history == null} picking={picked.size > 0}
        onOpen={pickOrOpen} disabledReason={waits} onRefused={setStatus} section={sessionsSection}
        icon={agentList.length > 1 ? (s) => agentIcon(s.agent) : undefined}
        tip={(s) => `${s.preview || s.title}${s.count ? ` · ${s.count} messages` : ""}`}
        lead={(s) => (s.active || s.current || s.lent ? null : (
          <input type="checkbox" className="chatHistPick" checked={picked.has(s.id)}
            aria-label={t("Select this conversation")} title={t("Select for deleting")}
            onClick={(e) => e.stopPropagation()}
            onChange={() => togglePicked(s.id)} />
        ))}
        menuItems={(row, close) => <>
          {row.agent === GAMMA && !row.current ? (
            <MenuItem icon={PenIcon} onClick={() => {
              close();
              renameCancelRef.current = false;
              setRenaming({ id: row.id, text: row.active ? chatTitle : row.title || "" });
            }}>{t("Rename")}</MenuItem>
          ) : null}
          <MenuItem icon={DownloadIcon} title={t("Download this conversation as Markdown, with its pictures")}
            onClick={() => { close(); exportRow(row); }}>{t("Export")}</MenuItem>
          {row.active || row.current || row.lent ? null : <>
            {picked.has(row.id) ? null : (
              <MenuItem icon={SquareCheckIcon} onClick={() => { close(); togglePicked(row.id); }}>{t("Select")}</MenuItem>
            )}
            <MenuItem icon={TrashIcon} danger onClick={() => { close(); deleteHistory(row); }}>{t("Delete")}</MenuItem>
          </>}
        </>}
        editing={(s) => (renaming?.id === s.id ? (
          <div key={s.id} className="chatHistRow renaming">
            <input
              autoFocus
              className="aiKeyInput"
              value={renaming.text}
              placeholder={t("Conversation name")}
              enterKeyHint="done"
              onChange={(e) => setRenaming({ id: s.id, text: e.target.value })}
              onKeyDown={(e) => {
                if (composing(e)) return;
                if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur(); }
                else if (e.key === "Escape") { e.preventDefault(); renameCancelRef.current = true; e.currentTarget.blur(); }
              }}
              onBlur={commitRename}
            />
          </div>
        ) : null)}>
        {picked.size ? (
          <div className="chatHistBulk">
            <span>{tn("{n} selected", "{n} selected", picked.size)}</span>
            {picked.size < archivedRows.length ? (
              <button type="button" className="uiBtn sm ghost"
                onClick={() => setPicked(new Set(archivedRows.map((s) => s.id)))}>{t("Select all")}</button>
            ) : null}
            <button type="button" className="uiBtn sm ghost" onClick={() => setPicked(new Set())}>{t("Clear")}</button>
            <button type="button" className="uiBtn sm danger" onClick={deletePicked}>{t("Delete")}</button>
          </div>
        ) : null}
      </ChatHistoryPop>
    ) : null },
  };
  // With a computer's agent: its conversation exported, Find, History, and
  // New chat, which keeps the conversation in History; neither of the last
  // while a turn is still to end (the server refuses them then, and their
  // titles say why). While a comment thread's or a co-author's conversation
  // is shown New chat is off too, its title saying why: the note in the
  // composer's place goes back to the page's own chat.
  const shownWhy = side.shown?.thread ? t("This chat answers a comment: go back to the chat to start a new one")
    : side.shown ? t("This is {person}'s chat: go back to your own to start a new one", {
      person: side.chat.conversation?.person || t("Someone") }) : "";
  const computerActions = () => [
    { id: "export", icon: DownloadIcon, label: t("Export conversation"), title: t("Download this conversation as Markdown"),
      disabled: exporting || !side.chat.conversation,
      onClick: () => exportRow({ agent: agent.id, active: true, title: side.chat.conversation?.title || "" }) },
    findAction,
    historyAction,
    { id: "new", icon: PlusIcon, label: t("New chat"), guide: "chat.new", keep: 3,
      disabled: !!side.shown || side.working || side.chat.busy || !side.chat.conversation, onClick: () => side.chat.newChat(),
      title: shownWhy || (side.working ? t("Stop {agent} or wait for it to finish to start a new chat", { agent: side.label })
        : t("New chat — keeps this conversation in history and starts a fresh one")) },
  ];
  const actions = readOnly ? [findAction] : onComputer ? computerActions() : aiOff || !aiKnown ? [] : [
    { id: "export", icon: DownloadIcon, label: t("Export chat"), title: t("Export this conversation or the context the model is sent"),
      disabled: exporting, items: [
        { icon: MessageSquareIcon, label: t("Export conversation"), title: t("Download this conversation as Markdown, with its pictures"),
          disabled: !chatMessages.length, onClick: () => exportConversation() },
        { icon: BotIcon, label: t("Export context"), onClick: exportContext,
          title: t("Export context — download what the model is sent (system prompt, pages, every message and your draft) as Markdown") },
      ] },
    headerModels.length > 0 && ctxUsed && ctxWindow && {
      id: "context", glyph: <UsageRing fraction={ctxUsed / ctxWindow} />, label: ctxText, onClick: toggleSettings, popover: { name: "chatsettings" },
      title: t("{ctxText} — the last reply's prompt and answer in {model}'s context window", { ctxText, model: headerModel.model }),
    },
    headerModels.length > 0 && {
      id: "settings", icon: SettingsIcon, label: t("Chat settings"), guide: "chat.settings", open: settingsOpen, onClick: toggleSettings,
      title: t("Chat settings — context {chatContextChars} chars, tools, tokens{usageTitle}", { chatContextChars: chatContextChars.toLocaleString(), usageTitle }),
      popover: { name: "chatsettings", content: settingsOpen ? (
        <Popover className="chatSettingsPop">
          <div className="popoverHint">{t("These preferences also appear in Settings and follow your account. Choose the model and reasoning effort on the message box.")}</div>
          <div className="popoverSection">{t("Context per page · {pages}", { pages: approxPages(chatContextChars) })}</div>
          <CharSlider value={chatContextChars} onChange={setChatContextChars} />
          <div className="popoverHint">
            {t("Extracted PDF text sent with each message. The multi-paper total is in Settings / AI / Chat, the agent's read window in Settings / AI / Tool usage.")}
          </div>
          {/* The account's master switch for tools (Settings → AI → Tool usage's Assistant tools), then this kind's permissions. */}
          <div data-guide="chat.tools">
            <Toggle icon={SparklesIcon} label={t("Tools")} hint={t("Allow tools in all chats")} checked={toolsEnabled} onChange={setAgentEnabled}
              title={t("Tools {off} for all chats - click to change the global setting", { off: toolsEnabled ? t("on") : t("off") })} />
          </div>
          <div className="chatToolPicker">
            <AgentToolPicker kind={chatKind} perms={agentPerms} setPerms={setAgentPerms} disabled={!toolsEnabled} />
          </div>
          <div className="popoverHint">
            {t("Applies to all {kind} conversations on your account.", { kind: chatKindLabel.toLowerCase() })}
          </div>
          <div className="popoverSection">{t("Tokens · this conversation")}</div>
          {totalUsage ? (
            <div className="chatUsageTotal" title={usageDetail(totalUsage)}>
              <UsageLine usage={totalUsage} className="chatMsgUsage inline" />
              {ctxUsed ? <span className="chatMsgUsage inline">{ctxWindow ? <UsageRing fraction={ctxUsed / ctxWindow} /> : null}{ctxText}</span> : null}
              <span className="popoverHint">{t("{n} replies counted, as the provider reported them. Totals per day and model: Settings / AI / Token usage.", { n: chatMessages.filter((m) => m.role === "ai" && m.usage).length })}</span>
            </div>
          ) : (
            <div className="popoverHint">{t("No token counts yet — they appear under each reply once the provider reports them.")}</div>
          )}
        </Popover>
      ) : null },
    },
    findAction,
    historyAction,
    {
      id: "new", icon: discardArmed ? TrashIcon : PlusIcon, label: discardArmed ? t("Discard chat") : t("New chat"), guide: "chat.new", keep: 3,
      glyph: <IconSwap swap={discardArmed} from={<PlusIcon size={16} />} to={<TrashIcon size={16} />} />,
      danger: discardArmed, disabled: busyHere, props: newChatAltProps, onClick: (e) => newChat(e.altKey),
      title: discardArmed ? t("Discard chat — it isn't kept in history")
        : canSave ? t("New chat — keeps this conversation in history and starts a fresh one (Alt+click: discard it)") : t("New chat"),
    },
  ].filter(Boolean);
  // The composer's model chip (AgentChip), "Allowed in this chat" and Stop,
  // for the agent shown: Gamma AI's models, efforts and speeds, or the
  // computer agent's (useComputerSide). The chip words them (the agent
  // first while there are several).
  const knobs = !aiOff && headerModel;
  const chip = onComputer ? side.chip : {
    label: knobsLabel(speed),
    text: knobs ? [headerModel.model, effort].filter(Boolean).join(" · ") : "",
    glyph: knobs ? <SpeedGlyph speed={speed} /> : null, speed: knobs ? speed : "",
    models: aiOff ? [] : headerModels.map((m) => [m.id, modelLabel(m)]), model: headerModel?.id || "", onModel: setChatModel,
    sections: aiOff ? [] : [
      ...(effortLevels.length ? [{
        label: t("Reasoning effort"),
        value: effort,
        onChange: setChatEffort,
        options: [["", t("Default")], ...effortLevels.map((ef) => [ef, ef])],
      }] : []),
      // Only what this model's provider actually offers: a tier it doesn't
      // have would be refused upstream.
      ...(speedTiers.length ? [{
        label: t("Speed"),
        value: speed,
        onChange: setChatSpeed,
        options: [["", t("Default")], ...speedTiers.map((tier) => [tier, tier])],
      }] : []),
    ],
  };
  const grants = onComputer ? side.grants : grantedHere.length && !aiOff ? {
    list: grantedHere.map(permissionLabel).join(", "),
    onRevoke: () => updateGrants((store) => withoutGrants(store, activeUser(), conversationId(chatMessages))),
    title: t("The assistant doesn't ask before these in this chat: {list}. Click to make it ask again.",
      { list: grantedHere.map(permissionLabel).join(", ") }),
  } : null;
  const stop = onComputer ? (side.stopId ? { onClick: () => side.chat.stop(side.stopId), label: t("Stop {agent}", { agent: side.label }) } : null)
    : busyHere ? { onClick: stopChat, label: t("Stop generating") } : null;
  const headerContent = readOnly ? <span className="uiTag">{t("Read only")}</span>
    : actions.length > 0 && !canSave && !onComputer ? (
      <span className="uiTag" title={t("You can view this workspace: this conversation stays in this tab and isn't saved")}>
        {t("Not saved")}
      </span>
    ) : null;

  return (
    <DockWindow title={t("Chat")} guide="chat.grip" onGrip={onGrip} onFold={onFold}
      collapsed={collapsed} onClose={onClose} headerContent={headerContent} actions={actions}>
    <div className="chatPanel chatWindow">
      {onComputer ? <ComputerStrip side={side} bucket={chatKey} page={!!focusedBlockId} /> : <>
        {!readOnly && aiHealth && !aiHealth.ok ? (
          // The login connection check found the active provider broken — say so
          // here, where the failure would otherwise surface mid-conversation,
          // in the error card's words.
          <div className="chatHealthStrip" title={aiHealth.error || ""}>
            <span className="chatHealthText">
              {failureCopy(aiHealth.kind, { provider: aiHealth.provider_name, auth: aiHealth.provider_auth }).headline}
            </span>
            <button className="uiBtn sm" {...settingsIntent} onClick={() => (aiHealth.kind === "blocked" && isAdmin && openSettings
              ? openSettings("proxy") : openAiKeysEditor({ entry: aiHealth.provider_id }))}>{t("Fix…")}</button>
            <button className="uiClose" onClick={dismissAiHealth} title={t("Dismiss")} aria-label={t("Dismiss")}><XIcon size={14} /></button>
          </div>
        ) : null}
        {saveError ? (
          <div className="chatHealthStrip" role="alert" data-testid="chat-save-error" title={saveError}>
            <span className="chatHealthText">{t("This conversation isn't saved: {message}", { message: saveError })}</span>
            <button className="uiBtn sm" onClick={() => session.flush(chatKey).catch(() => {})}>{t("Retry")}</button>
          </div>
        ) : null}
      </>}
      {chatFindOpen ? (
        <div className="chatFindRow">
          <input
            autoFocus
            className="searchInput"
            value={chatFind}
            onChange={(e) => setChatFind(e.target.value)}
            placeholder={t("Find in chat…")}
            onKeyDown={(e) => {
              if (composing(e)) return;
              if (e.key === "Enter") { e.preventDefault(); gotoChatFind(e.shiftKey ? chatFindIdx - 1 : chatFindIdx + 1); }
              else if (e.key === "Escape") { e.preventDefault(); setChatFindOpen(false); setChatFind(""); }
            }}
          />
          <span className="chatFindCount">{chatFind.trim() ? `${chatFindMatches.length ? chatFindIdx + 1 : 0}/${chatFindMatches.length}` : ""}</span>
          <button className="searchToggle searchNavBtn" onClick={() => gotoChatFind(chatFindIdx - 1)} disabled={!chatFindMatches.length} title={t("Previous match")}>
            <ChevronUpIcon size={14} />
          </button>
          <button className="searchToggle searchNavBtn" onClick={() => gotoChatFind(chatFindIdx + 1)} disabled={!chatFindMatches.length} title={t("Next match")}>
            <ChevronDownIcon size={14} />
          </button>
          <button className="uiClose" onClick={() => { setChatFindOpen(false); setChatFind(""); }} title={t("Close find")} aria-label={t("Close find")}><XIcon size={14} /></button>
        </div>
      ) : null}
      <div
        className="chatMessages"
        ref={chatScrollRefCb}
        style={chatTextScale.style}
        onScroll={(e) => {
          const el = e.currentTarget;
          const last = chatLastScrollTopRef.current;
          chatLastScrollTopRef.current = el.scrollTop;
          if (chatProgScrollRef.current) { chatProgScrollRef.current = false; return; }
          if (el.scrollTop < last) {
            chatStickRef.current = false; // any upward move de-sticks
          } else if (el.scrollHeight - el.scrollTop - el.clientHeight < 40) {
            chatStickRef.current = true; // back at the bottom → follow again
          }
        }}
        onWheel={(e) => {
          // Upward intent unsticks immediately — before any scroll event —
          // so an arriving delta can't yank the view back down first.
          if (e.deltaY < 0 && !(e.ctrlKey || e.metaKey)) chatStickRef.current = false; // Ctrl+wheel resizes text, not scroll
        }}
      >
        {chatTextScale.badge}
        {/* What stands in for an empty conversation waits until the chat
            knows its AI and has read the stored conversation, or holds
            what it last showed of it (an empty one switched back to shows
            its line at once while it is read again); a computer agent's
            until its conversation was read. */}
        {onComputer ? (side.messages.length || !side.empty ? null : (
          <div className="chatEmpty">
            {side.empty.text}
            {side.empty.again ? (
              <div className="chatEmptyAgain">
                <button type="button" className="uiBtn sm" disabled={side.chat.busy} onClick={side.chat.reload}>{t("Try again")}</button>
              </div>
            ) : side.empty.offer && openSettings ? (
              <div className="chatEmptyAgain">
                {/* where else it may work: a directory offered in Settings → AI → Agents */}
                <button type="button" className="uiBtn sm ghost" {...settingsIntent} onClick={() => openSettings("agents")}
                  title={t("Offer a directory of this computer for {agent} to work in, in Settings → AI → Agents", { agent: side.label })}>
                  {t("Offer a directory…")}
                </button>
              </div>
            ) : null}
          </div>
        )) : visibleMessages.length || !aiKnown || (loadedFor !== chatKey && !session.recall(chatKey)) ? null
          : setupCard && !loadError ? setupCard : (
            <div className="chatEmpty">
              {loadError || (readOnly ? t("No saved conversation for this page.")
                : focusedBlockId ? t("Ask AI about this page…") : (!noTools && agentIntro) || t("Ask AI anything, or generate a report from your pages…"))}
            </div>
          )}
        <PathLinkContext.Provider value={onComputer ? side.links : outerLinks}>
          <ChatTranscript messages={transcriptMessages}
            scope={onComputer ? `${chatKey}\n${agent.id}\n${side.chat.conversation?.id || ""}` : chatKey}
            replying={onComputer ? side.working : busyHere} still={onComputer ? false : fromLoad}
            findHit={chatFindOpen && chatFind.trim() ? chatFindMatches[chatFindIdx] ?? -1 : -1}
            titleOf={pageTitleOf} onOpenPage={onOpenPage} openActions={openActions} toggleAction={toggleAction}
            editing={onComputer ? null : editingMsg} onEdit={readOnly || onComputer ? undefined : setEditingMsg} onResend={resendFrom}
            copiedIdx={copiedMsgIdx} onCopy={copyChatMessage} canReport={!onComputer}
            approvals={{ answered: answeredApprovals, kindLabel: chatKindLabel, onDecide: decideApproval }}
            onAnswer={(m, ask, decision, note) => side.chat.answer(m.turnId, ask.id, decision, note)}
            errorActions={errorActions}
            changes={{ onReverted: readOnly ? null : changesReverted, onRevertMany: setRevertFrom, many: revertibleCount > 1,
              messages: chatMessages, key: chatKey }}
            handoffs={{ readOnly: readOnly || aiOff, autoOpen: fetchInBackground, onNeverWait: neverWaitHere,
              onContinue: (text) => sendChat(text), draft: composerHasDraft }}
            papers={!readOnly && canSave ? { folder: paperFolder, folderName: libraryTree.folders.get(paperFolder)?.name || "",
              options: paperSave, onOpenPage, onLibraryChange } : null} />
        </PathLinkContext.Provider>
        {/* An earlier conversation stays readable; the card follows it. */}
        {visibleMessages.length ? setupCard : null}
      </div>
      {/* A comment thread's conversation goes on in its thread: its replies
          sent to the agent are its messages. A co-author's is theirs to go
          on: its agent's owner reads it, stops its turn and answers what
          it asks. */}
      {!readOnly && onComputer && side.shown?.thread ? (
        <div className="chatThreadNote" data-testid="chat-thread-note">
          <span className="chatThreadNoteText">{t("This chat answers a comment: reply there to go on.")}</span>
          <button type="button" className="uiBtn sm" onClick={() => gammaNav?.openThread?.(side.shown.thread, "", chatKey)}>
            <MessageSquareIcon size={14} />
            {t("Show the comment")}
          </button>
          <button type="button" className="uiBtn sm ghost" onClick={leaveShown}
            title={t("Show this page's chat again: this conversation stays with its comment thread")}>
            <ArrowLeftIcon size={14} />{t("Back to the chat")}
          </button>
        </div>
      ) : !readOnly && onComputer && side.shown ? (
        <div className="chatThreadNote" data-testid="chat-lent-note">
          <span className="chatThreadNoteText">
            {t("This is {person}'s chat with {agent}: you can read it, stop it and answer what it asks.", {
              person: side.chat.conversation?.person || t("Someone"), agent: side.label })}
          </span>
          {/* the composer and its Stop give way to this line: its turn is stopped here */}
          {stop ? <button type="button" className="uiBtn sm" onClick={stop.onClick} title={stop.label}>{t("Stop")}</button> : null}
          <button type="button" className="uiBtn sm ghost" onClick={leaveShown} title={t("Show your own chat of this page again")}>
            <ArrowLeftIcon size={14} />{t("Back to the chat")}
          </button>
        </div>
      ) : !readOnly ? (
      // One box: the context chips on top, the message at full
      // width, then a toolbar — [+], the PDF switch while a PDF is in
      // context, the model chip (with reasoning effort), the mic when a
      // connection can transcribe, send / stop. While recording the text
      // and the toolbar give way to the waveform row. Its buttons never
      // take the focus, so a press keeps an on-screen keyboard up. With a
      // computer's agent it takes the message and the PDF passages alone
      // (`attaches` false), each passage noting its source line.
      <form
        ref={composerRef}
        className={`chatComposer${closed ? " off" : ""}`}
        data-guide="chat.composer"
        onSubmit={(e) => { e.preventDefault(); sendChatMessage(); }}
        onMouseDown={(e) => { if (e.target.closest("button")) e.preventDefault(); }}
      >
        {pdfSelections.length || (attaches && (chatNotes?.length || cursorChip)) ? (
          <div className="chatSelChips">
            {attaches && cursorChip ? (
              cursorChip.sel ? (
                <SelChip kind="isCursor" icon="selection" auto label={t("Selection in this note")} text={cursorChip.sel.text}
                  cut={keptOf(cursorChip.sel)}
                  title={[t("The text you selected in this note — the assistant changes only this part."), keptHint(cursorChip.sel), cursorChip.sel.text.slice(0, 2000)].filter(Boolean).join("\n\n")}
                  onRemove={() => setCursorOff(cursorChip.id)}
                  removeTitle={t("Don't send the selection with this message")} />
              ) : (
                <SelChip kind="isCursor" icon="cursor" auto label={t("Block at your cursor")}
                  note={t("added automatically, × to leave out")} text={cursorChip.text}
                  title={`${t("Your cursor is on this block — it rides with the message, so \"this block\" means it.")}\n\n${cursorChip.text}`}
                  onRemove={() => setCursorOff(cursorChip.id)}
                  removeTitle={t("Don't send the cursor block with this message")} />
              )
            ) : null}
            {pdfSelections.map((s, i) => (
              <SelChip key={`p${i}`} text={s.text} icon="passage"
                label={s.line ? linesLabel(s.line, s.end)
                  : pdfSelections.length > 1 ? t("PDF passage {i}", { i: i + 1 }) : t("PDF passage")}
                note={sourceNote(side.sources[i]) || s.file || (s.page ? t("p. {page}", { page: s.page }) : "")} file={side.sources[i]?.file || ""}
                cut={attaches ? keptOf(s) : ""}
                title={[s.text.slice(0, 2000), attaches ? keptHint(s) : t("Goes to {agent} with the message", { agent: side.label }),
                  t("Hold Ctrl while selecting to add more passages")].filter(Boolean).join("\n\n")}
                onRemove={() => setPdfSelections((prev) => prev.filter((_, j) => j !== i))}
                removeTitle={t("Remove this passage")} />
            ))}
            {(attaches ? chatNotes || [] : []).map((n, i) => (n.kind === "ink" ? (
              <SelChip key={`n${i}`} kind="isInk" icon="ink" label={n.text} text=""
                title={t("Handwriting selected with the lasso: the assistant gets a picture of these strokes and the id of their note")}
                onRemove={() => setChatNotes?.((prev) => prev.filter((_, j) => j !== i))}
                removeTitle={t("Remove this handwriting")} />
            ) : (
              <SelChip key={`n${i}`} kind={n.kind === "block" ? "isBlock" : "isNote"} text={n.text}
                icon={n.kind === "block" ? "block" : "selection"}
                label={n.kind === "block" ? t("Attached block") : t("Selected note text")}
                cut={n.kind === "note" ? keptOf(n) : ""}
                title={[n.kind === "block" ? t("A note block attached with Ctrl+click or the ⋮⋮ menu — the assistant gets its text and id") : t("Note text selected with Ctrl held — the assistant changes only this part"),
                  n.kind === "note" ? keptHint(n) : "", n.text.slice(0, 2000)].filter(Boolean).join("\n\n")}
                onRemove={() => setChatNotes?.((prev) => prev.filter((_, j) => j !== i))}
                removeTitle={n.kind === "block" ? t("Detach this block") : t("Remove this passage")} />
            )))}
          </div>
        ) : null}
        {attaches && chatDocs.length ? (
          <div className="chatReferenceStrip" aria-label={t("Attached library pages")}>
            <span className="chatReferenceLabel" title={t("Paper details and text stay in context for follow-up questions. Notes are optional in the library picker.")}>{t("Context")}</span>
            {chatDocs.map((id) => <span className="chatReferenceChip" key={id}>
              <button type="button" className="crumbBtn" title={homeBlocks.find((b) => b.id === id)?.content || t("Unavailable page")} onClick={() => onOpenPage?.(id)}><BookIcon size={14} /><span className="linkChipText">{homeBlocks.find((b) => b.id === id)?.content || t("Unavailable page")}</span></button>
              <button type="button" className="uiClose uiCloseSm" aria-label={t("Remove {page} from context", { page: homeBlocks.find((b) => b.id === id)?.content || t("page") })} onClick={() => setChatDocs((prev) => prev.filter((p) => p !== id))}><XIcon size={14} /></button>
            </span>)}
          </div>
        ) : null}
        {attaches && chatFiles.length ? (
          <div className="chatImgPreviewRow">
            {chatFiles.map((f, i) => (
              <span key={i} className="chatFileChip" title={nativePdf ? t("{name} — sent with your next message", { name: f.name }) : t("{name} — {nativePdfNote}", { name: f.name, nativePdfNote: nativePdfNote })}>
                {nativePdf ? <FileIcon size={14} /> : <AlertCircleIcon size={14} />}
                <span className="chatFileChipName">{f.name}</span>
                <button type="button" className="uiClose uiCloseSm chatFileChipRemove" title={t("Remove file")}
                  onClick={() => setChatFiles((prev) => prev.filter((_, j) => j !== i))}><XIcon size={14} /></button>
              </span>
            ))}
          </div>
        ) : null}
        {attaches && chatImages.length ? (
          <div className="chatImgPreviewRow" data-guide="chat.imageContext">
            {chatImages.map((p, i) => (
              <span key={pictureUrl(p) || i} className="chatImgPreview" title={pictureAlt(p)}>
                <img src={pictureSrc(p)} alt={pictureAlt(p)} />
                <button type="button" className="uiClose uiCloseSm uiCloseDanger chatImgRemove" title={t("Remove image")}
                  onClick={() => setChatImages((prev) => prev.filter((_, j) => j !== i))}><XIcon size={14} /></button>
              </span>
            ))}
          </div>
        ) : null}
        {dictation === "rec" ? (
          <div className="chatComposerBar chatRecBar">
            <button className="uiBtn chatCircleBtn chatMicBtn" type="button" onClick={() => finishDictation("cancel")} title={t("Cancel recording")} aria-label={t("Cancel recording")}>
              <XIcon size={14} />
            </button>
            <canvas ref={waveCanvasRef} className="chatWaveCanvas" />
            <span className="chatRecTimer" aria-live="polite">
              <span className="chatRecDot" />
              {Math.floor(recSecs / 60)}:{String(recSecs % 60).padStart(2, "0")}
            </span>
            <button className="uiBtn chatCircleBtn chatMicBtn" type="button" onClick={() => finishDictation("insert")} title={t("Stop — put the transcript in the input")} aria-label={t("Stop and transcribe")}>
              <StopIcon size={14} />
            </button>
            <button className="uiBtn primary chatCircleBtn" type="button" onClick={() => finishDictation("send")} title={t("Stop and send")} aria-label={t("Stop, transcribe and send")}>
              <ArrowUpIcon size={14} />
            </button>
          </div>
        ) : (
        <>
        <PaperMentionInput
          key={chatKey}
          pages={pickerPages} tree={libraryTree} recentViews={recentViews} openTabs={openTabs}
          currentPageId={focusedBlockId} selected={chatDocs}
          onAttach={(id) => setChatDocs((prev) => prev.includes(id) ? prev : [...prev, id])}
          onSend={sendChatMessage}
          className="chatInput chatInputArea"
          data-guide="chat.input"
          rows={1}
          value={chatInput}
          onChange={setChatInput}
          onPaste={attaches ? handleChatPaste : undefined}
          mentions={attaches} label={onComputer ? t("Message {agent}", { agent: side.label }) : ""}
          disabled={closed}
          // Until the chat knows its AI the field names nothing, so it
          // never offers to ask before it says a service is needed.
          placeholder={onComputer ? (side.where ? t("Message {agent} on {computer}…", { agent: side.label, computer: side.where })
            : t("Message {agent} on this computer…", { agent: side.label }))
            : !aiKnown ? "" : aiOff ? t("Connect an AI service to start chatting") : (
            // Names what the message will be about, most specific attachment first.
            chatFiles.length ? tn("Ask about the attached file…", "Ask about the attached files…", chatFiles.length)
            : chatImages.length ? t("Ask about the attached picture…") : pdfSelections.length > 1 ? t("Ask about the {n} selected passages…", { n: pdfSelections.length })
            : pdfSelections.length ? t("Ask about the selection…") : chatNotes?.length > 1 ? t("Ask about the {n} attached notes…", { n: chatNotes.length })
            : chatNotes?.length ? (chatNotes[0].kind === "block" ? t("Ask about the attached block…")
              : chatNotes[0].kind === "ink" ? t("Ask about the selected handwriting…") : t("Ask about the selected note…"))
            : cursorChip?.sel ? t("Ask about the selection…") : cursorChip ? t("Ask about this block…") : chatDocs.length ? tn("Ask about {n} attached page…", "Ask about {n} attached pages…", chatDocs.length)
            : (!noTools && agentAsk) || t("Ask…")
          ) + t(" (@ paper)")}
        />
        <div className="chatComposerBar">
          {attaches ? <span data-popover="chatdocs" className="popoverAnchor">
            <button
              type="button"
              className={`chatAttachToggle chatPlusBtn ${(chatDocs.length || chatIncludeNotes) ? "on" : ""}`}
              data-guide="chat.context"
              disabled={aiOff}
              onClick={() => setOpenPopover((p) => (p === "chatdocs" ? null : "chatdocs"))}
              title={t("Add photos & files, or pages from your library")}
              aria-label={t("Add attachments or chat context")}
            >
              <PlusIcon size={16} />{chatDocs.length ? <span className="chatPlusCount">{chatDocs.length}</span> : null}
            </button>
            {openPopover === "chatdocs" ? (
              <Popover className="popUp chatPlusMenu">
                <button type="button" className="chatPlusMenuItem"
                  onClick={() => {
                    setOpenPopover(null);
                    pickFiles({ accept: modelInfo?.images === false ? "application/pdf" : "image/*,application/pdf", multiple: true },
                      (files) => addChatFiles([...files]));
                  }}>
                  <span className="chatPlusMenuIcon">
                    <PaperclipIcon size={16} />
                  </span>
                  <span className="chatPlusMenuLabel">{t("Add photos & files")}</span>
                  <span className="chatPlusMenuHint">{t("Images or PDFs from your computer")}</span>
                </button>
                {onAttachView ? (
                  <button type="button" className="chatPlusMenuItem"
                    onClick={() => { setOpenPopover(null); onAttachView(); }}>
                    <span className="chatPlusMenuIcon">
                      <EyeIcon size={16} />
                    </span>
                    <span className="chatPlusMenuLabel">{t("Attach what I see")}</span>
                    <span className="chatPlusMenuHint">{t("The visible part of the page, with your handwriting")}</span>
                  </button>
                ) : null}
                <button type="button" className="chatPlusMenuItem"
                  onClick={() => { setOpenPopover(null); setDocPickerQuery(""); setDocPicker(true); }}>
                  <span className="chatPlusMenuIcon">
                    <BookIcon size={16} />
                  </span>
                  <span className="chatPlusMenuLabel">{t("Add pages from library")}</span>
                  <span className="chatPlusMenuHint">{chatDocs.length ? t("{n} selected", { n: chatDocs.length }) : t("Search your pages")}</span>
                </button>
              </Popover>
            ) : null}
          </span> : null}
          {attaches && pdfInContext && !aiOff ? <button
            type="button"
            className={`chatAttachToggle chatPdfToggle ${attachPdf ? "on" : ""}`}
            data-guide="chat.pdfSwitch"
            aria-pressed={attachPdf}
            onClick={() => { attachPdfManualRef.current = true; setAttachPdf((v) => !v); }}
            title={!nativePdf
              ? `${attachPdf ? t("On, but: ") : ""}${nativePdfNote}`
              : attachPdf
                ? t("Full PDF file is sent with each message (model sees figures & tables). Click to switch to extracted text only.") : t("Send the full PDF file with your messages so the model sees figures & tables (uses more tokens). Click to enable.")}
          >
            <FileIcon size={14} />
            {t("PDF")}
          </button> : null}
          {attaches && attachPdf && pdfInContext && !nativePdf && !aiOff ? (
            <button type="button" className="chatAttachToggle chatPdfToggle chatPdfWarn"
              onClick={() => setStatus(nativePdfNote)} title={nativePdfNote}
              aria-label={t("This provider cannot accept PDF files")}>
              <AlertCircleIcon size={14} />
            </button>
          ) : null}
          <span className="chatComposerSpacer" />
          {grants ? <GrantChip {...grants} /> : null}
          {attaches && noTools && !aiOff && toolsEnabled && agentPayload(chatMessages).agent_scope ? (
            <button type="button" className="chatAttachToggle chatGrantChip" disabled={checkingTools}
              onClick={checkTools} title={noToolsHint}
              aria-label={canCheckTools ? t("No tools on {model}. Check again", { model: headerModel.model })
                : t("No tools on {model}", { model: headerModel.model })}>
              <BanIcon size={14} /><span className="chatGrantLabel">{checkingTools ? t("Checking…") : t("No tools")}</span>
            </button>
          ) : null}
          {chip.models.length || agentList.length > 1 ? (
            <AgentChip guide="chat.model" agents={agentList} agent={agent} onAgent={chooseAgent} {...chip} />
          ) : null}
          {stop ? (
            <button className="uiBtn chatCircleBtn chatStopBtn" type="button" onClick={stop.onClick} title={stop.label} aria-label={stop.label}>
              <StopIcon size={14} />
            </button>
          ) : dictation === "busy" ? (
            <button className="uiBtn chatCircleBtn chatMicBtn" type="button" disabled title={t("Transcribing…")} aria-label={t("Transcribing")}>
              <span className="transferSpin inline" />
            </button>
          ) : (
            <>
              {canDictate ? <button className="uiBtn chatCircleBtn chatMicBtn" type="button" onClick={startDictation} title={t("Dictate — transcribed with your OpenAI key")} aria-label={t("Start dictation")}>
                <MicIcon size={14} />
              </button> : null}
              <button className="uiBtn primary chatCircleBtn" type="submit" title={t("Send")} aria-label={t("Send")}
                disabled={onComputer ? !side.ready(chatInput) : aiOff || !chatInput.trim()}>
                <ArrowUpIcon size={14} />
              </button>
            </>
          )}
        </div>
        </>
        )}
      </form>
      ) : null}
      {revertFrom ? (
        <RevertChanges messages={chatMessages} fromId={revertFrom} busy={busyHere}
          onReverted={(id, indexes, answer) => changesReverted(id, indexes, answer)} onClose={() => setRevertFrom(null)} />
      ) : null}
      {docPicker ? (
        <DialogOverlay {...backdropPress(() => setDocPicker(false))}>
          <div className="reportModal docPickerModal" onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              // ↑/↓ walk the search box and the pages' checkboxes; Enter on a
              // page ticks it like Space does.
              const list = [...e.currentTarget.querySelectorAll(".docPickerList input[type=checkbox]")];
              if (stepList(e, e.currentTarget.querySelector("[data-find]"), list)) return;
              if (e.key === "Enter" && list.includes(e.target)) { e.preventDefault(); e.target.click(); }
            }}>
            <div className="reportModalTitle">{t("Add pages to the chat")}</div>
            <div className="reportModalHint">
              {t("Selected pages (their PDF text, and optionally your notes) are sent with every question — pick a few and just ask for a report.")}
            </div>
            <input
              autoFocus={!touchTyping()} data-find
              className="searchInput"
              placeholder={t("Search your pages…")}
              value={docPickerQuery}
              onChange={(e) => setDocPickerQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") { e.preventDefault(); setDocPicker(false); }
                // Enter ticks the best match (with a query) and selects the
                // query, so the next name typed replaces it; on an empty box
                // it is Done.
                if (e.key === "Enter" && !composing(e)) {
                  e.preventDefault();
                  if (!docPickerQuery.trim()) { setDocPicker(false); return; }
                  const first = e.currentTarget.closest(".docPickerModal").querySelector(".docPickerList input[type=checkbox]");
                  if (first && !first.disabled) { first.click(); e.currentTarget.select(); }
                }
              }}
            />
            <div className="reportPageList docPickerList">
              {(() => {
                // Every page can be context — a page of notes as much as a
                // paper; the server adds PDF text for pages that carry one.
                if (!pickerPages.length) return <div className="popoverHint">{t("No pages yet — create one first.")}</div>;
                // Quick open's ranking, as the @ picker's: the query's matches,
                // or recents, open tabs and the rest under their headings.
                const rows = rankLibraryPages(pickerPages, libraryTree, docPickerQuery, { recentViews, openTabs, limit: Infinity });
                if (!rows.length) return <div className="popoverHint">{t("No pages match “{docPickerQuery}”.", { docPickerQuery: docPickerQuery.trim() })}</div>;
                const sectionTitle = recencySections();
                // A sub-page's row names the pages it is in after its title.
                return rows.map(({ page: b, section, path }, i) => (
                  <React.Fragment key={b.id}>
                    {section !== rows[i - 1]?.section && sectionTitle[section]
                      ? <div className="popoverSection">{sectionTitle[section]}</div> : null}
                    <label className="docPickerItem" title={b.content || t("Untitled")}>
                      <input
                        type="checkbox"
                        checked={chatDocs.includes(b.id)}
                        disabled={!chatDocs.includes(b.id) && chatDocs.length >= MAX_CHAT_REFERENCES}
                        onChange={(e) => setChatDocs((prev) => e.target.checked
                          ? [...prev, b.id]
                          : prev.filter((id) => id !== b.id))}
                      />
                      <span className="attachName">{b.content || t("Untitled")}</span>
                      {path ? <span className="docPickerPath">{path}</span> : null}
                      {b.id === focusedBlockId ? <span className="docPickerBadge">{t("current")}</span> : null}
                    </label>
                  </React.Fragment>
                ));
              })()}
            </div>
            <label className="docPickerItem docPickerNotes">
              <input type="checkbox" checked={chatIncludeNotes} onChange={(e) => setChatIncludeNotes(e.target.checked)} />
              <span className="attachName">{t("Include my notes & highlights")}</span>
            </label>
            {!chatDocs.length && pdfFile ? (
              <div className="popoverHint">{t("Nothing selected — the open page is used.")}</div>
            ) : null}
            <div className="reportModalBtns">
              {chatDocs.length ? (
                <button className="uiBtn" onClick={() => setChatDocs([])}>{t("Clear selection")}</button>
              ) : null}
              <button className="uiBtn primary" onClick={() => setDocPicker(false)}>{t("Done")}</button>
            </div>
          </div>
        </DialogOverlay>
      ) : null}
    </div>
    </DockWindow>
  );
}
