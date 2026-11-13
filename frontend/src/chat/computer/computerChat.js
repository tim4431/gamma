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
// changes: [path], changed: n, built, error, fresh, at, ended_at}, `changes`
// the folder's relative paths it named (at most 200), `changed` how many
// files changed in all, `error` the server's English sentence, `fresh`
// when a follow-up could not resume the agent's session. While the folder
// lets its agent ask (the vault's agent `runs: "ask"`), a running turn's
// message also has `asks`, what the agent waits on the person for:
// [{id, kind: "command" | "files" | "tool", command, cwd, paths, diff,
// reason, at}], `cwd` and `paths` relative to the folder, "outside" or
// "~", `diff` a unified diff; a step asked about reads "asked", then
// "allowed" or "declined".
import { t, tn } from "../../shared/i18n/i18n.js";

// How long a message may be (the server's cap).
export const TEXT_MAX = 20000;
// How long the note of a decline may be (the server's cap).
export const NOTE_MAX = 2000;
// How often the side reads the conversation again while a turn is queued
// or running, ms.
export const POLL_MS = 1500;

// The path of /api/local-agents/chat for the page, with the version of the
// conversation the side holds (the server then answers `same` while it
// is still that one).
export function chatPath(page, version = null) {
  const query = `page=${encodeURIComponent(page)}`;
  return `/local-agents/chat?${version == null ? query : `${query}&version=${encodeURIComponent(version)}`}`;
}

// What a message posts (POST /api/local-agents/chat/turns): the id the
// client minted (the same on a retry, so the turn is made once), the page,
// the text trimmed, and the PDF passages selected (App's pdfSelections,
// {text, page, box}), whose box lets the server find their source line.
export function turnBody(id, page, text, selections = []) {
  return {
    id, page, text: String(text || "").trim(),
    selections: (selections || []).filter((s) => s?.text).map((s) => ({ text: s.text, page: s.page || null, box: s.box || null })),
  };
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
// before its first step), once it ended how many steps it took.
export function turnLabel(message, label) {
  const agent = message?.label || label;
  if (isLive(message) && message.asks?.length) return t("{agent} is waiting for you", { agent });
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
// "working", "waiting", "asking", "problem", "paused"). `info` is the
// server's `computer` ({label, ready, problem, folder, paused, turn:
// {turn_id, state, ahead} | null}) or, until it is read, the page's
// (vaultState computerAgent); `live` the conversation's turn still to end,
// whose step, or what it asks, the strip names.
export function stripLine(info, live = null) {
  const agent = info?.label || "";
  if (info?.paused) return { text: t("The work folder is paused on this computer: {agent} starts once it syncs again.", { agent }), kind: "paused" };
  if (live?.asks?.length) return { text: t("Waiting for you: {ask}", { ask: askTitle(live.asks[0], live.label || agent) }), kind: "asking" };
  const turn = info?.turn || (live ? { state: live.state, ahead: false } : null);
  if (turn?.state === "queued") {
    return turn.ahead ? { text: t("Waiting for {agent} to finish its other work in this folder", { agent }), kind: "waiting" }
      : { text: t("Starting {agent}…", { agent }), kind: "working" };
  }
  if (turn?.state === "running") {
    const now = stepNow(currentStep(live));
    return { text: now ? t("{agent} is working: {detail}", { agent, detail: now }) : t("{agent} is working…", { agent }), kind: "working" };
  }
  if (info?.ready === false) return { text: String(info.problem || ""), kind: "problem" };
  if (info?.ready == null) return { text: t("Checking {agent}…", { agent }), kind: "ready" };
  const here = t("{agent} on this computer", { agent });
  return { text: info.folder ? `${here} · ${info.folder}` : here, kind: "ready" };
}

// Whether a message can go now: something written, no turn of the
// conversation still to end, and an agent that is not known to be unable
// to run (the server refuses the rest, with its reason).
export function canSend(info, conversation, text) {
  return !!String(text || "").trim() && !liveTurn(conversation) && !info?.turn && !info?.paused && info?.ready !== false;
}
