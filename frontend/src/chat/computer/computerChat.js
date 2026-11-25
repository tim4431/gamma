// The chat's computer side, its pure part (docs/dev/local_agents.md "The
// chat's computer side"): what the side posts to /api/local-agents/chat…,
// its words for the strip, a turn, its steps and what it changed, and the
// conversation in the shape the chat's transcript draws Gamma AI's in
// (`shownMessages`, chat/ChatTranscript.jsx). The server keeps the
// conversation and writes the agent's message as the turn goes; it stores
// the steps as data ({kind, path, program, tool, status}, `tool` the name
// of the Gamma tool a step of kind "gamma" called), so they are worded
// here, in the reader's language. Node tests it (tests/computerChat.test.mjs).
//
// A person's message is {id, role: "user", text, selections: [{text,
// page}], at}; the agent's {id, role: "agent", agent, label, turn_id,
// state: "queued" | "running" | "done" | "failed" | "stopped", text, steps,
// changes: [path], changed: n, built, error, fresh, model, effort, at,
// ended_at}, `model` and `effort` what the turn ran with, `threads` the
// comment threads its agent opened in its turn [{id, page_id, block_id,
// text}], `changes`
// the folder's relative paths it named (at most 200), `changed` how many
// files changed in all, `error` the server's English sentence, `fresh`
// when a follow-up could not resume the agent's session. While the folder
// lets its agent ask (the vault's agent `runs: "ask"`), a running turn's
// message also has `asks`, what the agent waits on the person for:
// [{id, kind: "command" | "files" | "tool", command, cwd, paths, diff,
// reason, host, at}], `cwd` and `paths` relative to the folder, "outside"
// or "~", `diff` a unified diff, `host` the network address a command
// reaches; a command's `command_shown` is it with every character that
// would not show written ⟨U+202E⟩ (`marked` how many), `lines` its line
// count when over one, and `inner` with `shell` the command one shell
// wrapper runs, when the server could read it back whole; `once` on each
// ask of a co-author's turn (allowed once at most: no Allow in this chat).
// A step asked about reads "asked", then "allowed" or "declined".
//
// With another computer's agent (docs/dev/local_agents.md "The chat on
// another server's page") the agent runs on that computer, not on the
// page's server: the server's `computer` then says `remote: true` with the
// computer's name (`computer`), whether it is connected (`online`), since
// when it is not (`since`) and the directories it offers (`dirs`, [{id,
// name}]). The words name that computer, no message goes while it is away,
// and what needs the window to be that computer (the strip's menu, the
// passages' source lines) is left out. What the agent asks there is
// answered on that computer, in its desktop app's window ("Asking on this
// computer"); where that computer lets this server answer it ("Relayed"),
// the message carries it in `asks` as this computer's are, each `once`, and
// the page draws its card; otherwise the message says only `waiting: true`
// while one waits on that computer alone.
import { T, t } from "../../shared/i18n/i18n.js";
import { agentWorking, sinceText } from "../../vault/vaultState.js";
import { GAMMA, agentChoice, agentLabel } from "../agents.js";
import { stepsSummary, toolWords } from "../agentSteps.js";

// How long a message may be (the server's cap).
export const TEXT_MAX = 20000;
// How long the note of a decline may be (the server's cap).
export const NOTE_MAX = 2000;
// How often the side reads the conversation again while a turn is queued
// or running, ms; while it waits for another computer that is away, less
// often (it may stay away for hours).
export const POLL_MS = 1500;
export const AWAY_POLL_MS = 10_000;

// Whether the agent is another computer's (the server's `computer.remote`,
// or the page's computers until it is read: chat/agents.js stripFacts).
export const isRemote = (info) => info?.remote === true;

// The other computer's name while it is away ("" while it is connected, or
// the agent is this computer's): no message goes to it.
export const awayFrom = (info) => (isRemote(info) && info.online === false ? String(info.computer || "") : "");

// Whether the strip offers its menu (Continue in a terminal, Show in
// folder): only for the agent on this computer, as both need the window to
// be the computer the folder is on.
export const offersMenu = (info) => !!info && !isRemote(info);

// Whether the strip's menu offers Show in folder: on a page (`page`) of a
// work folder kept here (the server's `dir` a vault's), whose file the
// shell shows.
export const revealsFile = (info, page) => !!page && offersMenu(info) && info.dir?.kind === "vault";

// Whether another computer's turn (its agent's message) waits there for
// the person's answer: what it asks is answered on that computer alone
// (one that relays its asks to this server puts them in the message's
// `asks`, whose cards the page draws as this computer's).
export const waitsThere = (info, message) => isRemote(info) && message?.waiting === true;

// The path of /api/local-agents/chat for the bucket (a page's id, a
// folder's, or "home"), with the version of the conversation the side
// holds (the server then answers `same` while it is still that one), the
// agent picked (the server shows no conversation of another agent) and the
// conversation it shows in place of the bucket's own (`shown`, chatSide.js
// useChatShown: {thread} a comment thread's, {conversation} a co-author's;
// null for none).
export function chatPath(bucket, version = null, agent = "", shown = null) {
  const query = new URLSearchParams({ bucket });
  if (version != null) query.set("version", String(version));
  if (agent) query.set("agent", agent);
  if (shown?.thread) query.set("thread", shown.thread);
  else if (shown?.conversation) query.set("conversation", shown.conversation);
  return `/local-agents/chat?${query}`;
}

// The PDF passages selected (App's pdfSelections, {text, page, box}) as the
// server takes them, whose box lets it find their source line.
const passages = (selections) =>
  (selections || []).filter((s) => s?.text).map((s) => ({ text: s.text, page: s.page || null, box: s.box || null }));

// What a message posts (POST /api/local-agents/chat/turns): the id the
// client minted (the same on a retry, so the turn is made once), the
// bucket, the text trimmed, the PDF passages selected, and what was chosen
// for the conversation (`choice`: {agent, model, effort, speed, dir}): the
// agent picked, the model, effort and speed, none sent when it is the
// agent's default, and where a conversation that begins works (`dir`: an
// offered directory's id, "" for one of its own; null, not sent, for the
// conversation's own), and the skill the message runs (`skill`: {id, name,
// args}, chat/skills.js splitSkill; its text is then the arguments).
export function turnBody(id, bucket, text, selections = [], choice = null, skill = null) {
  return {
    id, bucket, text: String(text || "").trim(), selections: passages(selections),
    ...(skill ? { skill } : {}),
    ...(choice?.agent ? { agent: choice.agent } : {}),
    ...(choice?.model ? { model: choice.model } : {}), ...(choice?.effort ? { effort: choice.effort } : {}),
    ...(choice?.speed ? { speed: choice.speed } : {}),
    ...(choice?.dir != null ? { dir: choice.dir } : {}),
  };
}

// How many passages one ask for their source lines may carry (the
// server's cap).
export const SOURCES_MAX = 20;

// What the passage chips post to learn their source lines (POST
// /api/local-agents/chat/sources); the answer's `sources` are in the same
// order, each {file, line, end?} or null.
export const sourcesBody = (bucket, selections) => ({ bucket, selections: passages(selections).slice(0, SOURCES_MAX) });

// A passage's source line as its chip notes it ("sections/method.tex:3"),
// "" while it is not known or there is none.
export function sourceNote(source) {
  if (!source?.file) return "";
  return source.line ? `${source.file}:${source.line}` : source.file;
}

// When the conversation's newest ended turn ended (its agent message's
// `ended_at`), null before any: the passages' source lines are asked
// again after it, since the agent may have moved them.
// How the conversation's newest turn stands (queued, running, done,
// failed, stopped), "" before its first.
export function lastTurnState(conversation) {
  return (conversation?.messages || []).findLast((m) => m.role === "agent")?.state || "";
}

export function lastEnded(conversation) {
  return (conversation?.messages || []).findLast((m) => m.role === "agent" && m.ended_at)?.ended_at || null;
}

// The model, effort and speed a conversation goes on with: the person's
// choice for it in this tab (`picked`), else what its newest turn ran with
// (the agent's message keeps `model`, `effort` and `speed`, null for the
// default; one from before it kept them says nothing), each kept only
// while the agent still lists it, but for Flex, which the chat no longer
// picks; "" is the agent's default. `shown` is the model the chip names:
// the one chosen, else the agent's default, else its first.
export function modelChoice(models, conversation = null, picked = null) {
  const listed = models || [];
  const ran = (conversation?.messages || []).findLast((m) => m.role === "agent"
    && ("model" in m || "effort" in m || "speed" in m));
  const said = picked || (ran ? { model: ran.model || "", effort: ran.effort || "", speed: ran.speed || "" } : null);
  const model = listed.some((m) => m.id === said?.model) ? said.model : "";
  const shown = listed.find((m) => m.id === model) || listed.find((m) => m.default) || listed[0] || null;
  const effort = said?.effort && (shown?.efforts || []).includes(said.effort) ? said.effort : "";
  const speed = said?.speed && said.speed !== "flex" && (shown?.speeds || []).includes(said.speed) ? said.speed : "";
  return { model, effort, speed, shown };
}

// Whether the strip's menu offers Continue in a terminal: the server says
// the conversation has a session a terminal can continue (`info.session`),
// and no turn is still to end (it refuses one while it is). The shell asks
// the server for the session itself and says why when there is none.
export function canContinue(info, conversation) {
  if (!info?.agent || info.turn || liveTurn(conversation)) return false;
  return info.session === true;
}

// The path an answer to an ask posts to, and what it posts: the decision
// ("once", "chat" or "deny") and, for a decline, the note when one was
// written.
export const askPath = (turnId, askId) =>
  `/local-agents/chat/turns/${encodeURIComponent(turnId)}/asks/${encodeURIComponent(askId)}`;

export function answerBody(decision, note = "") {
  const said = decision === "deny" ? String(note || "").trim().slice(0, NOTE_MAX) : "";
  return said ? { decision, note: said } : { decision };
}

// Whether a message is a turn still to end: queued or running.
export const isLive = (message) => message?.role === "agent" && (message.state === "queued" || message.state === "running");

// The conversation's turn still to end, its agent's message, else null.
export function liveTurn(conversation) {
  const last = (conversation?.messages || []).findLast((m) => m.role === "agent");
  return isLive(last) ? last : null;
}

// What a step's place reads as: the folder's relative path, or a file
// outside the folder ("outside", as the server records it), "" for none.
function placeOf(step) {
  if (step?.path === "outside") return t("a file outside the folder");
  return step?.path || "";
}

// A step asked about, in words: "Asked to run latexmk", then "Allowed to
// run latexmk" or "Not allowed to run latexmk".
function askedStep(step, path) {
  const run = step.kind === "run";
  const edit = step.kind === "edit";
  const program = step.program || "";
  switch (step.status) {
    case "allowed":
      return run ? (program ? t("Allowed to run {program}", { program }) : t("Allowed to run a command"))
        : edit ? (path ? t("Allowed to edit {path}", { path }) : t("Allowed to edit a file")) : t("Allowed");
    case "declined":
      return run ? (program ? t("Not allowed to run {program}", { program }) : t("Not allowed to run a command"))
        : edit ? (path ? t("Not allowed to edit {path}", { path }) : t("Not allowed to edit a file")) : t("Not allowed");
    default:
      return run ? (program ? t("Asked to run {program}", { program }) : t("Asked to run a command"))
        : edit ? (path ? t("Asked to edit {path}", { path }) : t("Asked to edit a file")) : t("Asked to go further");
  }
}

const ASKED = new Set(["asked", "allowed", "declined"]);
// Gamma's tools that write as the agent (gamma/ai_tools/registry.py WRITING_TOOLS,
// gamma/local_agents/events.py GAMMA_WRITES): a call of one is worded as a
// write.
const GAMMA_WRITES = new Set(["comment", "resolve", "highlight"]);

// A step in words, done: "Read sections/intro.tex", "Edited …", "Ran
// latexmk", "Searched the sources", a call of Gamma's tools in the words
// Gamma AI's own call of it reads ("Listed the highlights and comments":
// agentSteps.js toolWords; a tool those do not name by its name, "Read
// Gamma: read_page"), "Worked"; one asked about says so.
export function stepText(step) {
  const path = placeOf(step);
  if (ASKED.has(step?.status)) return askedStep(step, path);
  switch (step?.kind) {
    case "read": return path ? t("Read {path}", { path }) : t("Read a file");
    case "edit": return path ? t("Edited {path}", { path }) : t("Edited a file");
    case "run": return step.program ? t("Ran {program}", { program: step.program }) : t("Ran a command");
    case "search": return t("Searched the sources");
    case "gamma":
      if (!step.tool) return t("Read Gamma");
      return toolWords(step.tool) || (GAMMA_WRITES.has(step.tool) ? t("Wrote in Gamma: {tool}", { tool: step.tool })
        : t("Read Gamma: {tool}", { tool: step.tool }));
    default: return t("Worked");
  }
}

// A step in words while it runs ("Editing sections/intro.tex", "Listing
// the highlights and comments…": Gamma AI's runningLabel for a call of
// Gamma's tools), what the strip and the turn's pill say the agent is at;
// "" for a step of no kind worth naming.
export function stepNow(step) {
  const path = placeOf(step);
  switch (step?.kind) {
    case "read": return path ? t("Reading {path}", { path }) : t("Reading a file");
    case "edit": return path ? t("Editing {path}", { path }) : t("Editing a file");
    case "run": return step.program ? t("Running {program}", { program: step.program }) : t("Running a command");
    case "search": return t("Searching the sources");
    case "gamma":
      if (!step.tool) return t("Reading Gamma");
      return toolWords(step.tool, true) || (GAMMA_WRITES.has(step.tool) ? t("Writing in Gamma: {tool}", { tool: step.tool })
        : t("Reading Gamma: {tool}", { tool: step.tool }));
    default: return "";
  }
}

// The step a running turn is at: its newest still started, else its newest.
export function currentStep(message) {
  const steps = message?.steps || [];
  return steps.findLast((s) => s.status === "started") || steps.at(-1) || null;
}

// What a turn's pill says: while it runs the agent at work ("Codex is
// working: Editing sections/intro.tex", or "Codex is working…" before its
// first step); while it is queued that it starts ("Starting Codex…"), that
// it waits behind other work in its directory (`ahead`, the server's
// `computer.turn.ahead`: "Waiting for Codex to finish its other work…"),
// or for another computer that is away (`away`, its name: awayFrom)
// "Waiting for TIM-PC"; while another
// computer's turn (`computer`, its name) waits there alone for the
// person's answer "Codex is waiting for your answer on TIM-PC", or that it
// is away (one whose asks it relays here is waiting for you, as this
// computer's);
// once it ended, Gamma AI's summary of its steps (agentSteps.js
// stepsSummary of its chips: "3 steps · read 2 files, edited 1 file").
export function turnLabel(message, label, away = "", computer = "", ahead = false) {
  const agent = message?.label || label;
  if (isLive(message) && message.asks?.length) return t("{agent} is waiting for you", { agent });
  if (isLive(message) && message.waiting === true && (away || computer)) {
    return away ? t("Waiting for your answer on {computer}, which is offline", { computer: away })
      : t("{agent} is waiting for your answer on {computer}", { agent, computer });
  }
  if (away && message?.role === "agent" && message.state === "queued") return t("Waiting for {computer}", { computer: away });
  if (isLive(message) && message.state === "queued") {
    return ahead ? t("Waiting for {agent} to finish its other work…", { agent }) : t("Starting {agent}…", { agent });
  }
  if (isLive(message)) return agentWorking(agent, stepNow(currentStep(message)));
  return stepsSummary((message?.steps || []).map(stepChip));
}

// How many changed files a turn's card lists before "and N more".
export const CHANGES_SHOWN = 3;

// What a turn changed, as the card under its reply lists it: {shown, rest,
// more, n, built}, `shown` the first paths it named, `rest` the others it
// named (folded under "and N more"), `more` how many files that line
// counts (named or not), `n` how many changed in all, `built` whether the
// PDF was rebuilt; null for nothing.
export function turnChanges(message) {
  const named = message?.changes || [];
  const n = Math.max(message?.changed || 0, named.length);
  if (!n && !message?.built) return null;
  const shown = named.slice(0, CHANGES_SHOWN);
  return { shown, rest: named.slice(CHANGES_SHOWN), more: n - shown.length, n, built: !!message?.built };
}

// The person's message a failed turn answered, what Retry sends again
// ({text, selections, skill}: `skill` null for a message that ran none);
// null when there is none or the turn is not the conversation's newest.
export function retryOf(conversation, message) {
  const messages = conversation?.messages || [];
  const at = messages.indexOf(message);
  const asked = at > 0 ? messages[at - 1] : null;
  if (message?.state !== "failed" || at !== messages.length - 1 || asked?.role !== "user" || !(asked.text || asked.skill)) return null;
  return { text: asked.text || "", selections: asked.selections || [], skill: asked.skill || null };
}

// How a turn ended, when it did not answer: the reason it failed (the
// server's sentence) or that it was stopped; "" otherwise.
export function endText(message) {
  if (message?.state === "failed") return message.error || t("Failed");
  if (message?.state === "stopped") return t("Stopped");
  return "";
}

// The quiet line of a turn that could not resume the agent's session.
export const freshLine = (label) =>
  t("{agent} started a new session: it does not remember the earlier turns of this conversation.", { agent: label });

// What an ask's card is headed: "Codex wants to run a command".
export function askTitle(ask, agent) {
  if (ask?.kind === "command") return t("{agent} wants to run a command", { agent });
  if (ask?.kind === "files") return t("{agent} wants to change files outside its sandbox", { agent });
  return t("{agent} wants to use a tool", { agent });
}

// Where an asked command runs: its relative path in the directory the
// agent works in, a folder outside it, or the home folder ("~"); that
// directory itself for "" or ".".
export function askPlace(cwd) {
  if (!cwd || cwd === ".") return t("In the directory it works in");
  if (cwd === "outside") return t("In a folder outside the directory it works in");
  if (cwd === "~") return t("In your home folder");
  return t("In {path}", { path: cwd });
}

// An asked command as its card shows it: `main` in the box (the command one
// shell wrapper runs when the server read it back whole, else the command
// with what would not show marked), `through` the note under it naming
// that wrapper ("" for none), `lines` its line count over one (else 0).
export function askCommand(ask) {
  const whole = String(ask?.command_shown || ask?.command || "");
  const inner = ask?.inner && ask?.shell ? String(ask.inner) : "";
  return {
    main: inner || whole,
    through: inner ? t("Through {shell}: {command}", { shell: String(ask.shell), command: whole }) : "",
    lines: ask?.lines > 1 ? ask.lines : 0,
  };
}

// A shown command in pieces to draw: [{text, mark}], `mark` for a
// character the server wrote ⟨U+202E⟩ because it would not show. Only for
// an ask that says it holds some (`marked`): the agent may write the same
// letters itself.
const MARK = /(⟨U\+[0-9A-F]{4,6}⟩)/;
export function markParts(text, marked) {
  const s = String(text || "");
  if (!marked) return [{ text: s, mark: false }];
  return s.split(MARK).filter(Boolean).map((piece) => ({ text: piece, mark: MARK.test(piece) }));
}

// Whether a box of an ask was read to its end: scrolled there, or nothing
// to scroll. Allow waits for it.
export const scrolledToEnd = (el) => !el || el.scrollTop + el.clientHeight >= el.scrollHeight - 2;

// The line under an ask's buttons: what allowing it means.
export function askRisk(ask) {
  if (ask?.kind === "command") return t("It runs as you, with your files and the network.");
  if (ask?.kind === "files") return t("It changes files outside the directory it works in.");
  return "";
}

// A unified diff as rows to draw: [{kind, text}], `kind` "del" or "ins" for
// a line taken out or put in, "head" for the file and hunk lines, "same"
// for the context.
export function diffRows(diff) {
  if (!diff) return [];
  return String(diff).replace(/\n$/, "").split("\n").map((text) => {
    if (/^(---|\+\+\+|@@|diff |index )/.test(text)) return { kind: "head", text };
    if (text.startsWith("-")) return { kind: "del", text };
    if (text.startsWith("+")) return { kind: "ins", text };
    return { kind: "same", text };
  });
}

// Where the conversation's agent works (the server's `computer.dir`: a
// vault's, already named in the strip's line as the folder; {kind:
// "offered", id, name}; null for a directory of its own): null for a
// vault's and a co-author's (a synced folder's alone); else {pick, value,
// options, text}, `options` "Its own directory" then the directories
// offered (this computer's `dirs`, GET /api/local-agents' [{id, path,
// name}], or another computer's, the server's `computer.dirs`) as [value,
// name], those used last first (`used`: the ids conversations of this
// browser began in, the newest first, "" for a directory of its own;
// chat/computer/usedDirs.js), `value` the one it works in ("" its own),
// `text` its name, which the composer's folder chip shows. While the
// conversation has no turn it is still to choose (`pick`): `value` the one
// `picked` for it while that is offered, else the one used last that
// still is, as a new Codex thread starts in the project used last, else
// ""; once it has one it is the conversation's, kept among the options
// when no longer offered.
export function workPlace(info, conversation, dirs = [], picked = null, used = []) {
  if (!info || info.dir?.kind === "vault" || info.lent) return null;
  const offered = ((isRemote(info) ? info.dirs : dirs) || []).filter((d) => d && d.id);
  const rank = (id) => (used.includes(id) ? used.indexOf(id) : used.length);
  const ordered = [...offered].sort((a, b) => rank(a.id) - rank(b.id));
  const options = [["", t("Its own directory")], ...ordered.map((d) => [d.id, String(d.name || d.path || d.id)])];
  const pick = !conversation?.messages?.length;
  const listed = (id) => options.some(([v]) => v === id);
  const kept = info.dir?.kind === "offered" ? String(info.dir.id || "") : "";
  const value = !pick ? kept : picked != null && listed(picked) ? picked : used.find(listed) ?? "";
  const known = options.find(([v]) => v === value);
  const text = known ? known[1] : String(info.dir.name || "");
  return { pick, value, options: known ? options : [...options, [value, text]], text };
}

// The agent picker's hint on an agent of its menu that cannot answer now
// (chat/AgentPicker.jsx): {text, problem, title}, "offline" for another
// computer that is away, "not ready" (amber: `problem`) for an agent with a
// problem, the whole of it as the row's title; null for one that can. The
// row stays pickable: the chat then says why.
export function agentHint(agent) {
  if (agent?.remote && agent.online === false) {
    return { text: t("offline"), problem: false, title: t("{computer} is offline: send this once it connects.", { computer: agent.where }) };
  }
  if (agent?.ready === false) return { text: t("not ready"), problem: true, title: String(agent.problem || "") };
  return null;
}

// The directory whose sessions History lists under "Codex sessions in
// paper" (sessionsWords; the CLI's own, started in a terminal or an
// editor: GET /api/local-agents/sessions' `dir`), as the chat's picker
// names it: the work folder's id (`folder_id`) where the reader made its
// vault (the server's `dir` then names its `path` on this computer;
// another computer keeps the reader's own alone), an offered directory's
// id, the one picked for a conversation that begins; "" for none: a
// co-author's agent, a directory of its own (it holds Gamma's sessions
// alone), a vault another account made on this computer (its directory is
// its maker's).
export function sessionsDir(info, place = null) {
  if (!info || info.lent) return "";
  if (info.dir?.kind === "vault") return info.dir.path || isRemote(info) ? String(info.folder_id || "") : "";
  if (info.dir?.kind === "offered") return String(info.dir.id || "");
  return place?.pick ? String(place.value || "") : "";
}

// What History says of those sessions (`label` the agent's name, `place`
// workPlace's, `where` another computer's name, "" for this one's agent):
// {caption, empty}, the section's caption naming the CLI and the directory
// ("Codex sessions in paper", "… on TIM-PC" for another computer) and the
// line in place of its rows while it has none ("Sessions you start with
// Codex in paper show here."). The directory is the synced folder's, the
// offered one's or the one picked for a conversation that begins, by name.
export function sessionsWords(info, label, place = null, where = "") {
  const named = info?.dir?.kind === "vault" || info?.dir?.kind === "offered" ? info.dir.name || info.folder
    : place?.pick ? (place.options || []).find(([id]) => id === place.value)?.[1] : "";
  const words = { agent: label, dir: String(named || ""), computer: where };
  return {
    caption: where ? t("{agent} sessions in {dir} on {computer}", words) : t("{agent} sessions in {dir}", words),
    empty: t("Sessions you start with {agent} in {dir} show here.", words),
  };
}

// Why another computer lists no session (GET /api/local-agents/sessions'
// `away`), "" for none: offline, a Gamma there too old to be asked, or no
// answer yet (History opened again reads the one that came).
export function sessionsAway(away, computer) {
  if (away === "offline") return t("{computer} is offline: its sessions show once it connects.", { computer });
  if (away === "older") return t("{computer} needs a newer Gamma to list its sessions here.", { computer });
  if (away === "waiting") return t("{computer} has not answered yet.", { computer });
  return "";
}

// What a session was started from, as its History row says it (the
// server's `source`).
const SOURCE_WORDS = { cli: T("Terminal"), vscode: T("VS Code"), exec: T("Script"), app: T("Desktop app"), gamma: T("Gamma") };

// The sessions GET /api/local-agents/sessions lists ([{id, title, at,
// created, source, ours, busy}]) as History's rows (chat/ChatHistoryPop.jsx):
// {id, title, preview, updated_at, by, busy}, `by` "Continued here" for one
// a conversation of Gamma's continues, else what it was started from,
// `busy` why it cannot be opened now (another window of `label`'s has it
// open: the row says it under its title), "" while it can.
export function sessionRows(sessions, label) {
  return (sessions || []).map((s) => ({
    id: s.id, title: s.title || "", preview: "", updated_at: s.at || "",
    by: s.ours ? t("Continued here") : SOURCE_WORDS[s.source] ? t(SOURCE_WORDS[s.source]) : "",
    busy: s.busy ? t("{agent} has this session open in another window: close it there first.", { agent: label }) : "",
  }));
}

// The empty transcript's line: what the agent is asked about and where it
// works (`place` workPlace's, `where` another computer's name, `page`
// whether the bucket is a page, else a folder).
export function emptyLine(info, label, where = "", place = null, page = true) {
  const words = { agent: label, computer: where };
  if (info?.dir?.kind === "vault" && page) {
    return where ? t("Ask {agent} about this file or to change it: it works in the synced folder's directory on {computer}.", words)
      : t("Ask {agent} about this file or to change it: it works in the synced folder's directory on this computer.", words);
  }
  if (info?.dir?.kind === "vault") {
    return where
      ? t("Ask {agent} about this folder or its files, or to change them: it works in the synced folder's directory on {computer}.", words)
      : t("Ask {agent} about this folder or its files, or to change them: it works in the synced folder's directory on this computer.", words);
  }
  const offered = place && (place.pick ? place.value : info?.dir?.kind === "offered");
  if (offered) {
    return where ? t("Ask {agent} anything: it works in the directory you chose on {computer}.", words)
      : t("Ask {agent} anything: it works in the directory you chose on this computer.", words);
  }
  return where ? t("Ask {agent} anything: it works in an empty directory of its own on {computer}.", words)
    : t("Ask {agent} anything: it works in an empty directory of its own on this computer.", words);
}

// The strip under the header: {text, kind}, `kind` its look ("ready",
// "checking" with a spinner, "asking" with a shield; amber "problem",
// "paused" and "away"). `info` is
// the server's `computer` ({label, ready, problem, folder, paused, turn:
// {turn_id, state, ahead} | null}, and for another computer's agent
// {computer, online, since, remote: true}) or, until it is read, the
// picked agent's (chat/agents.js stripFacts); `live` the
// conversation's turn still to end, what it asks the strip names. While a
// turn is queued or runs the strip keeps its resting line: the turn's pill
// says how it goes (turnLabel). Another computer is named ("Codex on TIM-PC · paper"), and while
// it is away the strip says so and that a message goes once it is back
// ("TIM-PC is offline since 2:02 PM: send this once it connects."); a
// turn queued before it went away says it waits for it; one of it that
// waits for the person's answer there alone says where to answer
// ("Waiting for your answer on TIM-PC"), and that it is away when it is;
// what it relays here is waiting for you, as this computer's agent's is.
// A co-author's agent (`lent`: {by}) names its owner ("alice's Codex on
// TIM-PC").
export function stripLine(info, live = null, now = new Date()) {
  const agent = info?.label || "";
  const remote = isRemote(info);
  const computer = remote ? String(info.computer || "") : "";
  if (info?.paused) {
    return {
      text: remote ? t("The synced folder is paused on {computer}: {agent} starts once it syncs again.", { computer, agent })
        : t("The synced folder is paused on this computer: {agent} starts once it syncs again.", { agent }),
      kind: "paused",
    };
  }
  const away = awayFrom(info);
  const when = away ? sinceText(info.since, now) : "";
  if (live?.asks?.length) return { text: t("Waiting for you: {ask}", { ask: askTitle(live.asks[0], live.label || agent) }), kind: "asking" };
  if (isLive(live) && waitsThere(info, live)) {
    if (!away) return { text: t("Waiting for your answer on {computer}", { computer }), kind: "asking" };
    return {
      text: when ? t("Waiting for your answer on {computer}, offline since {when}", { computer: away, when })
        : t("Waiting for your answer on {computer}, which is offline", { computer: away }),
      kind: "away",
    };
  }
  const turn = info?.turn || (live ? { state: live.state, ahead: false } : null);
  if (away && turn?.state === "queued") {
    return {
      text: when ? t("Waiting for {computer}, offline since {when}: the message runs when it connects", { computer: away, when })
        : t("Waiting for {computer}, which is offline: the message runs when it connects", { computer: away }),
      kind: "away",
    };
  }
  if (away) {
    return {
      text: when ? t("{computer} is offline since {when}: send this once it connects.", { computer: away, when })
        : t("{computer} is offline: send this once it connects.", { computer: away }),
      kind: "away",
    };
  }
  if (info?.ready === false) return { text: String(info.problem || ""), kind: "problem" };
  if (info?.ready == null) return { text: t("Checking {agent}…", { agent }), kind: "checking" };
  const named = info.lent?.by ? t("{owner}'s {agent}", { owner: info.lent.by, agent }) : agent;
  const here = remote ? t("{agent} on {computer}", { agent: named, computer }) : t("{agent} on this computer", { agent: named });
  return { text: info.folder ? `${here} · ${info.folder}` : here, kind: "ready" };
}

// Whether a message can go now: something written or a skill to run
// (`skill`), no turn of the conversation still to end, and an agent that
// is not known to be unable to run (the server refuses the rest, with its
// reason). Another computer that is away takes none: the server queues
// nothing for it.
export function canSend(info, conversation, text, skill = null) {
  return (!!String(text || "").trim() || !!skill) && !liveTurn(conversation) && !info?.turn && !info?.paused
    && info?.ready !== false && !awayFrom(info);
}

// What a step's chip is among the turn's steps in the transcript (the
// chips of Gamma AI's tool calls, with no call to unfold): its words, its
// icon by a kind of the chat's own (chat/AgentChanges.jsx ACTION_ICONS: a
// file read, a file edited, a command, a search, other work; a call of
// Gamma's tools the kind Gamma AI's own call of that tool has), none of
// which is a change of the library or the notes (chat/agentSteps.js
// isChange), and whether it failed or was not allowed; a file's `path` and
// a command's `program`, which the ended turn's pill counts and names
// (chat/agentSteps.js stepsSummary: "read 2 files, edited 1 file, ran
// latexmk", a call of Gamma's tools counted as Gamma AI's reads are).
const STEP_KINDS = { read: "file", edit: "fileEdit", run: "run", search: "search" };
// A writing tool's call is a step of its own kind, "write": what it wrote is
// the agent's own, listed in the thread or on the page, never under the
// reply's changes (agentSteps.js CHANGE_KINDS has no such kind).
const GAMMA_KINDS = {
  list_pages: "list", list_folders: "list", list_annotations: "list", list_workspaces: "list",
  search_library: "search", view_pdf_page: "view", view_ink: "ink", view_image: "image", cite: "cite",
  comment: "write", resolve: "write", highlight: "write",
};
const chipKind = (step) => (step?.kind === "gamma" ? GAMMA_KINDS[step.tool] || "read" : STEP_KINDS[step?.kind] || "work");
export function stepChip(step) {
  return {
    kind: chipKind(step), summary: stepText(step),
    ...(step?.path ? { path: step.path } : {}), ...(step?.program ? { program: step.program } : {}),
    ...(step?.status === "failed" ? { error: true } : {}), ...(step?.status === "declined" ? { declined: true } : {}),
  };
}

// The conversation in the shape the chat's transcript draws Gamma AI's in
// (chat/ChatTranscript.jsx), `info` the strip's facts (the server's
// `computer`, else the picked agent's: chat/agents.js stripFacts). A
// person's message is {id, role: "user", text, passages: [{text, page}],
// skill?} (`skill` the {id, name, args} it ran, its chip);
// the agent's {id, role: "ai", turnId, label, text, partial, pill, actions,
// asks, files, notice, threads, model, effort}: `partial` while its turn
// is queued or running (the reply as it is typed), `pill` the steps pill's {label,
// waiting, queued} (turnLabel; `queued` while the turn waits its turn, drawn
// with a clock and no shimmer), `actions` its steps as chips (stepChip), `asks`
// what it waits on the person for, while it does (another computer's
// too, where that computer relays them), `files` what it changed (turnChanges, with `vault` while it
// works in a work folder's directory, whose paths a link opens), `notice`
// the line of a session not resumed, `threads` the comment threads it
// opened (its card lists them, chat/AgentChanges.jsx ThreadsOpened), `model`
// the model's name as the agent lists it. A stopped turn ends its text
// "(stopped)", as a Gamma AI reply stopped does; a failed one carries the
// error card's fields (chat/chatErrors.js, kind "agent": the computer's own
// sentence its headline),
// `error` only when it wrote nothing (with text, the card is compact).
// A turn the person steered messages into (`steers`, docs/dev/ai.md "Queue
// and Steer") is drawn as Gamma AI's reply is: its part before each, ended,
// then the person's message (`steered`), and the turn going on after it
// (steerParts).
const passagesOf = (selections) => (selections || []).filter((s) => s?.text).map((s) => ({ text: s.text, page: s.page || null }));
export function shownMessages(conversation, info) {
  const label = info?.label || "";
  const away = awayFrom(info);
  const computer = isRemote(info) ? String(info.computer || "") : "";
  const models = info?.models || [];
  const vault = info?.dir?.kind === "vault";
  return (conversation?.messages || []).flatMap((m) => {
    if (m.role === "user") {
      return [{ id: m.id, role: "user", text: m.text || "", passages: passagesOf(m.selections), ...(m.skill ? { skill: m.skill } : {}) }];
    }
    const live = isLive(m);
    const by = m.label || label;
    const model = models.find((x) => x.id === m.model)?.label || m.model || "";
    const parts = steerParts(m);
    const earlier = parts.slice(0, -1).flatMap((part, k) => [
      ...(part.text || part.steps.length ? [{
        id: `${m.id}:${k}`, role: "ai", turnId: m.turn_id, label: by, text: part.text, partial: false,
        pill: { label: turnLabel({ ...m, state: "done", steps: part.steps }, label), waiting: false, queued: false },
        actions: part.steps.map(stepChip), asks: [], files: null, notice: "", threads: [], model, effort: m.effort || "",
        speed: m.speed || "",
      }] : []),
      { id: `${m.id}:steer:${part.steer.id}`, role: "user", text: part.steer.text, steered: true,
        passages: passagesOf(part.steer.selections) },
    ]);
    const { text, steps } = parts.at(-1);
    const files = turnChanges(m);
    const waiting = live && (!!m.asks?.length || m.waiting === true);
    const ahead = !!m.turn_id && info?.turn?.turn_id === m.turn_id && info.turn.ahead === true;
    return [...earlier, {
      id: m.id, role: "ai", turnId: m.turn_id, label: by,
      text: m.state === "stopped" ? [text, `*(${t("stopped")})*`].filter(Boolean).join("\n\n") : text,
      partial: live,
      pill: { label: turnLabel({ ...m, steps }, label, away, computer, ahead), waiting, queued: live && !waiting && m.state === "queued" },
      actions: steps.map(stepChip),
      asks: live ? m.asks || [] : [],
      files: files ? { ...files, vault } : null,
      notice: m.fresh ? freshLine(by) : "", threads: m.threads || [],
      model, effort: m.effort || "", speed: m.speed || "",
      ...(m.state === "failed" ? { ...(text ? {} : { error: true }), errorKind: "agent", errorProvider: by, errorSaid: endText(m) } : {}),
    }];
  });
}

// An agent's message cut where messages were steered into its turn: [{text,
// steps, steer}], the reply's text and steps up to each steer's `place`
// (the length of the text then, how many steps had come) and that steer,
// the last part what came after the last one (`steer` null). A message
// with no steers is one part.
export function steerParts(m) {
  const text = m?.text || "";
  const steps = m?.steps || [];
  const parts = [];
  let at = 0, step = 0;
  for (const steer of m?.steers || []) {
    const end = Math.min(Math.max(steer.place?.text || 0, at), text.length);
    const upto = Math.min(Math.max(steer.place?.steps || 0, step), steps.length);
    parts.push({ text: text.slice(at, end).trim(), steps: steps.slice(step, upto), steer });
    at = end;
    step = upto;
  }
  parts.push({ text: text.slice(at).trim(), steps: steps.slice(step), steer: null });
  return parts;
}

// The turn a queued message's Steer hands it to (chat/chatQueue.js): the
// conversation's turn while its agent works (running, not waiting for its
// directory), "" for none.
export function steeringTurn(conversation) {
  const live = liveTurn(conversation);
  return live?.state === "running" ? live.turn_id || "" : "";
}

// Whether the message with id `id` was steered into one of the
// conversation's turns (its agent took it in).
export const steerTaken = (conversation, id) => (conversation?.messages || [])
  .some((m) => m.role === "agent" && (m.steers || []).some((s) => s.id === id));

// The rows of the chat's History (chat/ChatHistoryPop.jsx): every agent's
// conversations of the bucket together, the one shown first (`shown`,
// {title, preview} or null for none), then the others newest first: Gamma
// AI's archived ones (`gamma`, GET /api/chat-history's sessions) and its
// current one while another agent is shown (`gammaNow`, {title, preview,
// updated_at} or null), the computer agents' archived ones and their
// active one while it is not the one shown (`computer`, GET
// /api/local-agents/chat/history's {conversations, active, lent}; null
// unread), and the co-authors' chats with the reader's agents (`lent`,
// each with its `person`). `agent` is the agent shown, `shownId` the id of
// its conversation (a computer agent's). A row is {id, agent, title,
// preview, updated_at, active, current, lent, by} (Gamma AI's with its
// `count`): `agent` its agent's ("gamma", a computer agent's id, as the
// server names it: `agent_id`), `active` the one shown, `current` an
// agent's own current conversation, which picking that agent shows
// ("gamma" the id of Gamma AI's), `lent` a co-author's, which the chat
// shows in place of its own (chatSide.js showInChat) and only reads,
// stops, answers and exports, `by` what names it ("bob's chat with
// Codex"). While the chat lists several agents (`agents`, chat/agents.js
// chatAgents) a row also has `agentName`, its agent as the picker names it
// ("Gamma AI", "Codex on TIM-PC": agentChoice; an agent no longer listed
// by its name), which a computer agent's row says in its `by`.
export function historyRows({ shown = null, agent = GAMMA, shownId = "", gamma = [], gammaNow = null, computer = null,
  agents = [] } = {}) {
  const at = (row) => Date.parse(row.updated_at || "") || 0;
  const several = (agents || []).length > 1;
  const named = (row, name = "") => {
    if (!several) return row;
    const found = agents.find((a) => a.id === row.agent || (a.agentId && a.agentId === row.agent));
    const agentName = found ? agentChoice(found) : row.agent === GAMMA ? t("Gamma AI") : agentLabel(name || row.agent);
    return { ...row, agentName, ...(row.agent !== GAMMA ? { by: agentName } : {}) };
  };
  const theirs = (c, current = false) => named({ id: c.id, agent: c.agent_id || c.agent, title: c.title || "", preview: "",
    updated_at: c.updated_at || "", ...(current ? { current: true } : {}) }, c.agent);
  const others = [
    ...(gamma || []).map((s) => named({ ...s, agent: GAMMA })),
    ...(agent !== GAMMA && gammaNow ? [named({ id: GAMMA, agent: GAMMA, ...gammaNow, current: true })] : []),
    ...(computer?.conversations || []).map((c) => theirs(c)),
    ...(computer?.active && computer.active.id !== shownId ? [theirs(computer.active, true)] : []),
    ...(computer?.lent || []).filter((c) => c.id !== shownId).map((c) => ({ ...theirs(c), lent: true,
      by: t("{person}'s chat with {agent}", { person: c.person || t("Someone"), agent: agentLabel(c.agent) }) })),
  ].sort((a, b) => at(b) - at(a));
  return [...(shown ? [named({ id: "", agent, ...shown, updated_at: "", active: true })] : []), ...others];
}
