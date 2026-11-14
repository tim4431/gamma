// The chat's computer side, its pure part (docs/dev/local_agents.md "The
// chat's computer side"): what the side posts to /api/local-agents/chat…,
// and its words for the strip, a turn, its steps and what it changed. The
// server keeps the conversation and writes the agent's message as the turn
// goes; it stores the steps as data ({kind, path, program, status}), so
// they are worded here, in the reader's language. Node tests it
// (tests/computerChat.test.mjs).
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
// reason, at}], `cwd` and `paths` relative to the folder, "outside" or
// "~", `diff` a unified diff; a step asked about reads "asked", then
// "allowed" or "declined".
//
// On a page of another server's work folder (docs/dev/local_agents.md "A
// work folder of another server") the agent runs on the computer that
// keeps the folder, not on the page's server: the server's `computer` then
// says `remote: true` with the computer's name (`computer`), whether it is
// connected (`online`) and since when it is not (`since`). The words name
// that computer, a message waits for it while it is away, and what needs
// the window to be that computer (the strip's menu, the passages' source
// lines) is left out.
import { t, tn } from "../../shared/i18n/i18n.js";
import { sinceText } from "../../vault/vaultState.js";

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
// or the page's computers until it is read: vaultState keeperComputer).
export const isRemote = (info) => info?.remote === true;

// The other computer's name while it is away ("" while it is connected, or
// the agent is this computer's): a message waits for it.
export const awayFrom = (info) => (isRemote(info) && info.online === false ? String(info.computer || "") : "");

// Whether the strip offers its menu (Continue in a terminal, Show in
// folder): only for the agent on this computer, as both need the window to
// be the computer the folder is on.
export const offersMenu = (info) => !!info && !isRemote(info);

// The path of /api/local-agents/chat for the page, with the version of the
// conversation the side holds (the server then answers `same` while it
// is still that one).
export function chatPath(page, version = null) {
  const query = `page=${encodeURIComponent(page)}`;
  return `/local-agents/chat?${version == null ? query : `${query}&version=${encodeURIComponent(version)}`}`;
}

// The PDF passages selected (App's pdfSelections, {text, page, box}) as the
// server takes them, whose box lets it find their source line.
const passages = (selections) =>
  (selections || []).filter((s) => s?.text).map((s) => ({ text: s.text, page: s.page || null, box: s.box || null }));

// What a message posts (POST /api/local-agents/chat/turns): the id the
// client minted (the same on a retry, so the turn is made once), the page,
// the text trimmed, the PDF passages selected, and the model and effort
// chosen for the conversation ({model, effort}, neither sent when it is
// the agent's default).
export function turnBody(id, page, text, selections = [], choice = null) {
  return {
    id, page, text: String(text || "").trim(), selections: passages(selections),
    ...(choice?.model ? { model: choice.model } : {}), ...(choice?.effort ? { effort: choice.effort } : {}),
  };
}

// How many passages one ask for their source lines may carry (the
// server's cap).
export const SOURCES_MAX = 20;

// What the passage chips post to learn their source lines (POST
// /api/local-agents/chat/sources); the answer's `sources` are in the same
// order, each {file, line, end?} or null.
export const sourcesBody = (page, selections) => ({ page, selections: passages(selections).slice(0, SOURCES_MAX) });

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
// latexmk", "Searched the sources", "Worked"; one asked about says so.
export function stepText(step) {
  const path = placeOf(step);
  if (ASKED.has(step?.status)) return askedStep(step, path);
  switch (step?.kind) {
    case "read": return path ? t("Read {path}", { path }) : t("Read a file");
    case "edit": return path ? t("Edited {path}", { path }) : t("Edited a file");
    case "run": return step.program ? t("Ran {program}", { program: step.program }) : t("Ran a command");
    case "search": return t("Searched the sources");
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
// is away (`away`, its name: awayFrom) "Waiting for TIM-PC"; once it ended
// how many steps it took.
export function turnLabel(message, label, away = "") {
  const agent = message?.label || label;
  if (isLive(message) && message.asks?.length) return t("{agent} is waiting for you", { agent });
  if (away && message?.role === "agent" && message.state === "queued") return t("Waiting for {computer}", { computer: away });
  if (isLive(message)) {
    const now = stepNow(currentStep(message));
    return now ? t("{agent} is working: {detail}", { agent, detail: now }) : t("{agent} is working…", { agent });
  }
  const n = message?.steps?.length || 0;
  return tn("{n} step", "{n} steps", n);
}

// What a turn changed, on a line under its reply: "Changed
// sections/intro.tex and 1 more · The PDF was rebuilt"; "" for nothing.
export function changesLine(message) {
  const named = message?.changes || [];
  const n = Math.max(message?.changed || 0, named.length);
  const parts = [];
  if (named.length) {
    const more = n - 1;
    parts.push(t("Changed {files}", { files: more > 0 ? `${named[0]} ${t("and {n} more", { n: more })}` : named[0] }));
  } else if (n) {
    parts.push(tn("Changed {n} file", "Changed {n} files", n));
  }
  if (message?.built) parts.push(t("The PDF was rebuilt"));
  return parts.join(" · ");
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

// Where an asked command runs: the folder's relative path, a folder outside
// it, or the home folder ("~"); the work folder itself for "" or ".".
export function askPlace(cwd) {
  if (!cwd || cwd === ".") return t("In the work folder");
  if (cwd === "outside") return t("In a folder outside the work folder");
  if (cwd === "~") return t("In your home folder");
  return t("In {path}", { path: cwd });
}

// The line under an ask's buttons: what allowing it means.
export function askRisk(ask) {
  if (ask?.kind === "command") return t("It runs as you, with your files and the network.");
  if (ask?.kind === "files") return t("It changes files outside the work folder.");
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

// The strip under the header: {text, kind}, `kind` its look ("ready",
// "working", "waiting", "asking", "problem", "paused", "away"). `info` is
// the server's `computer` ({label, ready, problem, folder, paused, turn:
// {turn_id, state, ahead} | null}, and for another computer's agent
// {computer, online, since, remote: true}) or, until it is read, the
// page's (vaultState computerAgent or keeperComputer); `live` the
// conversation's turn still to end, whose step, or what it asks, the strip
// names. Another computer is named ("Codex on TIM-PC · paper"), and while
// it is away the strip says so and that a message waits for it ("TIM-PC is
// offline since 2:02 PM: a message waits and runs when it connects").
export function stripLine(info, live = null, now = new Date()) {
  const agent = info?.label || "";
  const remote = isRemote(info);
  const computer = remote ? String(info.computer || "") : "";
  if (info?.paused) {
    return {
      text: remote ? t("The work folder is paused on {computer}: {agent} starts once it syncs again.", { computer, agent })
        : t("The work folder is paused on this computer: {agent} starts once it syncs again.", { agent }),
      kind: "paused",
    };
  }
  if (live?.asks?.length) return { text: t("Waiting for you: {ask}", { ask: askTitle(live.asks[0], live.label || agent) }), kind: "asking" };
  const turn = info?.turn || (live ? { state: live.state, ahead: false } : null);
  const away = awayFrom(info);
  const when = away ? sinceText(info.since, now) : "";
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
    const doing = stepNow(currentStep(live));
    return { text: doing ? t("{agent} is working: {detail}", { agent, detail: doing }) : t("{agent} is working…", { agent }), kind: "working" };
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
