// The AI chat window, self-contained: per-page conversation state (loaded/
// saved on the backend), ChatGPT-style message actions (copy/edit/find/stop),
// pasted figures, the "+" context picker, and the per-message PDF attach.
// App provides context (open paper, library, selections) and the model/effort/
// prompt preferences it also needs elsewhere.
import React, { useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { API, apiJson, copyText, isPdfFile, readNdjson } from "../shared/lib/utils";
import { stepList } from "../shared/ui/listKeys.js";
import { DockWindow, ChatCiteContext, ChatMarkdown, AutoGrowTextarea, GammaNavContext, useCopied, useTextScale } from "../shared/ui/Widgets";
import PaperMentionInput from "./PaperMentionInput";
import { MAX_CHAT_REFERENCES } from "./paperMentions";
import { READ_TOOLS, WRITE_TOOLS, toolsForKind } from "./chatSettings";
import { addUsage, cachedPercent, contextUsed, conversationUsage, fmtTokens, liveUsage, usageDetail } from "./tokenUsage";
import { createTitleScorer } from "../library/librarySearch";
import { pageAttachment } from "../library/libraryUtils";
import { ActionMenu, MenuSelect } from "../shared/ui/Menus";
import { chatFailure, failureCopy, failureFields, fixLabel } from "./chatErrors";
import { changePlace, isChange, noteChangeText, runningLabel, splitActions, stepsSummary } from "./agentSteps";
import { guideEvents } from "../guide/events.js";
import { gammaLinksIn } from "../shared/model/gammaLinks.js";
import { CharSlider, approxPages } from "../settings/SettingsKit";
import { AgentToolPicker, CHAT_KIND_ROWS } from "../settings/SettingsDialog";
import { aiServiceTiles } from "../settings/SettingsAi";
import { renderKatex } from "../editor/LatexEditor";
import { chipSegments } from "./chipText";
import { effortFor } from "./effort";
import { AlertCircleIcon, ArrowDownIcon, ArrowUpIcon, BookIcon, CheckIcon, ChevronDownIcon, ChevronUpIcon, CloudDownloadIcon, CopyIcon, EyeIcon, FileIcon, FolderIcon, GlobeIcon, HighlightIcon, HistoryIcon, InfoIcon, ListIcon, MicIcon, OutlineIcon, PaperclipIcon, PencilIcon, PlusIcon, QuoteIcon, SearchIcon, SettingsIcon, SlidersIcon, SparklesIcon, StopIcon, TextCursorIcon, TrashIcon, XIcon } from "../shared/ui/Icons";
import { T, getLocale, t } from "../shared/i18n/i18n.js";

const JSON_HEADERS = { "Content-Type": "application/json" };

// A conversation's display name when the user never named it: the first
// user message's first non-quote line (mirrors derive_title in
// gamma/routers/chats.py, which names entries when they are archived).
function deriveTitle(messages) {
  const first = (messages || []).find((m) => m.role === "user");
  const line = (first?.text || "").split("\n").map((l) => l.trim()).find((l) => l && !l.startsWith(">"));
  return (line || "").replace(/\s+/g, " ").slice(0, 80);
}

// Compact age for a history row: "now", "5m", "3h", "2d", "4mo", "1y".
function relAge(iso) {
  const ms = Date.now() - Date.parse(iso || "");
  if (!Number.isFinite(ms) || ms < 60000) return "now";
  const m = Math.floor(ms / 60000);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}d`;
  return d < 365 ? `${Math.floor(d / 30)}mo` : `${Math.floor(d / 365)}y`;
}

// Folder-agent tool chips: icon per action kind; rename/move are the kinds
// that changed the library (they trigger the home-feed refresh). Every chip
// carries the raw call the server ran (tool/args/result, both truncated), so
// clicking one expands the arguments and the output the model saw.
const ACTION_ICONS = { rename: PencilIcon, move: FolderIcon, search: SearchIcon, read: BookIcon, view: EyeIcon, list: ListIcon, edit: PencilIcon, create: PlusIcon, websearch: GlobeIcon, fetch: CloudDownloadIcon, error: XIcon };
// What the model was given for a reply, per document — streamed by
// /api/ai/chat as its first line and saved on the message. Shown only when
// it matters: the paper was truncated, or the PDF file was requested but the
// provider refused it (text went instead). A full native attachment or a
// paper that fit whole stays silent. A selection says where the server
// placed it ("p. 7 · Methods › Noise model") and whether a picture of it
// went along (its text layer looked like a formula or table).
function selectionPlace(selection) {
  const placed = (selection?.passages || []).filter((p) => p.page);
  if (!placed.length) return "";
  const pages = [...new Set(placed.map((p) => p.page))];
  const section = placed.find((p) => p.section)?.section || "";
  const short = section.length > 40 ? `${section.slice(0, 40)}…` : section;
  return `${pages.length > 1 ? "pp." : "p."} ${pages.join(", ")}${short ? ` · ${short}` : ""}`;
}

function ContextCoverage({ items }) {
  const [open, setOpen] = useState(-1); // the pill whose explanation is unfolded
  const notes = items.flatMap((c) => {
    const out = [];
    const refused = c.native_requested && !c.native;
    const place = selectionPlace(c.selection);
    const what = c.title ? `“${c.title.slice(0, 48)}${c.title.length > 48 ? "…" : ""}”` : t("the PDF");
    if (refused || c.partial) {
      const around = c.selection && !c.pages_shown;
      const span = c.pages_shown && c.pages
        ? t("pages 1–{pages} of {pages2}", { pages: Math.min(c.pages_shown, c.pages), pages2: c.pages })
        : around ? (place ? t("text around {place}", { place }) : t("selected passages + head")) : `${(c.chars || 0).toLocaleString()} characters`;
      const short = refused && !c.partial
        ? t("PDF file not accepted — sent as text")
        : refused
          ? t("PDF file not accepted — text only, {span}", { span })
          : t("Model saw {span}", { span });
      const long = (refused ? t("This provider does not accept PDF files, so the document went as extracted text. ") : "")
        + (!c.partial ? `${what} was sent as extracted text.`
          : around ? `The model got the text around your selection${place ? ` (${place})` : ""} and the start of ${what}, not the whole document. Turn on Tools so it can read and search the rest.`
          : `Only ${span} of ${what} fit the context budget — the rest was not visible to the model. Raise the budget in Settings / AI / Advanced AI settings / Context size, or turn on Tools so it can read and search the whole paper.`);
      out.push({ short, long, refused });
    }
    if ((c.selection?.passages || []).some((p) => p.crop)) {
      out.push({
        short: T("Picture of the selection sent"),
        long: t("The selected text looked like a formula or table (or wasn't in the extracted text), so the model also got a picture of that region of the page."),
        refused: false,
      });
    }
    return out;
  });
  if (!notes.length) return null;
  // Pills like the agent's steps: a click unfolds the explanation under them.
  return (
    <div className="chatCoverage">
      {notes.map((n, i) => (
        <button key={i} type="button" className="chatPill" aria-expanded={open === i}
          title={open === i ? undefined : n.long} onClick={() => setOpen(open === i ? -1 : i)}>
          {n.refused ? <AlertCircleIcon size={14} /> : <InfoIcon size={14} />}
          <span className="chatPillText">{n.short}</span>
          {open === i ? <ChevronUpIcon size={14} /> : <ChevronDownIcon size={14} />}
        </button>
      ))}
      {notes[open] ? <p className="chatCoverageNote">{notes[open].long}</p> : null}
    </div>
  );
}

// A failed request as a card (message.errorKind, chat/chatErrors.js): a
// headline saying what went wrong, one plain sentence, the buttons that fix
// it, and the provider's own words folded under "Details". A reply that
// broke off partway keeps its text and gets the compact card (no sentence)
// under it.
function ChatErrorCard({ message, compact, actions }) {
  const copy = failureCopy(message.errorKind, { provider: message.errorProvider, auth: message.errorAuth });
  const fromProvider = !["network", "not_configured"].includes(message.errorKind);
  return (
    <div className={`chatErrorCard${compact ? " compact" : ""}`}>
      <div className="chatErrorHead"><AlertCircleIcon size={16} /><span>{copy.headline}</span></div>
      {!compact ? <div className="chatErrorText">{copy.text}</div> : null}
      {actions.length ? <div className="chatErrorActions">{actions}</div> : null}
      {message.errorDetail ? (
        <details className="chatErrorDetails">
          <summary>{fromProvider ? t("Details from the provider") : t("Details")}</summary>
          <pre>{message.errorDetail}</pre>
        </details>
      ) : null}
    </div>
  );
}

// The agent's work in a reply (chat/agentSteps.js): one pill summing up its
// steps, which expands to every call's chip (arguments and output), and
// under it the changes — renamed or filed pages, edited or added notes —
// each old → new with a link to what changed. While the reply streams, the
// pill names the step running now.
function AgentSteps({ actions, running, open, onToggle, titleOf, children }) {
  const { failed } = splitActions(actions);
  const live = !!running;
  return (
    <div className="chatStepsWrap">
      <button type="button" className={`chatPill chatSteps${live ? " live" : ""}`} onClick={onToggle} aria-expanded={open}
        title={open ? t("Hide the steps") : t("Show every step with its arguments and output")}>
        {live ? <span className="transferSpin inline" aria-hidden="true" /> : <CheckIcon size={14} />}
        <span className="chatPillText">{live ? runningLabel(running, titleOf) : stepsSummary(actions)}</span>
        {failed && !live ? <span className="chatStepsFailed">{t("{n} failed", { n: failed })}</span> : null}
        {actions.length ? (open ? <ChevronUpIcon size={14} /> : <ChevronDownIcon size={14} />) : null}
      </button>
      {open ? children : null}
    </div>
  );
}

function AgentChanges({ actions, onOpenPage }) {
  const nav = useContext(GammaNavContext);
  const { library, notes } = splitActions(actions);
  if (!library.length && !notes.length) return null;
  const pageLink = (id, label) => id
    ? <button type="button" className="chatChangeLink" onClick={() => onOpenPage?.(id)}>{label}</button>
    : <span>{label}</span>;
  const blockLink = (a, label) => a.block_id && nav?.openBlock
    ? <button type="button" className="chatChangeLink" onClick={() => nav.openBlock(a.block_id, a.page_id)}>{label}</button>
    : pageLink(a.page_id, label);
  const libraryRow = (a) => {
    if (a.kind === "rename" && a.to) {
      return <><s className="chatChangeOld">{a.from}</s> → {pageLink(a.page_id, a.to)}</>;
    }
    if (a.kind === "move" && a.title) {
      return <span title={a.from ? t("Was in: {folders}", { folders: a.from }) : undefined}>
        {t("{page} moved to {folder}", { page: pageLink(a.page_id, a.title), folder: <strong>{a.to || t("the library root")}</strong> })}
      </span>;
    }
    return pageLink(a.page_id, a.summary); // saved before the structured fields
  };
  const noteRow = (a) => (a.title ? noteChangeText(a, blockLink(a, `“${a.title}”`)) : blockLink(a, a.summary));
  const group = (rows, place) => rows.length ? (
    <div className="chatChanges" key={place}>
      <div className="chatChangesHead">
        {place === "library" ? t("Changed in your library · {n}", { n: rows.length }) : t("Changed in your notes · {n}", { n: rows.length })}
      </div>
      {rows.map((a, j) => {
        const Icon = ACTION_ICONS[a.kind] || PencilIcon;
        return (
          <div key={j} className="chatChange">
            <Icon size={14} />
            <span className="chatChangeText">{changePlace(a) === "library" ? libraryRow(a) : noteRow(a)}</span>
          </div>
        );
      })}
    </div>
  ) : null;
  return <>{group(library, "library")}{group(notes, "notes")}</>;
}

// The token line under a reply, Claude Code style: prompt in, reply out,
// and the share of the prompt the provider served from its cache. Every
// count comes from the provider's own report ({"usage"} lines of the chat
// stream, summed over an agent reply's rounds); a reply without one shows
// nothing. While the reply streams the same line ticks up next to the
// "Responding" pill: exact counts for the rounds already reported, a "~"
// estimate from the characters received for the one still arriving.
function UsageLine({ usage, className = "chatMsgUsage" }) {
  if (!usage || !(usage.input || usage.output)) return null;
  const cached = cachedPercent(usage);
  const live = !!usage.estimate;
  return (
    <span className={className} title={live ? t("Counting while the reply streams — the provider's own count replaces the estimate when it finishes") : usageDetail(usage)}>
      {usage.input ? <span className="chatMsgUsagePart"><ArrowUpIcon size={9} />{fmtTokens(usage.input)}</span> : null}
      <span className="chatMsgUsagePart"><ArrowDownIcon size={9} />{live ? "~" : ""}{fmtTokens(usage.output)}</span>
      {cached && !live ? <span className="chatMsgUsagePart">{cached}{t("% cached")}</span> : null}
    </span>
  );
}

// What the server knows about a chat model, asked once per model per page
// load (GET /api/ai/model-info): its context window in tokens and the
// reasoning-effort levels it takes, read from the provider's own model
// listing, else the public models.dev catalog. Null while unknown, and so
// is either field when no source knows it — nothing is guessed from the name.
const modelInfos = new Map(); // model id -> Promise<{context_window, efforts} | null>
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

// Claude Code's context ring: the share of the model's window the
// conversation fills (tokenUsage.contextUsed against useContextWindow),
// drawn in the icon colour and turning red past 80%.
function ContextRing({ fraction }) {
  const f = Math.max(0, Math.min(1, fraction));
  const c = 2 * Math.PI * 6;
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" className={`chatContextRing${f >= 0.8 ? " full" : ""}`} aria-hidden="true">
      <circle cx="8" cy="8" r="6" className="chatContextRingTrack" />
      <circle cx="8" cy="8" r="6" className="chatContextRingFill" strokeDasharray={`${f * c} ${c}`} transform="rotate(-90 8 8)" />
    </svg>
  );
}

// The note-block mutators: their actions carry the page id(s) they touched,
// so the open page's block tree can reload and show the change.
const BLOCK_TOOLS = new Set(["edit_block", "create_block", "move_block"]);
const toolCallText = (a) => {
  const args = Object.entries(a.args || {}).map(([k, v]) => `${k}: ${v}`).join(", ");
  const head = `${a.tool || a.kind}(${args})`;
  return [head, a.result].filter(Boolean).join("\n\n");
};

// One chip in the composer's strip: a PDF passage, the cursor block, an
// attached block or selected note text. Two lines: what it is, in words
// (`label`, plus a muted `note` — the PDF page, or how to leave an automatic
// one out), over a plain-text preview of the text (chat/chipText.js: the
// markdown dropped, inline math typeset). `auto` marks context nobody
// attached — it rides along because of where the cursor is — with a dashed
// border; `kind` is the modifier class (isCursor / isBlock / isNote; none
// for a PDF passage).
const SEL_CHIP_ICONS = { cursor: TextCursorIcon, selection: HighlightIcon, passage: QuoteIcon, block: OutlineIcon };

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

function SelChip({ kind, icon, label, note, auto = false, text, title, onRemove, removeTitle }) {
  const Glyph = SEL_CHIP_ICONS[icon];
  return (
    <div className={`chatSelChip${kind ? ` ${kind}` : ""}${auto ? " auto" : ""}`} title={title ?? text}>
      <div className="chatSelChipHead">
        <span className="chatSelChipLabel"><Glyph size={14} aria-hidden="true" />{label}</span>
        {note ? <span className="chatSelChipNote">· {note}</span> : null}
        <button type="button" className="uiClose uiCloseSm chatSelChipClose" onClick={onRemove} title={removeTitle} aria-label={removeTitle}><XIcon size={14} /></button>
      </div>
      <ChipPreview text={text} />
    </div>
  );
}

// No AI connected: what the chat is for, then one tile per way to connect
// (settings/SettingsAi.jsx aiServiceTiles over GET /api/ai/settings). A tile
// opens Settings → Connections with the connect dialog set to that service.
// An account that can't store keys (a guest) can only ask for a shared one;
// an admin may also share one with the whole server.
function ChatSetupCard({ info, isAdmin, onConnect, openSettings }) {
  const tiles = info && !info.failed ? aiServiceTiles(info, { long: true }) : [];
  return (
    <div className="chatSetup" data-guide="chat.setup">
      <span className="chatSetupIcon" aria-hidden="true"><SparklesIcon size={16} /></span>
      <div className="chatSetupTitle">{t("Chat with your papers")}</div>
      <p className="chatSetupText">
        {t("Ask about the paper you are reading and get answers that cite the page they come from. The assistant can also search, summarize and organize your library.")}
      </p>
      {info?.failed ? (
        <button type="button" className="uiBtn primary chatSetupRetry" onClick={() => onConnect()}>{t("Set up AI…")}</button>
      ) : info && !info.can_edit ? (
        <p className="chatSetupText">{t("No AI service is shared on this server — ask your administrator.")}</p>
      ) : info ? (
        <>
          <div className="chatSetupTiles" role="group" aria-label={t("Connect an AI service")}>
            {tiles.map((tile) => (
              <button key={tile.value} type="button" className="chatSetupTile" onClick={() => onConnect(tile.value)}>
                <span className="chatSetupTileName">{tile.label}</span>
                <span className="chatSetupTileHint">{tile.hint}</span>
              </button>
            ))}
          </div>
          <p className="chatSetupNote">{t("Keys are stored on this server and never shown again. Pick one to connect — it takes about a minute.")}</p>
        </>
      ) : null}
      {isAdmin && openSettings ? (
        <p className="chatSetupNote">
          {t("Or share one with everyone on this server: {link}", {
            link: <button type="button" className="chatEmptyLink" onClick={() => openSettings("server")}>{t("Settings › Server › Shared AI provider")}</button>,
          })}
        </p>
      ) : null}
    </div>
  );
}

export default function ChatDock({
  session,
  readOnly = false,
  docId, pageAttach, focusedBlockId, homeBlocks, pageTitle, openTabs,
  pdfSelections, setPdfSelections,
  // Note chips ([{kind: "block", id, text} | {kind: "note", id, from, to,
  // text}], App state like pdfSelections) and the block row the user's
  // cursor is on ({id, text, sel?: {from, to, text}} — the text selected in
  // its editor) — both ride with the next message so "this block" and "the
  // selection" mean something. onSelectionSent drops the sent selection.
  chatNotes, setChatNotes, focusedNote, onSelectionSent,
  chatImages, setChatImages,
  chatModel, setChatModel, chatEffort, setChatEffort, chatSystem,
  dictationModel, dictationLang,
  chatContextChars, setChatContextChars, multiContextChars,
  // openAiKeysEditor({service, entry}?) opens Settings → Connections: with
  // `service` the connect dialog set to it (the setup card), with `entry`
  // that connection's form (an error card's "Update key", the login check's
  // Fix…). focusSignal: bumped by App when a connection made from the setup
  // card is ready — the message box takes the focus once the chat is enabled.
  aiInfo, aiProvider, openAiKeysEditor, openSettings, isAdmin = false, focusSignal = 0,
  aiHealth, dismissAiHealth,
  openPopover, setOpenPopover,
  setStatus, askConfirm,
  // Home/folder view: the folder path being viewed ("" = library root) —
  // enables the folder-agent tools; null in the paper view. agentPerms is the
  // Settings per-chat-kind permission map ({folder, pdf, notes} → {list,
  // read, block_read, search, rename, move, block_edit}; setAgentPerms edits
  // it from the ⚙ popover) and toolRounds the round budget. When the AI applied changes, onLibraryChange
  // refreshes the home feed and onNotesChange reloads touched pages' notes.
  organizeFolder = null, toolRounds, agentReadChars, agentPerms, setAgentPerms, agentSystem,
  agentEnabled, setAgentEnabled, onLibraryChange, onNotesChange, onAgentEvent,
  // Opens a page the reply links to (/?page=<id>) in place.
  onOpenPage,
  onGrip, onGripDoubleClick, collapsed, onClose,
}) {
  const [loadedMessages, setChatMessages] = useState([]);
  const [chatInput, setChatInput] = useState("");
  const [loadError, setLoadError] = useState("");
  // Chat history is per page; the home view buckets per folder ("home" at the
  // library root, "home:<path>" inside a folder) — switching folders switches
  // conversations, so the organizer never drags one folder's context into
  // another. App migrates the buckets on folder rename/move/delete
  // (POST /api/folders/rename).
  const chatKey = focusedBlockId || (organizeFolder ? `home:${organizeFolder}` : "home");
  const sessionState = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const chatMessages = sessionState.replies.get(chatKey)?.messages || loadedMessages;
  // A reply is streaming into THIS conversation. Other buckets stream on
  // their own — asking one paper never waits for another's answer.
  const busyHere = session.isActive(chatKey);
  // No AI connected (known once /api/ai/models answered): the setup card
  // takes the empty state, the composer is disabled and the header's tools
  // go — a send could only fail. The card's tiles come from the settings
  // payload, fetched only then.
  const aiOff = !readOnly && !!aiInfo && !aiInfo.enabled;
  const [setupInfo, setSetupInfo] = useState(null);
  useEffect(() => {
    if (!aiOff) return undefined;
    let live = true;
    apiJson(`${API}/ai/settings`)
      .then((info) => { if (live) setSetupInfo(info); })
      .catch(() => { if (live) setSetupInfo({ failed: true }); });
    return () => { live = false; };
  }, [aiOff]);
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
  // map in Settings → AI → Chat (app/prefDefs.js CHAT_KINDS): the folder chat, a
  // page with a PDF, a page of notes.
  const chatKind = folderChat ? "folder" : pageAttach ? "pdf" : "notes";
  const chatKindLabel = CHAT_KIND_ROWS.find((r) => r[0] === chatKind)?.[2] || t("Chat");
  // The chat settings shortcut edits the same global preferences as Settings.
  const chatToolPerms = agentPerms?.[chatKind] || {};
  const toolsEnabled = !!agentEnabled;
  const perm = (key) => chatToolPerms?.[key] !== false;
  const toggleTools = () => setAgentEnabled(!agentEnabled);
  // What the agent may do here after applying the shared permissions.
  const agentReads = READ_TOOLS.some(perm);
  const agentWrites = WRITE_TOOLS.some(perm);
  // Agent fields riding on /api/ai/chat ({} = plain chat): folder chats reach
  // the folder's pages, page chats get the read + note-block tools for their
  // own page.
  const agentPayload = () => {
    if (!toolsEnabled) return {};
    const scope = organizeFolder != null && (agentReads || agentWrites)
      ? { agent_scope: "folder", folder: organizeFolder }
      : focusedBlockId && toolsForKind("pdf").some(perm)
        ? { agent_scope: "page", page_id: focusedBlockId }
        : null;
    return scope
      ? { ...scope, tool_rounds: toolRounds || 0, read_char_limit: agentReadChars || 0,
          permissions: chatToolPerms, agent_system: agentSystem || "" }
      : {};
  };
  // The block the user's cursor is on shows as a "Cursor" chip (like a PDF
  // selection) and rides with the message as focus_block_id; its × leaves it
  // out for that block until the cursor moves.
  const [cursorOff, setCursorOff] = useState(null);
  useEffect(() => { setCursorOff(null); }, [focusedNote?.id]);
  const cursorChip = focusedNote && focusedNote.id !== cursorOff ? focusedNote : null;
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
  // On by default for a conversation that hasn't seen this document yet;
  // derived from the loaded history below, so a refresh can't re-enable it
  // after the PDF was already sent (re-sending re-bills the whole file).
  const [attachPdf, setAttachPdf] = useState(false);
  // Whether the active model's provider takes the PDF file itself. The
  // ChatGPT sign-in wire (Codex backend) refuses file parts — the server
  // falls back to extracted text — so there the PDF button must not
  // default on, and turning it on by hand gets a warning, not silence.
  const activeModel = (aiInfo?.models || []).find((m) => m.id === chatModel) || null;
  // The header's model list is scoped to the active key (Settings → AI ›
  // Connections); all models only when no key is selected or the selected one is gone.
  const headerModels = aiInfo?.models?.length
    ? (aiProvider && aiInfo.models.some((m) => m.provider === aiProvider)
      ? aiInfo.models.filter((m) => m.provider === aiProvider) : aiInfo.models)
    : [];
  const headerModel = headerModels.find((m) => m.id === chatModel) || headerModels[0] || null;
  // A model's name in the pickers, with its connection's when there are several.
  const multiProvider = new Set(headerModels.map((m) => m.provider)).size > 1;
  const modelLabel = (m) => (multiProvider ? `${m.model} · ${m.provider_name || m.provider}` : m.model);
  const modelInfo = useModelInfo(!aiOff && aiInfo ? headerModel?.id : "");
  // The context ring: the latest reply's size against the model's window.
  const ctxUsed = contextUsed(chatMessages);
  const ctxWindow = ctxUsed ? modelInfo?.context_window || null : null;
  // The reasoning efforts the picked model takes ([] = none); a model no
  // source knows gets the generic ones. The preference stays as chosen and
  // is sent as the nearest level this model takes (chat/effort.js), so a
  // switch to a model without "xhigh" and back keeps it.
  const effortLevels = Array.isArray(modelInfo?.efforts) ? modelInfo.efforts : (aiInfo?.efforts || ["low", "medium", "high"]);
  const effort = effortFor(chatEffort, effortLevels);
  const nativePdf = activeModel ? activeModel.native_pdf !== false : true;
  // The mic shows only when a connection can transcribe (an OpenAI-protocol
  // key — /api/ai/models `transcribe`); without one dictation can only fail.
  const canDictate = !aiOff && !!aiInfo?.transcribe;
  const nativePdfNote = nativePdf ? "" :
    t("{provider} does not accept PDF files — the PDF is sent as extracted text instead (first {chatContextChars} characters; Settings / AI / Advanced AI settings / Context size).", { provider: activeModel?.provider_name || t("This provider"), chatContextChars: (chatContextChars || 0).toLocaleString() });
  const attachPdfManualRef = useRef(false); // the user toggled the PDF button themselves
  useEffect(() => {
    // A provider without native PDF input: drop the automatic "send the file
    // once" default. A deliberate manual switch-on stays (with its warning).
    if (!nativePdf && !attachPdfManualRef.current) setAttachPdf(false);
  }, [nativePdf]);
  // Extra chat context: selected PDF pages + whether to include notes/highlights.
  const [chatDocs, setChatDocs] = useState([]);
  // The Full PDF switch shows only while a PDF is in context: the open
  // page's, or an attached page's.
  const pdfInContext = !!pageAttach || chatDocs.some((id) => pageAttachment(homeBlocks.find((b) => b.id === id)));
  const [chatIncludeNotes, setChatIncludeNotes] = useState(false);
  const [chatFiles, setChatFiles] = useState([]); // uploaded PDFs ({name, data}) pending send
  const [docPicker, setDocPicker] = useState(false); // "Add pages" modal
  const [docPickerQuery, setDocPickerQuery] = useState("");
  const fileInputRef = useRef(null);
  const chatScrollRef = useRef(null);
  // Ctrl+scroll over the transcript: session-only text size (useTextScale).
  const chatTextScale = useTextScale();
  const chatScrollRefCb = useCallback((el) => { chatScrollRef.current = el; chatTextScale.ref(el); }, [chatTextScale.ref]);
  // Voice dictation (ChatGPT-style): mic records (live waveform), ■ transcribes
  // into the input via /api/ai/transcribe, ↑ transcribes and sends, × discards.
  const [dictation, setDictation] = useState(""); // "" | "rec" | "busy"
  const [recSecs, setRecSecs] = useState(0);
  const recRef = useRef(null); // {recorder, stream, chunks, canceled, autoSend, baseText, analyser, audioCtx, amps}
  const waveCanvasRef = useRef(null);
  // Latest sendChat for the record-view ↑ button — the recorder's onstop fires
  // from the render that started it, and a page switch mid-recording would
  // otherwise send through a stale conversation closure.
  const sendChatRef = useRef(null);

  // The active conversation's user-given name ("" = derived from its first
  // message). Renames go to the server directly; the autosave never sends
  // it, so a debounced save can't roll a rename back.
  const [chatTitle, setChatTitle] = useState("");

  // Show a conversation that came from the server (bucket switch, or a
  // history entry opened). PDF button: on until this document has been sent
  // in THIS conversation, then off. Messages record the doc ids they carried
  // (pdfDocs); older saves only have display names — treat any sent PDF as
  // covering the current one.
  function showLoaded(msgs, title) {
    setChatMessages(msgs);
    const lastUser = [...msgs].reverse().find((m) => m.role === "user");
    const references = [...new Set((lastUser?.contextPages || []).map((p) => p.id))].slice(0, MAX_CHAT_REFERENCES);
    setChatDocs(references);
    setChatIncludeNotes(!!lastUser?.includeNotes);
    setChatInput("");
    setChatTitle(title || "");
    const sent = msgs.some((m) => m.pdfDocs
      ? (docId && m.pdfDocs.includes(docId)) || m.pdfDocs.includes(focusedBlockId)
        || (!docId && references.some((id) => m.pdfDocs.includes(id)
          || m.pdfDocs.includes(pageAttachment(homeBlocks.find((b) => b.id === id))?.id)))
      : m.pdfs?.length);
    attachPdfManualRef.current = false;
    setAttachPdf(!sent && nativePdf);
  }

  // Load chat from backend whenever the chat bucket changes.
  useEffect(() => {
    let cancelled = false;
    setChatDocs([]);
    setChatIncludeNotes(false);
    setChatInput("");
    setDocPicker(false);
    const reply = session.getSnapshot().replies.get(chatKey);
    const reloadSaved = session.isSaved(chatKey);
    showLoaded(reply?.messages || [], reply?.title);
    setLoadError("");
    apiJson(`${API}/chats/${encodeURIComponent(chatKey)}`)
      .then(data => {
        if (cancelled) return;
        const latest = session.getSnapshot().replies.get(chatKey);
        // Once a reply was saved before this GET, the server is authoritative
        // again (another tab or a folder rename may have changed the bucket).
        if (reloadSaved && latest === reply && session.isSaved(chatKey)) {
          showLoaded(data.messages || [], data.title);
          session.forget(chatKey);
        } else {
          showLoaded(latest?.messages || data.messages || [], data.title);
        }
      })
      .catch((err) => { if (!cancelled && !session.getSnapshot().replies.has(chatKey)) setLoadError(t("Could not load chat: {message}", { message: err.message })); });
    return () => { cancelled = true; };
  }, [chatKey, docId, readOnly, session]);

  // History: the bucket's earlier conversations (server `chat_history`).
  // "New chat" archives the current one there instead of deleting it, and
  // opening an entry swaps it with the current one. Both send the client's
  // copy of the conversation, so a reply still sitting in the autosave
  // debounce is kept. The list loads lazily when the popover opens and is
  // dropped on any change (bucket switch, archive, open) so it re-fetches.
  const [history, setHistory] = useState(null); // null = not loaded yet
  const [historyQuery, setHistoryQuery] = useState("");
  const [renaming, setRenaming] = useState(null); // {id: "" = the active chat | entry id, text}
  const renameCancelRef = useRef(false);
  const historyOpen = openPopover === "chathistory";
  useEffect(() => { setHistory(null); setHistoryQuery(""); setRenaming(null); }, [chatKey]);
  useEffect(() => {
    if (readOnly || !historyOpen || history != null) return;
    let cancelled = false;
    apiJson(`${API}/chat-history?bucket=${encodeURIComponent(chatKey)}`)
      .then((data) => { if (!cancelled) setHistory(data.sessions || []); })
      .catch((err) => { if (!cancelled) { setHistory([]); setStatus(t("Chat history: {message}", { message: err.message })); } });
    return () => { cancelled = true; };
  }, [historyOpen, history, chatKey, readOnly]);
  const activeTitle = chatTitle || deriveTitle(chatMessages) || t("Untitled");
  // Names the paper a reply's citation pill points at: the page title for
  // its preview, and a short form (first author's surname, else the title
  // cut short) for replies that cite several papers.
  const citeTitles = useMemo(() => ({
    titleOf(id) {
      const page = homeBlocks.find((b) => b.id === id);
      const title = page?.content || (id === focusedBlockId ? pageTitle : "") || "";
      if (!title) return null;
      const first = page?.properties?.meta?.authors?.[0];
      const surname = first ? String(first).trim().split(/\s+/).pop() : "";
      return { title, short: surname || (title.length > 24 ? `${title.slice(0, 24).replace(/\s+\S*$/, "")}…` : title) };
    },
  }), [homeBlocks, focusedBlockId, pageTitle]);
  // Reserve the reply's bubble before the first stream event. This placeholder
  // is display-only; tool activity and answer text replace it in the same row.
  const visibleMessages = busyHere && (!chatMessages.length || chatMessages.at(-1).role === "user")
    ? [...chatMessages, { role: "ai", text: "", partial: true }]
    : chatMessages;
  const currentPayload = () => ({ bucket: chatKey, messages: chatMessages, title: chatTitle });

  async function newChat() {
    if (busyHere) return;
    const payload = currentPayload();
    try {
      await session.flush(chatKey);
      await apiJson(`${API}/chat-history/archive`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(payload) });
    } catch (err) {
      setStatus(t("Couldn't keep the conversation in history: {message}", { message: err.message }));
      return;
    }
    session.forget(chatKey);
    if (chatKeyRef.current !== chatKey) return;
    setChatMessages([]);
    setChatDocs([]);
    setChatInput("");
    setChatIncludeNotes(false);
    setChatTitle("");
    attachPdfManualRef.current = false;
    setAttachPdf(nativePdf); // new chat: first question carries the full PDF again (where the provider takes it)
    setHistory(null);
  }

  async function openHistory(id) {
    if (busyHere) return;
    try {
      await session.flush(chatKey);
      const data = await apiJson(`${API}/chat-history/${id}/open`,
        { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(currentPayload()) });
      session.forget(chatKey);
      if (chatKeyRef.current !== chatKey) return;
      showLoaded(data.messages || [], data.title);
      setHistory(null);
      setOpenPopover(null);
      chatStickRef.current = true;
    } catch (err) {
      setStatus(t("Couldn't open the conversation: {message}", { message: err.message }));
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
        setChatTitle(title);
        await session.flush(chatKey);
        await apiJson(`${API}/chats/${encodeURIComponent(chatKey)}`,
          { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ messages: chatMessages, title }) });
      } else {
        setHistory((prev) => (prev || []).map((s) => (s.id === edit.id ? { ...s, title } : s)));
        await apiJson(`${API}/chat-history/${edit.id}`,
          { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ title }) });
      }
    } catch (err) {
      setStatus(t("Couldn't rename the conversation: {message}", { message: err.message }));
    }
  }

  function deleteHistory(entry) {
    const run = async () => {
      setHistory((prev) => (prev || []).filter((s) => s.id !== entry.id));
      try {
        await apiJson(`${API}/chat-history/${entry.id}`, { method: "DELETE" });
      } catch (err) {
        setStatus(t("Couldn't delete the conversation: {message}", { message: err.message }));
        setHistory(null);
      }
    };
    askConfirm({
      title: T("Delete conversation"),
      message: t("Delete “{title}” from this chat's history? This can't be undone.", { title: entry.title || t("Untitled") }),
      confirmLabel: t("Delete"), danger: true, onConfirm: run,
    });
  }

  // Rows of the history popover: the active conversation first, then the
  // archived ones newest-first, filtered by the search box.
  const historyRows = useMemo(() => {
    const q = historyQuery.trim().toLowerCase();
    const active = { id: "", title: activeTitle, preview: deriveTitle(chatMessages), updated_at: "", active: true };
    const rows = [active, ...(history || [])];
    return q ? rows.filter((s) => `${s.title} ${s.preview || ""}`.toLowerCase().includes(q)) : rows;
  }, [history, historyQuery, activeTitle, chatMessages]);

  // Attach picked/pasted files: images join the pasted-figures row, PDFs
  // become one-shot native attachments (same as the library PDF button).
  function addChatFiles(files) {
    for (const f of files) {
      if (isPdfFile(f)) {
        if (f.size > 15 * 1024 * 1024) { setStatus(t("\"{name}\" is too large to attach (max 15 MB).", { name: f.name })); continue; }
        const reader = new FileReader();
        reader.onload = () => setChatFiles((prev) => prev.length >= 4 ? prev : [...prev, { name: f.name || "file.pdf", data: reader.result }]);
        reader.readAsDataURL(f);
      } else if (f.type?.startsWith("image/")) {
        if (f.size > 6 * 1024 * 1024) { setStatus(t("Image too large to attach (max 6 MB).")); continue; }
        const reader = new FileReader();
        reader.onload = () => setChatImages((prev) => prev.length >= 4 ? prev : [...prev, reader.result]);
        reader.readAsDataURL(f);
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
    addChatFiles(files);
  }

  const chatFindMatches = useMemo(() => {
    const q = chatFind.trim().toLowerCase();
    if (!q) return [];
    return chatMessages.map((m, i) => ((m.text || "").toLowerCase().includes(q) ? i : -1)).filter((i) => i >= 0);
  }, [chatFind, chatMessages]);
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
  useEffect(() => {
    const el = chatScrollRef.current;
    if (!el || !chatStickRef.current) return;
    const target = el.scrollHeight - el.clientHeight;
    if (el.scrollTop < target - 1) {
      chatProgScrollRef.current = true;
      el.scrollTop = target;
    }
  }, [chatMessages, busyHere]);

  // Core chat send. baseMessages overrides the history (used when re-sending
  // an edited message: everything after the edited message is discarded,
  // ChatGPT-style).
  async function sendChat(rawText, { baseMessages, referenceMessage, model } = {}) {
    if (readOnly || aiOff) return;
    const text = (rawText || "").trim();
    if (!text || busyHere) return;
    guideEvents.emit("chat.sent");
    const selectedDocs = referenceMessage ? (referenceMessage.contextPages || []).map((p) => p.id) : chatDocs;
    const includeNotes = referenceMessage ? !!referenceMessage.includeNotes : chatIncludeNotes;
    if (referenceMessage) { setChatDocs(selectedDocs); setChatIncludeNotes(includeNotes); }
    const selections = pdfSelections;
    const selection = selections.map((s) => s.text).join("\n\n---\n\n");
    setPdfSelections([]);
    // Note chips: attached blocks go as ids (the server serves their current
    // text, id-labelled, so the agent can edit them); selected note text as
    // verbatim passages.
    const notes = chatNotes || [];
    setChatNotes?.([]);
    const contextBlocks = notes.filter((n) => n.kind === "block").map((n) => n.id);
    // Selected note text goes as exact source ranges (the cursor block's
    // selection first) — edit_block mode "selection" rewrites only that.
    const cursorSel = cursorChip?.sel ? [{ id: cursorChip.id, ...cursorChip.sel }] : [];
    const noteSelections = [...cursorSel, ...notes.filter((n) => n.kind === "note")]
      .map((n) => ({ block_id: n.id, from: n.from, to: n.to, text: n.text }));
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
      const attachment = id === focusedBlockId ? pageAttach : pageAttachment(page);
      return attachment ? [{ id: attachment.id, title: page?.content || (id === focusedBlockId ? pageTitle : "") || t("Untitled") }] : [];
    });
    const sendingPdf = attachPdf && pdfPages.length > 0;
    const pdfNames = [
      ...files.map((f) => f.name),
      ...(sendingPdf ? pdfPages.map((p) => p.title) : []),
    ];
    const contextPages = selectedDocs.map((id) => ({ id, title: homeBlocks.find((b) => b.id === id)?.content || t("Untitled") }));
    const userMsg = {
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
    // One-shot semantics: the PDF went with this message; don't silently
    // re-upload (and re-bill) it on every follow-up.
    if (sendingPdf) setAttachPdf(false);
    let acc = ""; // streamed reply so far — kept on Stop
    const actions = []; // organizer mutations streamed for this reply
    let coverage = null; // {"context": [...]} — what the model was given, per document
    let answered = null; // {"model": {id, name, effort}} — which model answers, at what effort
    let usage = null; // the provider's token report, summed over the reply's rounds
    let lastRound = null; // the latest round's report alone — the context ring's figure
    let liveChars = 0; // characters received since the last report — the running estimate
    let running = null; // the tool call running now ({"step"} line), until its action lands
    const liveArgs = new Map(); // tool call id -> argument chars previewed so far (cumulative)
    const aiMsg = (extra = {}) => ({
      role: "ai", text: acc,
      ...(actions.length ? { actions: [...actions] } : {}),
      ...(coverage ? { context: coverage } : {}),
      ...(answered ? { model: answered.name, ...(answered.effort ? { effort: answered.effort } : {}) } : {}),
      ...(usage ? { usage } : {}),
      ...(lastRound ? { context_tokens: (lastRound.input || 0) + (lastRound.output || 0) } : {}),
      ...extra,
    });
    try {
      const res = await fetch(`${API}/ai/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        signal: ctrl.signal,
        body: JSON.stringify({
          prompt: text,
          page_id: focusedBlockId || "",
          history: prevMessages.filter((m) => !m.error), // failed replies aren't answers
          model: model || chatModel || "",
          selections,
          focus_block_id: cursorChip ? cursorChip.id : "",
          context_blocks: contextBlocks,
          note_selections: noteSelections,
          attach_pdf: sendingPdf,
          effort,
          system: chatSystem || "",
          pages: selectedDocs.length ? contextIds : [],
          include_notes: includeNotes,
          images,
          files,
          context_char_limit: chatContextChars,
          multi_context_char_limit: multiContextChars,
          stream: true,
          ...agentPayload(),
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
          } else if (ev.action) {
            running = null;
            actions.push(ev.action);
            // Live: the notes panel lights up the block the agent just
            // read/edited (and reloads the tree for an applied edit).
            onAgentEvent?.({ type: "action", action: ev.action });
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
        if (acc || actions.length || usage || running) {
          showReply(aiMsg({ partial: true, live: liveChars, ...(running ? { step: running } : {}) }));
        }
      });
      showReply(aiMsg({ text: acc || (actions.length ? "" : t("(no response)")) }), true);
      if (gammaLinksIn(acc).some((link) => link.kind === "citation")) guideEvents.emit("chat.cited");
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
      session.finish(sendKey);
      onAgentEvent?.({ type: "done", key: sendKey });
      // Agent tools changed the library — reload the home feed. Read-only
      // tool calls (list/read/search) render as chips but change nothing.
      if (actions.some(isChange)) onLibraryChange?.();
      // Note-block edits carry the page(s) they touched, so the open page's
      // block tree can reload and show the change.
      const notePages = [...new Set(actions
        .filter((a) => !a.error && BLOCK_TOOLS.has(a.tool))
        .flatMap((a) => [a.page_id, a.src_page_id].filter(Boolean)))];
      if (notePages.length) onNotesChange?.(notePages);
    }
  }

  sendChatRef.current = sendChat;

  function sendChatMessage() {
    const text = chatInput;
    if (!text.trim() || busyHere) return;
    setChatInput("");
    sendChat(text);
  }

  function stopChat() {
    session.stop(chatKey);
  }

  async function startDictation() {
    if (dictation) return;
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setStatus(t("Voice input needs HTTPS or localhost — the browser blocks the microphone on plain HTTP."));
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setStatus(t("Microphone access was denied."));
      return;
    }
    // Chrome/Firefox record webm/opus; Safari only mp4 (m4a to OpenAI).
    const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((t) => MediaRecorder.isTypeSupported(t)) || "";
    const rec = {
      recorder: new MediaRecorder(stream, mime ? { mimeType: mime } : undefined),
      stream, chunks: [], canceled: false, autoSend: false,
      baseText: chatInput, // composer text at record start (the input is hidden while recording)
      amps: [], peak: 0,
    };
    // Analyser feeds the live waveform; recording works fine without it.
    try {
      const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 1024;
      audioCtx.createMediaStreamSource(stream).connect(analyser);
      rec.audioCtx = audioCtx;
      rec.analyser = analyser;
    } catch {}
    recRef.current = rec;
    rec.recorder.ondataavailable = (e) => { if (e.data?.size) rec.chunks.push(e.data); };
    rec.recorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      rec.audioCtx?.close().catch(() => {});
      if (rec.canceled) return;
      transcribeDictation(new Blob(rec.chunks, { type: rec.recorder.mimeType || mime || "audio/webm" }), rec);
    };
    rec.recorder.start();
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
    try { rec.recorder.stop(); } catch { setDictation(""); }
    if (rec.canceled) recRef.current = null;
  }

  async function transcribeDictation(blob, rec) {
    try {
      if (blob.size > 24 * 1024 * 1024) throw new Error("recording too long (max ~25 MB)");
      const form = new FormData();
      form.append("file", blob, blob.type.includes("mp4") ? "dictation.m4a" : "dictation.webm");
      if (dictationModel) form.append("model", dictationModel);
      const language = dictationLang === "auto" ? "" : dictationLang || getLocale();
      if (language) form.append("language", language);
      if (chatModel) form.append("model_hint", chatModel);
      const data = await apiJson(`${API}/ai/transcribe`, { method: "POST", body: form });
      const text = (data.text || "").trim();
      if (text && rec.autoSend) {
        setChatInput("");
        sendChatRef.current?.([rec.baseText.trim(), text].filter(Boolean).join(" "));
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
      try { rec.recorder.stop(); } catch {}
      rec.stream.getTracks().forEach((t) => t.stop());
      rec.audioCtx?.close().catch(() => {});
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
  // reply Retry and a model switch that retries with the model picked.
  function errorActions(m, idx) {
    const copy = failureCopy(m.errorKind, { provider: m.errorProvider, auth: m.errorAuth });
    const latest = idx === chatMessages.length - 1 && !busyHere && !readOnly;
    const out = [];
    const openEntry = () => openAiKeysEditor({ entry: m.errorProviderId });
    const fix = {
      connect: openAiKeysEditor, own_key: openAiKeysEditor, key: openEntry, signin: openEntry,
      connection: openEntry, new_chat: latest ? newChat : null,
    }[copy.fix];
    if (fix && !readOnly) {
      out.push(<button key="fix" type="button" className="uiBtn sm primary" onClick={() => fix()}>{fixLabel(copy.fix)}</button>);
    }
    if (latest) {
      out.push(<button key="retry" type="button" className="uiBtn sm" onClick={() => retryReply(idx)}>{t("Retry")}</button>);
      const others = copy.switchModel ? headerModels.filter((x) => x.id !== chatModel) : [];
      if (others.length) {
        out.push(<ActionMenu key="switch" label={t("Switch model")} items={others.map((x) => ({
          label: modelLabel(x),
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

  // Header: one icon strip (the PDF zoom column's buttons, laid flat) —
  // the context ring (opens the settings popover, whose Tokens section
  // spells it out), ⚙ chat settings (context size, tools and tokens — the
  // same prefs Settings / AI edits, in a popover; the model and effort are
  // the composer's model chip), Tools, Find, New chat.
  const settingsOpen = openPopover === "chatsettings";
  const findBtn = (
    <button type="button" className={`ctlBtn ${chatFindOpen ? "modeActive" : ""}`}
      onClick={() => { setChatFindOpen((v) => !v); setChatFind(""); }}
      title={t("Find in this conversation")} aria-label={t("Find in this conversation")}>
      <SearchIcon size={16} />
    </button>
  );
  // No AI connected: the setup card, alone in the empty transcript or after
  // an earlier conversation.
  const setupCard = aiOff ? (
    <ChatSetupCard info={setupInfo} isAdmin={isAdmin} openSettings={openSettings}
      onConnect={(service) => openAiKeysEditor({ service })} />
  ) : null;
  const headerContent = aiOff ? null : (
    <>
      <div className="ctlBtnRow chatPanelHeaderBtns">
        {headerModels.length > 0 ? (() => {
          const totalUsage = conversationUsage(chatMessages);
          const usageTitle = totalUsage ? t("; this conversation: {input} tokens in, {output} out", { input: fmtTokens(totalUsage.input), output: fmtTokens(totalUsage.output) }) : "";
          const ctxText = !ctxUsed ? ""
            : ctxWindow ? t("Context: {used} of {size} tokens ({percent}%)", { used: fmtTokens(ctxUsed), size: fmtTokens(ctxWindow), percent: Math.round((ctxUsed / ctxWindow) * 100) })
            : t("Context: {used} tokens", { used: fmtTokens(ctxUsed) });
          const toggleSettings = () => setOpenPopover((p) => (p === "chatsettings" ? null : "chatsettings"));
          return (
            <span data-popover="chatsettings" className="popoverAnchor">
              {ctxUsed && ctxWindow ? (
                <button type="button" className="ctlBtn" onClick={toggleSettings}
                  title={t("{ctxText} — the last reply's prompt and answer in {model}'s context window", { ctxText, model: headerModel.model })}
                  aria-label={ctxText}>
                  <ContextRing fraction={ctxUsed / ctxWindow} />
                </button>
              ) : null}
              <button type="button" data-guide="chat.settings" className={`ctlBtn ${settingsOpen ? "modeActive" : ""}`}
                onClick={toggleSettings}
                title={t("Chat settings — context {chatContextChars} chars, tools, tokens{usageTitle}", { chatContextChars: chatContextChars.toLocaleString(), usageTitle })}
                aria-label={t("Chat settings")} aria-expanded={settingsOpen}>
                <SettingsIcon size={16} />
              </button>
              {settingsOpen ? (
                <div className="popover chatSettingsPop">
                  <div className="popoverHint">{t("Global settings for all chats in this browser. Changes also appear in Settings. The model and reasoning effort are on the message box.")}</div>
                  <div className="popoverSection">{t("Context per page · {pages}", { pages: approxPages(chatContextChars) })}</div>
                  <CharSlider value={chatContextChars} onChange={setChatContextChars} />
                  <div className="popoverHint">
                    {t("Extracted PDF text sent with each message. The multi-page total and the agent's read window are in Settings / AI / Advanced AI settings.")}
                  </div>
                  <div className="popoverSection">{t("Tools")}</div>
                  <label className="chatToolPermRow" title={t("Allow assistant tools in all chats")}>
                    <input type="checkbox" checked={toolsEnabled} onChange={toggleTools} />
                    <SlidersIcon size={14} />
                    <span>{t("Allow tools in all chats")}</span>
                  </label>
                  <div className="chatToolPicker">
                    <AgentToolPicker kind={chatKind} perms={agentPerms} setPerms={setAgentPerms} disabled={!toolsEnabled} />
                  </div>
                  <div className="popoverHint">
                    {t("Applies to all {kind} conversations in this browser.", { kind: chatKindLabel.toLowerCase() })}
                  </div>
                  <div className="popoverSection">{t("Tokens · this conversation")}</div>
                  {totalUsage ? (
                    <div className="chatUsageTotal" title={usageDetail(totalUsage)}>
                      <UsageLine usage={totalUsage} className="chatMsgUsage inline" />
                      {ctxUsed ? <span className="chatMsgUsage inline">{ctxWindow ? <ContextRing fraction={ctxUsed / ctxWindow} /> : null}{ctxText}</span> : null}
                      <span className="popoverHint">{t("{n} replies counted, as the provider reported them. Totals per day and model: Settings / AI / Token usage.", { n: chatMessages.filter((m) => m.role === "ai" && m.usage).length })}</span>
                    </div>
                  ) : (
                    <div className="popoverHint">{t("No token counts yet — they appear under each reply once the provider reports them.")}</div>
                  )}
                </div>
              ) : null}
            </span>
          );
        })() : null}
        <button
          type="button"
          className={`ctlBtn ${toolsEnabled ? "modeActive" : ""}`}
          data-guide="chat.tools"
          aria-pressed={toolsEnabled}
          aria-label={`Tools ${toolsEnabled ? "on" : "off"}`}
          onClick={() => { setOpenPopover(null); toggleTools(); }}
          title={t("Tools {off} for all chats - click to change the global setting", { off: toolsEnabled ? t("on") : t("off") })}
        >
          <SlidersIcon size={16} />
        </button>
        {findBtn}
        <span data-popover="chathistory" className="popoverAnchor">
          <button type="button" className={`ctlBtn ${historyOpen ? "modeActive" : ""}`}
            onClick={() => setOpenPopover((p) => (p === "chathistory" ? null : "chathistory"))}
            title={t("Chat history — earlier conversations of {scope}", { scope: folderChat ? t("this folder") : t("this page") })}
            aria-label={t("Chat history")} aria-expanded={historyOpen}>
            <HistoryIcon size={16} />
          </button>
          {historyOpen ? (
            <div className="popover chatHistoryPop">
              <input
                autoFocus
                className="searchInput"
                value={historyQuery}
                onChange={(e) => setHistoryQuery(e.target.value)}
                placeholder={t("Search conversations…")}
                onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); setOpenPopover(null); } }}
              />
              <div className="chatHistList">
                {historyRows.map((s) => renaming?.id === s.id ? (
                  <div key={s.id} className="chatHistRow renaming">
                    <input
                      autoFocus
                      className="aiKeyInput"
                      value={renaming.text}
                      placeholder={t("Conversation name")}
                      onChange={(e) => setRenaming({ id: s.id, text: e.target.value })}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur(); }
                        else if (e.key === "Escape") { e.preventDefault(); renameCancelRef.current = true; e.currentTarget.blur(); }
                      }}
                      onBlur={commitRename}
                    />
                  </div>
                ) : (
                  <div key={s.id} className={`chatHistRow${s.active ? " active" : ""}`}
                    role="button" tabIndex={0}
                    title={s.active ? t("The conversation shown now") : `${s.preview || s.title}${s.count ? ` · ${s.count} messages` : ""}`}
                    onClick={() => { if (!s.active) openHistory(s.id); }}
                    onKeyDown={(e) => { if (e.key === "Enter" && !s.active) openHistory(s.id); }}>
                    <span className="chatHistTitle">{s.title || t("Untitled")}</span>
                    <span className="chatHistAge">{s.active ? "now" : relAge(s.updated_at)}</span>
                    <span className="ctlBtnRow chatHistActs" onClick={(e) => e.stopPropagation()}>
                      <button type="button" className="ctlBtn" title={t("Rename")} aria-label={t("Rename conversation")}
                        onClick={() => { renameCancelRef.current = false; setRenaming({ id: s.id, text: s.active ? chatTitle : s.title || "" }); }}>
                        <PencilIcon size={16} />
                      </button>
                      {!s.active ? (
                        <button type="button" className="ctlBtn" title={t("Delete")} aria-label={t("Delete conversation")}
                          onClick={() => deleteHistory(s)}>
                          <TrashIcon size={16} />
                        </button>
                      ) : null}
                    </span>
                  </div>
                ))}
                {history == null ? <div className="popoverHint">{t("Loading…")}</div>
                  : historyRows.length <= 1 && !historyQuery.trim() ? <div className="popoverHint">{t("No earlier conversations — New chat keeps the current one here.")}</div>
                  : !historyRows.length ? <div className="popoverHint">{t("No conversation matches.")}</div>
                  : null}
              </div>
            </div>
          ) : null}
        </span>
        <button type="button" className="ctlBtn" onClick={newChat} disabled={busyHere}
          title={t("New chat — keeps this conversation in history and starts a fresh one")} aria-label={t("New chat")}>
          <PlusIcon size={16} />
        </button>
      </div>
    </>
  );

  return (
    <DockWindow title={t("Chat")} onGrip={onGrip} onGripDoubleClick={onGripDoubleClick}
      collapsed={collapsed} onClose={onClose} headerContent={readOnly ? <>
        <span className="uiTag">{t("Read only")}</span>
        {findBtn}
      </> : headerContent}>
    <div className="chatPanel chatWindow">
      {!readOnly && aiHealth && !aiHealth.ok ? (
        // The login connection check found the active provider broken — say so
        // here, where the failure would otherwise surface mid-conversation,
        // in the error card's words. Only a probe with no model picked comes
        // without a kind.
        <div className="chatHealthStrip" title={aiHealth.error || ""}>
          <span className="chatHealthText">
            {aiHealth.kind ? failureCopy(aiHealth.kind, { provider: aiHealth.provider_name, auth: aiHealth.provider_auth }).headline : <>
              {aiHealth.provider_name ? `${aiHealth.provider_name}: ` : ""}
              {t("connection failed — {unreachable}", { unreachable: aiHealth.error || t("provider unreachable") })}
            </>}
          </span>
          <button className="uiBtn sm" onClick={() => openAiKeysEditor({ entry: aiHealth.provider_id })}>{t("Fix…")}</button>
          <button className="uiClose" onClick={dismissAiHealth} title={t("Dismiss")} aria-label={t("Dismiss")}><XIcon size={14} /></button>
        </div>
      ) : null}
      {chatFindOpen ? (
        <div className="chatFindRow">
          <input
            autoFocus
            className="searchInput"
            value={chatFind}
            onChange={(e) => setChatFind(e.target.value)}
            placeholder={t("Find in chat…")}
            onKeyDown={(e) => {
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
      <ChatCiteContext.Provider value={citeTitles}>
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
        {setupCard && !visibleMessages.length && !loadError ? setupCard : visibleMessages.length === 0 ? (
          <div className="chatEmpty">
            {loadError || (readOnly ? t("No saved conversation for this page.")
              : focusedBlockId ? t("Ask AI about this page…") : agentIntro || t("Ask AI anything, or generate a report from your pages…"))}
          </div>
        ) : (
          visibleMessages.map((m, i) => {
            const isUser = m.role === "user";
            const isResponding = busyHere && !isUser && m.partial && i === visibleMessages.length - 1;
            const isFindHit = chatFindOpen && chatFind.trim() && chatFindMatches[chatFindIdx] === i;
            if (editingMsg?.idx === i) {
              const resend = () => { const text = editingMsg.text; setEditingMsg(null); resendFrom(i, text); };
              return (
                <div key={i} className="chatBubbleRow user" data-msg-idx={i}>
                  <div className="chatMsgCol">
                    <div className="chatBubble user chatEditBubble">
                      <AutoGrowTextarea
                        autoFocus
                        className="chatEditTextarea"
                        value={editingMsg.text}
                        onChange={(e) => setEditingMsg({ idx: i, text: e.target.value })}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" && !e.shiftKey) {
                            e.preventDefault();
                            resend();
                          } else if (e.key === "Escape") { e.preventDefault(); setEditingMsg(null); }
                        }}
                      />
                      <div className="chatEditBtns">
                        <button type="button" className="uiBtn sm" onClick={() => setEditingMsg(null)}>{t("Cancel")}</button>
                        <button type="button" className="uiBtn sm chatEditSend"
                          disabled={!editingMsg.text.trim() || busyHere}
                          onClick={resend}
                          title={t("Re-send — replaces this message and everything after it")}>{t("Send")}</button>
                      </div>
                    </div>
                  </div>
                </div>
              );
            }
            return (
              <div key={i} className={`chatBubbleRow ${isUser ? "user" : "ai"}${isFindHit ? " findHit" : ""}`} data-msg-idx={i}>
                <div className="chatMsgCol">
                  <div className={`chatBubble ${isUser ? "user" : "ai"}${m.error && !m.errorKind ? " error" : ""}`}>
                    {m.images?.length ? (
                      <div className="chatMsgImages">
                        {m.images.map((src, j) => <img key={j} src={src} className="chatMsgImage" alt={t("pasted figure")} />)}
                      </div>
                    ) : null}
                    {m.pdfs?.length ? (
                      <div className="chatMsgPdfs">
                        {m.pdfs.map((n, j) => (
                          <span key={j} className="chatPdfChip" title={n}>
                            <FileIcon size={14} />
                            {n.slice(0, 40)}{n.length > 40 ? "…" : ""}
                          </span>
                        ))}
                      </div>
                    ) : null}
                    {!isUser && m.context?.length ? (
                      <ContextCoverage items={m.context} />
                    ) : null}
                    {!isUser && (m.actions?.length || (isResponding && m.step)) ? (
                      <AgentSteps actions={m.actions || []} running={isResponding ? m.step : null}
                        open={openActions.has(`${i}:steps`)} onToggle={() => toggleAction(`${i}:steps`)}
                        titleOf={(id) => citeTitles.titleOf(id)?.title || ""}>
                      <div className="chatToolActions">
                        {(m.actions || []).map((a, j) => {
                          const Icon = ACTION_ICONS[a.kind] || FolderIcon;
                          // Chats saved before tool output was recorded have
                          // no raw call — those chips stay plain text.
                          const hasDetail = !!(a.tool || a.result);
                          const key = `${i}:${j}`;
                          const open = openActions.has(key);
                          return (
                            <div key={j} className={`chatToolAction${a.error ? " err" : ""}`}>
                              {hasDetail ? (
                                <button type="button" className="chatToolActionHead"
                                  onClick={() => toggleAction(key)}
                                  title={open ? t("Hide tool output") : t("Show tool output")}>
                                  <Icon size={14} />
                                  <span>{a.summary}</span>
                                  {open ? <ChevronUpIcon size={10} /> : <ChevronDownIcon size={10} />}
                                </button>
                              ) : (
                                <div className="chatToolActionHead plain" title={a.summary}>
                                  <Icon size={14} />
                                  <span>{a.summary}</span>
                                </div>
                              )}
                              {open ? <pre className="chatToolDetail">{toolCallText(a)}</pre> : null}
                            </div>
                          );
                        })}
                      </div>
                      </AgentSteps>
                    ) : null}
                    {!isUser && m.actions?.length ? <AgentChanges actions={m.actions} onOpenPage={onOpenPage} /> : null}
                    {isUser && m.contextPages?.length ? <div className="chatMsgPdfs">
                      {m.contextPages.map((p) => <button type="button" key={p.id} className="crumbBtn" title={p.title} onClick={() => onOpenPage?.(p.id)}><BookIcon size={14} /><span className="linkChipText">{p.title}</span></button>)}
                    </div> : null}
                    {isUser
                      ? <div className="chatUserText">{m.text}</div>
                      : m.text && !(m.error && m.errorKind) ? <ChatMarkdown text={m.text} copyBlocks /> : null}
                    {!isUser && m.errorKind && !isResponding ? (
                      <ChatErrorCard message={m} compact={!m.error} actions={errorActions(m, i)} />
                    ) : null}
                    {isResponding && !m.step ? (
                      <div className="chatThinking" role="status" aria-label={m.text ? t("AI is responding") : t("AI is thinking")}>
                        <span aria-hidden="true">{m.text ? t("Responding") : t("Thinking")}</span>
                        <span className="chatTyping" aria-hidden="true"><span /><span /><span /></span>
                        <UsageLine usage={liveUsage(m.usage, m.live)} className="chatMsgUsage live" />
                      </div>
                    ) : null}
                  </div>
                  {!isResponding ? <div className="chatMsgFoot">
                    {!isUser && m.model ? (
                      <span className="chatMsgModel" title={t("Model and reasoning effort")}>
                        {m.effort ? `${m.model} · ${m.effort}` : m.model}
                      </span>
                    ) : null}
                    {!isUser ? <UsageLine usage={m.usage} /> : null}
                    <div className="chatMsgActions">
                    <button type="button" className="chatMsgActionBtn" title={t("Copy message")}
                      onClick={() => copyChatMessage(i, m.text)}>
                      {copiedMsgIdx === i
                        ? <CheckIcon size={14} />
                        : <CopyIcon size={14} />}
                    </button>
                    {!readOnly && isUser && !busyHere ? (
                      <button type="button" className="chatMsgActionBtn" title={t("Edit and re-send (removes later messages)")}
                        onClick={() => setEditingMsg({ idx: i, text: m.text })}>
                        <PencilIcon size={14} />
                      </button>
                    ) : null}
                    </div>
                  </div> : null}
                </div>
              </div>
            );
          })
        )}
        {/* An earlier conversation stays readable; the card follows it. */}
        {visibleMessages.length ? setupCard : null}
      </div>
      </ChatCiteContext.Provider>
      {!readOnly ? (
      // One box: the context chips on top, the message at full
      // width, then a toolbar — [+], the Full PDF switch while a PDF is in
      // context, the model chip (with reasoning effort), the mic when a
      // connection can transcribe, send / stop. While recording the text
      // and the toolbar give way to the waveform row.
      <form
        ref={composerRef}
        className={`chatComposer${aiOff ? " off" : ""}`}
        data-guide="chat.composer"
        onSubmit={(e) => { e.preventDefault(); sendChatMessage(); }}
      >
        {pdfSelections.length || chatNotes?.length || cursorChip ? (
          <div className="chatSelChips">
            {cursorChip ? (
              cursorChip.sel ? (
                <SelChip kind="isCursor" icon="selection" auto label={t("Selection in this note")} text={cursorChip.sel.text}
                  title={`${t("The text you selected in this note — the assistant changes only this part.")}\n\n${cursorChip.sel.text}`}
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
                label={pdfSelections.length > 1 ? t("PDF passage {i}", { i: i + 1 }) : t("PDF passage")}
                note={s.page ? t("p. {page}", { page: s.page }) : ""}
                title={`${s.text}\n\n${t("Hold Ctrl while selecting in the PDF to add more passages")}`}
                onRemove={() => setPdfSelections((prev) => prev.filter((_, j) => j !== i))}
                removeTitle={t("Remove this passage")} />
            ))}
            {(chatNotes || []).map((n, i) => (
              <SelChip key={`n${i}`} kind={n.kind === "block" ? "isBlock" : "isNote"} text={n.text}
                icon={n.kind === "block" ? "block" : "selection"}
                label={n.kind === "block" ? t("Attached block") : t("Selected note text")}
                title={`${n.kind === "block" ? t("A note block attached with Ctrl+click or the ⋮⋮ menu — the assistant gets its text and id") : t("Note text selected with Ctrl held — the assistant changes only this part")}\n\n${n.text}`}
                onRemove={() => setChatNotes?.((prev) => prev.filter((_, j) => j !== i))}
                removeTitle={n.kind === "block" ? t("Detach this block") : t("Remove this passage")} />
            ))}
          </div>
        ) : null}
        {chatDocs.length ? (
          <div className="chatReferenceStrip" aria-label={t("Attached library pages")}>
            <span className="chatReferenceLabel" title={t("Paper details and text stay in context for follow-up questions. Notes are optional in the library picker.")}>{t("Context")}</span>
            {chatDocs.map((id) => <span className="chatReferenceChip" key={id}>
              <button type="button" className="crumbBtn" title={homeBlocks.find((b) => b.id === id)?.content || t("Unavailable page")} onClick={() => onOpenPage?.(id)}><BookIcon size={14} /><span className="linkChipText">{homeBlocks.find((b) => b.id === id)?.content || t("Unavailable page")}</span></button>
              <button type="button" className="uiClose uiCloseSm" aria-label={t("Remove {page} from context", { page: homeBlocks.find((b) => b.id === id)?.content || t("page") })} onClick={() => setChatDocs((prev) => prev.filter((p) => p !== id))}><XIcon size={14} /></button>
            </span>)}
          </div>
        ) : null}
        {chatFiles.length ? (
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
        {chatImages.length ? (
          <div className="chatImgPreviewRow" data-guide="chat.imageContext">
            {chatImages.map((src, i) => (
              <span key={i} className="chatImgPreview">
                <img src={src} alt={t("pasted figure")} />
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
          pages={homeBlocks} openTabs={openTabs} selected={chatDocs}
          onAttach={(id) => setChatDocs((prev) => prev.includes(id) ? prev : [...prev, id])}
          onSend={sendChatMessage}
          className="chatInput chatInputArea"
          data-guide="chat.input"
          rows={1}
          value={chatInput}
          onChange={setChatInput}
          onPaste={handleChatPaste}
          disabled={aiOff}
          placeholder={aiOff ? t("Connect an AI service to start chatting") : (
            // Names what the message will be about, most specific attachment first.
            chatFiles.length ? `Ask about the attached file${chatFiles.length > 1 ? "s" : ""}…`
            : chatImages.length ? t("Ask about the pasted figure…") : pdfSelections.length > 1 ? `Ask about the ${pdfSelections.length} selected passages…`
            : pdfSelections.length ? t("Ask about the selection…") : chatNotes?.length > 1 ? t("Ask about the {n} attached notes…", { n: chatNotes.length })
            : chatNotes?.length ? (chatNotes[0].kind === "block" ? t("Ask about the attached block…") : t("Ask about the selected note…"))
            : cursorChip?.sel ? t("Ask about the selection…") : cursorChip ? t("Ask about this block…") : chatDocs.length ? `Ask about ${chatDocs.length} attached page${chatDocs.length > 1 ? "s" : ""}…`
            : agentAsk || t("Ask…")
          ) + t(" (@ paper)")}
        />
        <div className="chatComposerBar">
          <span data-popover="chatdocs" className="popoverAnchor">
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
              <div className="popover popUp chatPlusMenu">
                <button type="button" className="chatPlusMenuItem"
                  onClick={() => { setOpenPopover(null); fileInputRef.current?.click(); }}>
                  <span className="chatPlusMenuIcon">
                    <PaperclipIcon size={16} />
                  </span>
                  <span className="chatPlusMenuLabel">{t("Add photos & files")}</span>
                  <span className="chatPlusMenuHint">{t("Images or PDFs from your computer")}</span>
                </button>
                <button type="button" className="chatPlusMenuItem"
                  onClick={() => { setOpenPopover(null); setDocPickerQuery(""); setDocPicker(true); }}>
                  <span className="chatPlusMenuIcon">
                    <BookIcon size={16} />
                  </span>
                  <span className="chatPlusMenuLabel">{t("Add pages from library")}</span>
                  <span className="chatPlusMenuHint">{chatDocs.length ? `${chatDocs.length} selected` : t("Search your pages")}</span>
                </button>
              </div>
            ) : null}
          </span>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept="image/*,application/pdf"
            style={{ display: "none" }}
            onChange={(e) => { addChatFiles(Array.from(e.target.files || [])); e.target.value = ""; }}
          />
          {pdfInContext && !aiOff ? <button
            type="button"
            className={`chatAttachToggle chatPdfToggle ${attachPdf ? "on" : ""}`}
            aria-pressed={attachPdf}
            onClick={() => { attachPdfManualRef.current = true; setAttachPdf((v) => !v); }}
            title={!nativePdf
              ? `${attachPdf ? t("On, but: ") : ""}${nativePdfNote}`
              : attachPdf
                ? t("Full PDF file is sent with each message (model sees figures & tables). Click to switch to extracted text only.") : t("Send the full PDF file with your messages so the model sees figures & tables (uses more tokens). Click to enable.")}
          >
            <FileIcon size={14} />
            {t("Full PDF")}
          </button> : null}
          {attachPdf && pdfInContext && !nativePdf && !aiOff ? (
            <button type="button" className="chatAttachToggle chatPdfToggle chatPdfWarn"
              onClick={() => setStatus(nativePdfNote)} title={nativePdfNote}
              aria-label={t("This provider cannot accept PDF files")}>
              <AlertCircleIcon size={14} />
            </button>
          ) : null}
          <span className="chatComposerSpacer" />
          {!aiOff && headerModels.length ? (
            <span className="chatModelChip">
              <MenuSelect
                up
                label={t("Model and reasoning effort")}
                heading={t("Model")}
                value={headerModel.id}
                onChange={setChatModel}
                display={effort ? `${headerModel.model} · ${effort}` : headerModel.model}
                options={headerModels.map((m) => [m.id, modelLabel(m)])}
                sections={effortLevels.length ? [{
                  label: t("Reasoning effort"),
                  value: effort,
                  onChange: setChatEffort,
                  options: [["", t("Default")], ...effortLevels.map((ef) => [ef, ef])],
                }] : []}
              />
            </span>
          ) : null}
          {busyHere ? (
            <button className="uiBtn chatCircleBtn chatStopBtn" type="button" onClick={stopChat} title={t("Stop generating")} aria-label={t("Stop generating")}>
              <StopIcon size={14} />
            </button>
          ) : dictation === "busy" ? (
            <button className="uiBtn chatCircleBtn chatMicBtn" type="button" disabled title={t("Transcribing…")} aria-label={t("Transcribing")}>
              <span className="transferSpin inline" />
            </button>
          ) : (
            <>
              {canDictate ? <button className="uiBtn chatCircleBtn chatMicBtn" data-guide="chat.voice" type="button" onClick={startDictation} title={t("Dictate — transcribed with your OpenAI key")} aria-label={t("Start dictation")}>
                <MicIcon size={14} />
              </button> : null}
              <button className="uiBtn primary chatCircleBtn" type="submit" disabled={aiOff || !chatInput.trim()} title={t("Send")} aria-label={t("Send")}>
                <ArrowUpIcon size={14} />
              </button>
            </>
          )}
        </div>
        </>
        )}
      </form>
      ) : null}
      {docPicker ? (
        <div className="reportOverlay" onClick={() => setDocPicker(false)}>
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
              autoFocus data-find
              className="searchInput"
              placeholder={t("Search your pages…")}
              value={docPickerQuery}
              onChange={(e) => setDocPickerQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") { e.preventDefault(); setDocPicker(false); }
                // Enter ticks the best match (with a query) and selects the
                // query, so the next name typed replaces it; on an empty box
                // it is Done.
                if (e.key === "Enter" && !e.nativeEvent.isComposing) {
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
                const pages = homeBlocks;
                if (!pages.length) return <div className="popoverHint">{t("No pages yet — create one first.")}</div>;
                const title = (b) => b.content || t("Untitled");
                const byRecency = (x, y) => (y.updated_at || "").localeCompare(x.updated_at || "");
                const row = (b, badge) => (
                  <label key={b.id} className="docPickerItem" title={title(b)}>
                    <input
                      type="checkbox"
                      checked={chatDocs.includes(b.id)}
                      disabled={!chatDocs.includes(b.id) && chatDocs.length >= MAX_CHAT_REFERENCES}
                      onChange={(e) => setChatDocs((prev) => e.target.checked
                        ? [...prev, b.id]
                        : prev.filter((id) => id !== b.id))}
                    />
                    <span className="attachName">{title(b)}</span>
                    {badge || null}
                  </label>
                );
                const q = docPickerQuery.trim().toLowerCase();
                if (q) {
                  const score = createTitleScorer(q);
                  const hits = pages
                    .filter((b) => score && score(b) > 0)
                    .sort((a, b) => score(b) - score(a) || byRecency(a, b));
                  return hits.length
                    ? hits.map((b) => row(b))
                    : <div className="popoverHint">{t("No pages match “{docPickerQuery}”.", { docPickerQuery: docPickerQuery.trim() })}</div>;
                }
                // No search: pages open as tabs first (the likely candidates),
                // then the rest of the library by recency.
                const tabIds = (openTabs || []).map((t) => t.id);
                const inTabs = tabIds.map((id) => pages.find((b) => b.id === id)).filter(Boolean);
                const rest = pages.filter((b) => !tabIds.includes(b.id)).sort(byRecency);
                return (
                  <>
                    {inTabs.length ? <div className="popoverSection">{t("Open tabs")}</div> : null}
                    {inTabs.map((b) => row(b, b.id === focusedBlockId
                      ? <span className="docPickerBadge">{t("current")}</span> : null))}
                    {rest.length ? <div className="popoverSection">{t("Library")}</div> : null}
                    {rest.map((b) => row(b))}
                  </>
                );
              })()}
            </div>
            <label className="docPickerItem docPickerNotes">
              <input type="checkbox" checked={chatIncludeNotes} onChange={(e) => setChatIncludeNotes(e.target.checked)} />
              <span className="attachName">{t("Include my notes & highlights")}</span>
            </label>
            {!chatDocs.length && docId ? (
              <div className="popoverHint">{t("Nothing selected — the open page is used.")}</div>
            ) : null}
            <div className="reportModalBtns">
              {chatDocs.length ? (
                <button className="uiBtn" onClick={() => setChatDocs([])}>{t("Clear selection")}</button>
              ) : null}
              <button className="uiBtn primary" onClick={() => setDocPicker(false)}>{t("Done")}</button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
    </DockWindow>
  );
}
