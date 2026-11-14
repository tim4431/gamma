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
// ended_at}, `model` and `effort` what the turn ran with, `changes`
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
// wrapper runs, when the server could read it back whole. A step asked
// about reads "asked", then "allowed" or "declined".
//
// On a page of another server's work folder (docs/dev/local_agents.md "A
// work folder of another server") the agent runs on the computer that
// keeps the folder, not on the page's server: the server's `computer` then
// says `remote: true` with the computer's name (`computer`), whether it is
// connected (`online`) and since when it is not (`since`). The words name
// that computer, a message waits for it while it is away, and what needs
// the window to be that computer (the strip's menu, the passages' source
// lines) is left out. What the agent asks there is answered on that
// computer, in its desktop app's window ("Asking on this computer"): the
// message never carries `asks`, only `waiting: true` while one waits, and
// the page draws no card.
import { t, tn } from "../../shared/i18n/i18n.js";
import { agentWorking, sinceText } from "../../vault/vaultState.js";
import { GAMMA } from "../agents.js";

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
// the agent is this computer's): a message waits for it.
export const awayFrom = (info) => (isRemote(info) && info.online === false ? String(info.computer || "") : "");

// Whether the strip offers its menu (Continue in a terminal, Show in
// folder): only for the agent on this computer, as both need the window to
// be the computer the folder is on.
export const offersMenu = (info) => !!info && !isRemote(info);

// Whether the strip's menu offers Show in folder: on a page (`page`) of a
// work folder kept here (the server's `dir` a vault's), whose file the
// shell shows.
export const revealsFile = (info, page) => !!page && offersMenu(info) && info.dir?.kind === "vault";

// Whether the page draws the cards of what the agent asks: only for the
// agent on this computer; another computer's are answered there.
export const drawsAsks = (info) => !isRemote(info);

// Whether another computer's turn (its agent's message) waits there for
// the person's answer.
export const waitsThere = (info, message) => isRemote(info) && message?.waiting === true;

// The path of /api/local-agents/chat for the bucket (a page's id, a
// folder's, or "home"), with the version of the conversation the side
// holds (the server then answers `same` while it is still that one) and
// the agent picked (the server shows no conversation of another agent).
export function chatPath(bucket, version = null, agent = "") {
  const query = new URLSearchParams({ bucket });
  if (version != null) query.set("version", String(version));
  if (agent) query.set("agent", agent);
  return `/local-agents/chat?${query}`;
}

// The PDF passages selected (App's pdfSelections, {text, page, box}) as the
// server takes them, whose box lets it find their source line.
const passages = (selections) =>
  (selections || []).filter((s) => s?.text).map((s) => ({ text: s.text, page: s.page || null, box: s.box || null }));

// What a message posts (POST /api/local-agents/chat/turns): the id the
// client minted (the same on a retry, so the turn is made once), the
// bucket, the text trimmed, the PDF passages selected, and what was chosen
// for the conversation (`choice`: {agent, model, effort, dir}): the agent
// picked, the model and effort, neither sent when it is the agent's
// default, and where a conversation that begins works (`dir`: an offered
// directory's id, "" for one of its own; null, not sent, for the
// conversation's own).
export function turnBody(id, bucket, text, selections = [], choice = null) {
  return {
    id, bucket, text: String(text || "").trim(), selections: passages(selections),
    ...(choice?.agent ? { agent: choice.agent } : {}),
    ...(choice?.model ? { model: choice.model } : {}), ...(choice?.effort ? { effort: choice.effort } : {}),
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
export function lastEnded(conversation) {
  return (conversation?.messages || []).findLast((m) => m.role === "agent" && m.ended_at)?.ended_at || null;
}

// The model and effort a conversation goes on with: the person's choice
// for it in this tab (`picked`), else what its newest turn ran with (the
// agent's message keeps `model` and `effort`, null for the default; one
// from before it kept them says nothing), each kept only while the agent
// still lists it; "" is the agent's default. `shown` is the model the chip
// names: the one chosen, else the agent's default, else its first.
export function modelChoice(models, conversation = null, picked = null) {
  const listed = models || [];
  const ran = (conversation?.messages || []).findLast((m) => m.role === "agent" && ("model" in m || "effort" in m));
  const said = picked || (ran ? { model: ran.model || "", effort: ran.effort || "" } : null);
  const model = listed.some((m) => m.id === said?.model) ? said.model : "";
  const shown = listed.find((m) => m.id === model) || listed.find((m) => m.default) || listed[0] || null;
  const effort = said?.effort && (shown?.efforts || []).includes(said.effort) ? said.effort : "";
  return { model, effort, shown };
}

// What the model chip reads: the model's name and the effort chosen
// ("GPT-5.5 · high").
export const modelDisplay = (choice) => [choice?.shown?.label || choice?.shown?.id || "", choice?.effort || ""].filter(Boolean).join(" · ");

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

// A step in words, done: "Read sections/intro.tex", "Edited …", "Ran
// latexmk", "Searched the sources", "Read Gamma: read_page" (a call of
// Gamma's tools, by the tool's name), "Worked"; one asked about says so.
export function stepText(step) {
  const path = placeOf(step);
  if (ASKED.has(step?.status)) return askedStep(step, path);
  switch (step?.kind) {
    case "read": return path ? t("Read {path}", { path }) : t("Read a file");
    case "edit": return path ? t("Edited {path}", { path }) : t("Edited a file");
    case "run": return step.program ? t("Ran {program}", { program: step.program }) : t("Ran a command");
    case "search": return t("Searched the sources");
    case "gamma": return step.tool ? t("Read Gamma: {tool}", { tool: step.tool }) : t("Read Gamma");
    default: return t("Worked");
  }
}

// A step in words while it runs ("Editing sections/intro.tex"), what the
// strip and the turn's pill say the agent is at; "" for a step of no kind
// worth naming.
export function stepNow(step) {
  const path = placeOf(step);
  switch (step?.kind) {
    case "read": return path ? t("Reading {path}", { path }) : t("Reading a file");
    case "edit": return path ? t("Editing {path}", { path }) : t("Editing a file");
    case "run": return step.program ? t("Running {program}", { program: step.program }) : t("Running a command");
    case "search": return t("Searching the sources");
    case "gamma": return step.tool ? t("Reading Gamma: {tool}", { tool: step.tool }) : t("Reading Gamma");
    default: return "";
  }
}

// The step a running turn is at: its newest still started, else its newest.
export function currentStep(message) {
  const steps = message?.steps || [];
  return steps.findLast((s) => s.status === "started") || steps.at(-1) || null;
}

// What a turn's pill says: while it is queued or running the agent at work
// ("Codex is working: Editing sections/intro.tex", or "Codex is working…"
// before its first step), or while it is queued for another computer that
// is away (`away`, its name: awayFrom) "Waiting for TIM-PC"; while another
// computer's turn (`computer`, its name) waits there for the person's
// answer "Codex is waiting for your answer on TIM-PC", or that it is away;
// once it ended how many steps it took.
export function turnLabel(message, label, away = "", computer = "") {
  const agent = message?.label || label;
  if (isLive(message) && message.asks?.length) return t("{agent} is waiting for you", { agent });
  if (isLive(message) && message.waiting === true && (away || computer)) {
    return away ? t("Waiting for your answer on {computer}, which is offline", { computer: away })
      : t("{agent} is waiting for your answer on {computer}", { agent, computer });
  }
  if (away && message?.role === "agent" && message.state === "queued") return t("Waiting for {computer}", { computer: away });
  if (isLive(message)) return agentWorking(agent, stepNow(currentStep(message)));
  const n = message?.steps?.length || 0;
  return tn("{n} step", "{n} steps", n);
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
// ({text, selections}); null when there is none or the turn is not the
// conversation's newest.
export function retryOf(conversation, message) {
  const messages = conversation?.messages || [];
  const at = messages.indexOf(message);
  const asked = at > 0 ? messages[at - 1] : null;
  if (message?.state !== "failed" || at !== messages.length - 1 || asked?.role !== "user" || !asked.text) return null;
  return { text: asked.text, selections: asked.selections || [] };
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

// Where the conversation's agent works, as the strip says it after its
// first line (the server's `computer.dir`: a vault's, already named there
// as the folder; {kind: "offered", id, name}; null for a directory of its
// own): null for a vault's and another computer's agent; else, while the
// conversation has no turn, the menu of the directories offered (`dirs`,
// GET /api/local-agents' [{id, path, name}]) and "No folder" ({pick: true,
// value, options}, `value` the one `picked` while it is offered, else "");
// once it has one, {pick: false, text} naming the one it works in.
export function workPlace(info, conversation, dirs = [], picked = "") {
  if (!info || isRemote(info) || info.dir?.kind === "vault") return null;
  if (conversation?.messages?.length) {
    return { pick: false, text: info.dir?.kind === "offered" ? String(info.dir.name || "") : t("No folder") };
  }
  const offered = (dirs || []).filter((d) => d && d.id);
  const options = [["", t("No folder")], ...offered.map((d) => [d.id, String(d.name || d.path || d.id)])];
  return { pick: true, value: offered.some((d) => d.id === picked) ? picked : "", options };
}

// The empty transcript's line: what the agent is asked about and where it
// works (`place` workPlace's, `where` another computer's name, `page`
// whether the bucket is a page, else a folder).
export function emptyLine(info, label, where = "", place = null, page = true) {
  if ((info?.dir?.kind === "vault" || where) && page) {
    return where
      ? t("Ask {agent} about this file or to change it: it works in the synced folder's directory on {computer}.", { agent: label, computer: where })
      : t("Ask {agent} about this file or to change it: it works in the synced folder's directory on this computer.", { agent: label });
  }
  if (info?.dir?.kind === "vault" || where) {
    return where
      ? t("Ask {agent} about this folder or its files, or to change them: it works in the synced folder's directory on {computer}.", { agent: label, computer: where })
      : t("Ask {agent} about this folder or its files, or to change them: it works in the synced folder's directory on this computer.", { agent: label });
  }
  const offered = place && (place.pick ? place.value : info?.dir?.kind === "offered");
  return offered ? t("Ask {agent} anything: it works in the directory you chose on this computer.", { agent: label })
    : t("Ask {agent} anything: it works in an empty directory of its own on this computer.", { agent: label });
}

// The strip under the header: {text, kind}, `kind` its look ("ready",
// "working", "waiting", "asking", "problem", "paused", "away"). `info` is
// the server's `computer` ({label, ready, problem, folder, paused, turn:
// {turn_id, state, ahead} | null}, and for another computer's agent
// {computer, online, since, remote: true}) or, until it is read, the
// picked agent's (chat/agents.js stripFacts); `live` the
// conversation's turn still to end, whose step, or what it asks, the strip
// names. Another computer is named ("Codex on TIM-PC · paper"), and while
// it is away the strip says so and that a message waits for it ("TIM-PC is
// offline since 2:02 PM: a message waits and runs when it connects"); a
// turn of it that waits for the person's answer there says where to answer
// ("Waiting for your answer on TIM-PC"), and that it is away when it is.
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
  if (isLive(live) && waitsThere(info, live)) {
    if (!away) return { text: t("Waiting for your answer on {computer}", { computer }), kind: "asking" };
    return {
      text: when ? t("Waiting for your answer on {computer}, offline since {when}", { computer: away, when })
        : t("Waiting for your answer on {computer}, which is offline", { computer: away }),
      kind: "away",
    };
  }
  if (live?.asks?.length) return { text: t("Waiting for you: {ask}", { ask: askTitle(live.asks[0], live.label || agent) }), kind: "asking" };
  const turn = info?.turn || (live ? { state: live.state, ahead: false } : null);
  if (away && turn?.state === "queued") {
    return {
      text: when ? t("Waiting for {computer}, offline since {when}: the message runs when it connects", { computer: away, when })
        : t("Waiting for {computer}, which is offline: the message runs when it connects", { computer: away }),
      kind: "away",
    };
  }
  if (turn?.state === "queued") {
    return turn.ahead ? { text: t("Waiting for {agent} to finish its other work in this folder", { agent }), kind: "waiting" }
      : { text: t("Starting {agent}…", { agent }), kind: "working" };
  }
  if (turn?.state === "running") {
    return { text: agentWorking(agent, stepNow(currentStep(live))), kind: "working" };
  }
  if (away) {
    return {
      text: when ? t("{computer} is offline since {when}: a message waits and runs when it connects", { computer: away, when })
        : t("{computer} is offline: a message waits and runs when it connects", { computer: away }),
      kind: "away",
    };
  }
  if (info?.ready === false) return { text: String(info.problem || ""), kind: "problem" };
  if (info?.ready == null) return { text: t("Checking {agent}…", { agent }), kind: "ready" };
  const here = remote ? t("{agent} on {computer}", { agent, computer }) : t("{agent} on this computer", { agent });
  return { text: info.folder ? `${here} · ${info.folder}` : here, kind: "ready" };
}

// Whether a message can go now: something written, no turn of the
// conversation still to end, and an agent that is not known to be unable
// to run (the server refuses the rest, with its reason). Another computer
// that is away takes one: it waits there and runs when it connects.
export function canSend(info, conversation, text) {
  return !!String(text || "").trim() && !liveTurn(conversation) && !info?.turn && !info?.paused
    && (info?.ready !== false || !!awayFrom(info));
}

// What a step's chip is among the turn's steps in the transcript (the
// chips of Gamma AI's tool calls, with no call to unfold): its words, its
// icon by a kind of the chat's own (chat/AgentChanges.jsx ACTION_ICONS: a
// file read, a file edited, a command, a search, other work; a call of
// Gamma's tools the kind Gamma AI's own call of that tool has, so the
// steps pill counts it as it counts Gamma AI's reads, chat/agentSteps.js
// stepsSummary), none of which is a change of the library or the notes
// (chat/agentSteps.js isChange), and whether it failed or was not allowed.
const STEP_KINDS = { read: "file", edit: "fileEdit", run: "run", search: "search" };
const GAMMA_KINDS = {
  list_pages: "list", list_folders: "list", list_feedback: "list", list_workspaces: "list",
  search_library: "search", view_pdf_page: "view", view_ink: "ink", view_image: "image", cite: "cite",
};
const chipKind = (step) => (step?.kind === "gamma" ? GAMMA_KINDS[step.tool] || "read" : STEP_KINDS[step?.kind] || "work");
export function stepChip(step) {
  return {
    kind: chipKind(step), summary: stepText(step),
    ...(step?.status === "failed" ? { error: true } : {}), ...(step?.status === "declined" ? { declined: true } : {}),
  };
}

// The conversation in the shape the chat's transcript draws Gamma AI's in
// (chat/ChatTranscript.jsx), `info` the strip's facts (the server's
// `computer`, else the picked agent's: chat/agents.js stripFacts). A
// person's message is {id, role: "user", text, passages: [{text, page}]};
// the agent's {id, role: "ai", turnId, label, text, partial, pill, actions,
// asks, files, notice, model, effort}: `partial` while its turn is queued
// or running (the reply as it is typed), `pill` the steps pill's {label,
// waiting} (turnLabel), `actions` its steps as chips (stepChip), `asks`
// what it waits on the person for, while it does and the page draws them
// (drawsAsks), `files` what it changed (turnChanges, with `vault` while it
// works in a work folder's directory, whose paths a link opens), `notice`
// the line of a session not resumed, `model` the model's name as the agent
// lists it. A stopped turn ends its text "(stopped)", as a Gamma AI reply
// stopped does; a failed one carries the error card's fields (chat/
// chatErrors.js, kind "agent": the computer's own sentence its headline),
// `error` only when it wrote nothing (with text, the card is compact).
export function shownMessages(conversation, info) {
  const label = info?.label || "";
  const away = awayFrom(info);
  const computer = isRemote(info) ? String(info.computer || "") : "";
  const models = info?.models || [];
  const asks = drawsAsks(info);
  const vault = info?.dir?.kind === "vault";
  return (conversation?.messages || []).map((m) => {
    if (m.role === "user") {
      return { id: m.id, role: "user", text: m.text || "",
        passages: (m.selections || []).filter((s) => s?.text).map((s) => ({ text: s.text, page: s.page || null })) };
    }
    const live = isLive(m);
    const by = m.label || label;
    const text = m.text || "";
    const files = turnChanges(m);
    return {
      id: m.id, role: "ai", turnId: m.turn_id, label: by,
      text: m.state === "stopped" ? [text, `*(${t("stopped")})*`].filter(Boolean).join("\n\n") : text,
      partial: live,
      pill: { label: turnLabel(m, label, away, computer), waiting: live && (!!m.asks?.length || m.waiting === true) },
      actions: (m.steps || []).map(stepChip),
      asks: live && asks ? m.asks || [] : [],
      files: files ? { ...files, vault } : null,
      notice: m.fresh ? freshLine(by) : "",
      model: models.find((x) => x.id === m.model)?.label || m.model || "", effort: m.effort || "",
      ...(m.state === "failed" ? { ...(text ? {} : { error: true }), errorKind: "agent", errorProvider: by, errorSaid: endText(m) } : {}),
    };
  });
}

// The rows of the chat's History (chat/ChatHistoryPop.jsx): every agent's
// conversations of the bucket together, the one shown first (`shown`,
// {title, preview} or null for none), then the others newest first: Gamma
// AI's archived ones (`gamma`, GET /api/chat-history's sessions) and its
// current one while another agent is shown (`gammaNow`, {title, preview,
// updated_at} or null), the computer agents' archived ones and their
// active one while it is not the one shown (`computer`, GET
// /api/local-agents/chat/history's {conversations, active}; null unread).
// `agent` is the agent shown, `shownId` the id of its conversation (a
// computer agent's). A row is {id, agent, title, preview, updated_at,
// active, current} (Gamma AI's with its `count`): `agent` its agent's
// name, `active` the one shown, `current` an agent's own current
// conversation, which picking that agent shows ("gamma" the id of Gamma
// AI's).
export function historyRows({ shown = null, agent = GAMMA, shownId = "", gamma = [], gammaNow = null, computer = null } = {}) {
  const at = (row) => Date.parse(row.updated_at || "") || 0;
  const theirs = (c, current = false) => ({ id: c.id, agent: c.agent, title: c.title || "", preview: "", updated_at: c.updated_at || "",
    ...(current ? { current: true } : {}) });
  const others = [
    ...(gamma || []).map((s) => ({ ...s, agent: GAMMA })),
    ...(agent !== GAMMA && gammaNow ? [{ id: GAMMA, agent: GAMMA, ...gammaNow, current: true }] : []),
    ...(computer?.conversations || []).map((c) => theirs(c)),
    ...(computer?.active && computer.active.id !== shownId ? [theirs(computer.active, true)] : []),
  ].sort((a, b) => at(b) - at(a));
  return [...(shown ? [{ id: "", agent, ...shown, updated_at: "", active: true }] : []), ...others];
}
