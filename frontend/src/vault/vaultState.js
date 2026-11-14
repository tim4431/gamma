// How the open page stands with this computer, read from the desktop app's
// own server (GET /api/folder-links/page/{id}, routers/folder_links.py
// `page_state`; docs/research/vaults.md "The words" and "Reading a PDF where
// it is"): `{vault, reader}`. `vault` is the synced vault that keeps the page
// (`{id, name, scope, path, pull, push, problem, conflicts, paused,
// work_folder, choosing, mine, agent}`, `mine` whether this account made it,
// `choosing` a work folder still choosing its first files, `agent` a work
// folder's agent on this computer or null), `reader`
// the file the page reads where it lies (`{path, missing, mine, format,
// ext, relocation_error?}`, `mine` whether this account opened the file,
// `format` and `ext` its document's: a PDF, or any other format Gamma
// shows, which cannot do all a PDF can, opensAsPdf and openedNote).
// What the page shows of it: the chip in the page header and its card
// (VaultChip.jsx), the banner over the page (its VaultBanner), whether the
// notes are locked, and the conflict route the merge chips read. A path is
// "" for a member who cannot act on the vault: where its files lie is the
// reading of whoever made it. On any other server the page's chip names
// the computer that keeps it instead (computerChip, read by
// usePageComputer.js), and a work folder's agent there (vaultAgent, which
// the chat's and the comments' agents read: chat/agents.js keeperAgent).
// Pure: tests/vaultState.test.mjs.
import { T, fmtDate, t, tn } from "../shared/i18n/i18n.js";
import { inPlace, isPdf } from "../documents/pageDocument.js";

// The answer, kept only when it says something: null for a page of no vault
// that reads no file here.
export function readVault(answer) {
  const vault = answer?.vault && typeof answer.vault === "object" ? answer.vault : null;
  const reader = answer?.reader && typeof answer.reader === "object" ? answer.reader : null;
  return vault || reader ? { vault, reader } : null;
}

// The last name of a path, with either separator.
export function baseName(path) {
  return String(path || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "";
}

// A vault's direction in its two switches' words (`push` is the API's
// "off" | "pdfs" | "everything").
export function directionText(vault) {
  const push = vault?.push === "everything" ? t("Push: everything") : vault?.push === "pdfs" ? t("Push: PDFs") : t("Push: off");
  return `${vault?.pull ? t("Pull: on") : t("Pull: off")} · ${push}`;
}

// The chip in the page header: `{kind, label, title}`, or null for a page
// that has nothing to do with this computer.
// `kind` is what the header draws, one icon for how the page stands:
// "sync" a folder vault keeps it in step, "push" its PDF comes from a file
// of its own, "disk" it is a PDF read where it lies, "paused" its vault is
// paused, "alert" the vault could not write or the file is gone. The label
// says the same in words, for the tooltip, the screen reader and the card;
// a folder vault's page says where it is synced, a vault of one file where
// its PDF is pushed from (a file vault only pushes), an opened file that it
// is on this computer, in a PDF's words or any other file's.
export function vaultChip(state) {
  if (!state) return null;
  const { vault, reader } = state;
  if (vault) {
    const name = baseName(vault.path) || vault.name || "";
    const file = vault.scope === "file";
    const label = file ? t("Pushed from: {name}", { name }) : t("Synced: {name}", { name });
    const where = vault.path || vault.name || "";
    const lines = [file ? t("This page's PDF is pushed from {path}", { path: where }) : t("This page is synced with {path}", { path: where })];
    if (vault.paused) lines.push(t("The vault is paused."));
    if (vault.conflicts) lines.push(tn("{n} conflict", "{n} conflicts", vault.conflicts));
    if (vault.problem) lines.push(t("Not written to disk: {reason}", { reason: vault.problem }));
    const kind = vault.problem ? "alert" : vault.paused ? "paused" : file ? "push" : "sync";
    return { kind, label, title: lines.join("\n") };
  }
  const missing = !!reader.missing;
  const pdf = opensAsPdf(reader);
  const title = pdf ? (missing ? t("The file this PDF was read from is gone")
    : reader.path ? t("This PDF is read from {path}, not stored in the library", { path: reader.path })
    : t("This PDF is read from a file on this computer, not stored in the library"))
    : missing ? t("The file this page was read from is gone")
    : reader.path ? t("This file is read from {path}, not stored in the library", { path: reader.path })
    : t("This file is read where it lies on this computer, not stored in the library");
  return { kind: missing ? "alert" : "disk", label: missing ? t("File missing") : t("On this computer"), title };
}

// An agent at work, in the one wording every place that shows it uses (the
// chip's card, the chat's strip and pill, a Send's tag; the desktop sync
// panel copies it): "Codex is working…", with what it is at "Codex is
// working: Editing sections/intro.tex"; "The agent is working…" unnamed.
export function agentWorking(agent, detail = "") {
  if (!agent) return t("The agent is working…");
  return detail ? t("{agent} is working: {detail}", { agent, detail }) : t("{agent} is working…", { agent });
}

// What the card says of the agent on this computer that answers the work
// folder's Sends (`vault.agent`, {name, label, ready, problem, running:
// {kind, request_id, detail} | null, `kind` "send" or "chat"};
// docs/dev/local_agents.md): {line, problem,
// working}, `line` naming it, `problem` why it cannot run (the server's
// English sentence, "" while ready or not known yet), `working` during a
// run the StateTag's state of it at work ({label, detail, title, tone,
// live}, as comments.js sendState's running: `detail` what it is at, "" for
// a member who cannot act on the vault), null otherwise. Null for a vault
// with no agent.
export function agentLine(state) {
  const agent = state?.vault?.agent;
  if (!agent || typeof agent !== "object") return null;
  const name = agent.label || agent.name || "";
  const detail = agent.running?.detail || "";
  const label = agentWorking(name);
  const working = agent.running ? { label, detail, title: detail || label, tone: "pending", live: true } : null;
  return {
    line: t("Agent: {agent} on this computer", { agent: name }),
    problem: agent.ready === false ? String(agent.problem || "") : "",
    working,
  };
}

// The computers that keep the page in a synced vault of the account's, as
// the page's server knows them (GET /api/pages/{id}/computer,
// docs/dev/computers.md): each announces itself to that server over its
// own channel, `[{id, name, online, since, vault: {kind, name, paused,
// agent}, assistants: [{name, label, found, ready, version, code}]}]`,
// online ones first; `vault.agent` ({name, label} | null) the agent that
// answers the Sends of a work folder of that server there. Only the
// vault's maker is told: `[]` for anyone else, for a server older than the
// route and for an answer that says nothing.
export function readComputers(answer) {
  const list = Array.isArray(answer?.computers) ? answer.computers : [];
  return list.filter((c) => c && typeof c === "object" && typeof c.name === "string" && c.name);
}

// When a computer went offline, as the chip says it: "2:02 PM" today, "Nov 3"
// another day this year, with the year before it, in the interface's
// language ("" for no time known).
export function sinceText(since, now = new Date()) {
  const d = new Date(since);
  if (!since || Number.isNaN(d.getTime())) return "";
  if (d.toDateString() === now.toDateString()) return fmtDate(d, { hour: "numeric", minute: "2-digit" });
  return fmtDate(d, d.getFullYear() === now.getFullYear() ? { month: "short", day: "numeric" } : { month: "short", day: "numeric", year: "numeric" });
}

const ASSISTANTS = { codex: T("Codex"), claude: T("Claude Code") };

// An assistant a computer has, by its announced code (never the computer's
// English problem): "Codex", "Codex (not signed in)", "Claude Code
// (checking)", "Codex (not ready)".
function assistantText(assistant) {
  const name = String(assistant.label || (ASSISTANTS[assistant.name] ? t(ASSISTANTS[assistant.name]) : assistant.name) || "");
  if (assistant.code === "signed_out") return t("{agent} (not signed in)", { agent: name });
  if (assistant.code === "checking" || assistant.ready == null) return t("{agent} (checking)", { agent: name });
  if (assistant.code === "not_ready" || assistant.ready === false) return t("{agent} (not ready)", { agent: name });
  return name;
}

// The chip of a page another computer keeps (readComputers' first), drawn
// where vaultChip has nothing to say (any server but this computer's own):
// {online, label, title, state, vault, paused, assistants}. `label` names
// the computer ("Synced vault on TIM-PC"); `state` is "Connected" or when it
// went offline; `vault` the folder or file it keeps, as the page's server
// names it; `paused` whether its vault is paused there ("" if not);
// `assistants` the assistants found there, a not-ready one saying why (""
// for none); `agent` the agent there that runs the Sends of the work
// folder it keeps ("Agent: Codex on TIM-PC", "" for none); `title` the
// lines after the label, for the tooltip. Null for a page no computer of
// the account's keeps.
export function computerChip(computers, now = new Date()) {
  const computer = readComputers({ computers })[0];
  if (!computer) return null;
  const name = computer.name;
  const online = computer.online === true;
  const when = online ? "" : sinceText(computer.since, now);
  const state = online ? t("Connected") : when ? t("Offline since {when}", { when }) : t("Offline");
  const vault = computer.vault && typeof computer.vault === "object" ? computer.vault : {};
  const kept = !vault.name ? "" : vault.kind === "file" ? t("File: {name}", { name: vault.name }) : t("Folder: {name}", { name: vault.name });
  const paused = vault.paused ? t("Paused on {computer}", { computer: name }) : "";
  const found = (Array.isArray(computer.assistants) ? computer.assistants : [])
    .filter((a) => a && typeof a === "object" && a.found !== false && a.code !== "missing"); // found: null while checking
  const assistants = found.length ? t("Assistants there: {names}", { names: found.map(assistantText).join(", ") }) : "";
  const answers = vaultAgent(computer);
  const agent = answers ? t("Agent: {agent} on {computer}", { agent: answers.label, computer: name }) : "";
  return {
    online, label: t("Synced vault on {computer}", { computer: name }), state, vault: kept, paused, assistants, agent,
    title: [state, kept, paused, assistants, agent].filter(Boolean).join("\n"),
  };
}

// A computer's agent on the work folder it keeps of the page's server
// (`vault.agent`), {name, label}, or null for none: the chip's, and the
// chat's and the comments' agent there (chat/agents.js keeperAgent).
export function vaultAgent(computer) {
  const agent = computer?.vault?.agent;
  if (!agent || typeof agent !== "object" || typeof agent.name !== "string" || !agent.name) return null;
  return { name: agent.name, label: String(agent.label || (ASSISTANTS[agent.name] ? t(ASSISTANTS[agent.name]) : agent.name)) };
}

// The banner over the page, in the missing-page notice's slot: a note the
// vault could not write (`problem`, why), or an opened file whose file is
// gone (`missing`, with Locate… and Remove). Null for neither.
export function vaultBanner(state) {
  if (state?.vault?.problem) return { kind: "problem", reason: String(state.vault.problem) };
  if (state?.reader?.missing) return { kind: "missing", path: state.reader.path || "" };
  return null;
}

// What a dismissed banner is remembered by: the page and what the banner
// says, so it shows again once it says something else ("" for none).
export function bannerKey(pageId, banner) {
  return banner ? `${pageId}:${banner.kind}:${banner.reason || banner.path}` : "";
}

// Whether the page's notes are locked: a folder vault that does not pull
// writes nothing of Gamma's to its files, so where it pushes everything a
// note edited here would part from the file, which is where the notes are
// written. Highlights and chat stay open (BlockTree's `notesLocked`). One
// that pushes only PDFs never reads a notes file into Gamma, and a vault of
// one file keeps only its PDF on disk: their notes are Gamma's. So are the
// notes of a page whose document is no PDF (`doc`, the page's
// pageDocument): a work folder's picture or text file has no notes file,
// the vault never writes one, and its notes stay open.
export function notesLocked(state, doc = null) {
  const vault = state?.vault;
  if (doc && !isPdf(doc)) return false;
  return !!vault && vault.scope !== "file" && !vault.pull && vault.push === "everything";
}

// Whether the page's notes file can be edited from here (FileSourceDialog):
// the vault pushes everything, so the file is what the page becomes, and the
// page has a notes file whose path this account may see.
export function editsFile(state) {
  const vault = state?.vault;
  return !!vault && vault.push === "everything" && /\.(md|markdown)$/i.test(vault.path || "");
}

// Whether Add to library… takes the page in as an opened file (a work
// folder's file is fileRules' addToLibrary, explorer/workFolders.js).
export function addsToLibrary(state) {
  return !!state?.reader && !state.vault;
}

// Whether this account opened the page's file, as far as the page knows:
// false only when the server says another account did. Keeping on pushing
// from the file and taking the page to another workspace or server are the
// opener's alone (the file is on that account's computer); the server and the
// desktop app refuse them to anyone else too.
export function openedHere(state) {
  return state?.reader?.mine !== false;
}

// Whether a document (a page's pageDocument, a library row's `_doc`, or
// the open page's `reader`, which carries its document's format and ext)
// is a PDF: an opened file of any other format neither keeps pushing from
// its file nor is carried to another server (linked_files.add_to_library
// and the carry routes refuse it).
export const opensAsPdf = (doc) => doc?.format === "pdf";

// Whether a document is a Markdown file read as a text document: an opened
// lone .md, shown read-only in the code view, which never joins the library
// (every .md there is a note; linked_files.add_to_library refuses it). To
// edit it as a note, its folder becomes a work folder (Open with Gamma's
// Edit in Gamma); dropped on the library, it is imported as a note.
export const openedNote = (doc) => doc?.format === "text" && /^\.(md|markdown)$/i.test(doc.ext || "");

// Whether the page's PDF can be carried to another server's library (the
// desktop app's `carry-to-server`): an opened PDF whose file is there to read,
// opened by this account.
export function carriesToServer(state) {
  return addsToLibrary(state) && opensAsPdf(state.reader) && !state.reader.missing && openedHere(state);
}

// Where the page's vault keeps its conflicts (the merge chips' read and
// resolve), or "" for a page with no vault that has them: under
// /api/folder-links/{id}.
export function vaultConflictsPath(state) {
  const vault = state?.vault;
  return vault?.id && vault.scope !== "file" ? `/folder-links/${encodeURIComponent(vault.id)}` : "";
}

// Whether a library row (App's pageBlocks: `_doc`, `_parentPage`, `_opened`)
// is a file opened from this computer, of any format, one whose document is
// read in place, inside *Opened files*: the page Move to library and Move
// into page… take to Add to library… instead, since its bytes must be
// stored before it leaves.
export function isOpenedFile(id, pagesById) {
  const row = pagesById?.get(id);
  return inPlace(row?._doc) && !!row._parentPage && !!pagesById.get(row._parentPage)?._opened;
}

// The workspaces Add to library… offers (`workspaces`: the session's list,
// as the workspace menu reads it): the one open here, then each other this
// account edits on this server, a clone left out (it keeps its origin's
// pages; linked_files.add_to_library refuses it too). `[{id, name}]`.
export function libraryWorkspaces(workspaces, current) {
  const list = workspaces || [];
  const writes = (w) => (w.role === "owner" || w.role === "editor") && !w.mirror_of;
  const here = list.find((w) => w.id === current);
  return [...(here ? [here] : []), ...list.filter((w) => w.id !== current && writes(w))]
    .map((w) => ({ id: w.id, name: w.name || w.id }));
}
