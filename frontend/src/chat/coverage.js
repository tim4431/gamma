// What a reply's context pills say: the pages the model was given (the
// stream's {"context"} line, saved on the message) combined with the pages
// its tools read (the read_page actions' `pdf_pages`; a text file's lines,
// `lines`), the turns left out of a conversation too long for the model,
// the selected passages that went short, a reply cut off at the output
// limit, one a tool limit or the window stopped (with the Continue both
// offer), and one that went without tools because its model can't call
// them. Pure functions, so the chat's component and the test share them.
import { t, tn } from "../shared/i18n/i18n.js";
import { estimateTokens, fmtTokens } from "./tokenUsage.js";

// The spans one reply's read_page calls covered on a page, merged and
// sorted: [[3, 5], [8, 8]]. `key` is the chip's field — "pdf_pages" for a
// PDF's pages, "lines" for a text file's lines.
export function readSpans(actions, pageId, key = "pdf_pages") {
  const spans = (actions || [])
    .filter((a) => a.kind === "read" && a.page_id === pageId && Array.isArray(a[key]) && a[key][1])
    .map((a) => [a[key][0], a[key][1]])
    .sort((a, b) => a[0] - b[0]);
  const out = [];
  for (const [from, to] of spans) {
    const last = out[out.length - 1];
    if (last && from <= last[1] + 1) last[1] = Math.max(last[1], to);
    else out.push([from, to]);
  }
  return out;
}

// [[3, 5], [8, 8]] → "3–5, 8"; `num` writes one number (a line count's
// thousands separators).
export function formatSpans(spans, num = String) {
  return spans.map(([from, to]) => (to > from ? `${num(from)}–${num(to)}` : num(from))).join(", ");
}

// The pages (or lines) of a document the model never saw: neither in the
// context's head span nor read by a tool. [] when it saw everything or the
// total is unknown. Walks the spans, never the units: a log runs to
// millions of lines.
export function unseenSpans(shown, total, spans) {
  if (!total) return [];
  const seen = (shown ? [[1, shown], ...spans] : [...spans])
    .map(([from, to]) => [Math.max(1, from), Math.min(total, to)])
    .filter(([from, to]) => from <= to)
    .sort((a, b) => a[0] - b[0]);
  const out = [];
  let next = 1; // the first unit no span so far covers
  for (const [from, to] of seen) {
    if (from > next) out.push([next, from - 1]);
    next = Math.max(next, to + 1);
  }
  if (next <= total) out.push([next, total]);
  return out;
}

// "p. 7 · Methods › Noise model": where the server placed a selection.
export function selectionPlace(selection) {
  const placed = (selection?.passages || []).filter((p) => p.page);
  if (!placed.length) return "";
  const pages = [...new Set(placed.map((p) => p.page))];
  const section = placed.find((p) => p.section)?.section || "";
  const short = section.length > 40 ? `${section.slice(0, 40)}…` : section;
  return `${pages.length > 1 ? "pp." : "p."} ${pages.join(", ")}${short ? ` · ${short}` : ""}`;
}

const SETTINGS_PATH = () => t("Settings / AI / Chat / Context size");

// “A page's title”, cut for a pill's sentence.
const quoted = (title) => `“${title.slice(0, 48)}${title.length > 48 ? "…" : ""}”`;

// The pill for one context entry (a document the model was given), or null
// when nothing needs saying: a paper that fit whole, a native attachment
// the provider took. `actions` are the reply's tool calls (what the
// assistant read on its own), `tools` whether the reply had tools at all —
// the advice differs: with tools the assistant can read the rest itself.
// A text file's entry (`format` "text") counts lines, not pages.
export function coverageNote(c, { actions = [], tools = false } = {}) {
  if (c.format === "text") return textCoverageNote(c, { actions, tools });
  const refused = c.native_requested && !c.native;
  if (!refused && !c.partial) return null;
  const what = c.title ? quoted(c.title) : t("the PDF");
  const place = selectionPlace(c.selection);
  const around = c.selection && !c.pages_shown;
  const shown = c.pages_shown && c.pages ? Math.min(c.pages_shown, c.pages) : 0;
  const read = c.page_id ? readSpans(actions, c.page_id) : [];
  const readText = read.length ? formatSpans(read) : "";
  const tokens = c.chars ? t("about {tokens} tokens", { tokens: fmtTokens(estimateTokens(c.chars)) }) : "";
  let span = shown
    ? t("pages 1–{pages} of {pages2}", { pages: shown, pages2: c.pages })
    : around ? (place ? t("text around {place}", { place }) : t("selected passages + head")) : `${(c.chars || 0).toLocaleString()} characters`;
  if (shown && place) span += ` · ${t("around {place}", { place })}`;
  if (readText) span += ` · ${t("read {pages} with tools", { pages: readText })}`;
  // Why the file went as text: the provider refused it, or it never went
  // (`native_skipped`: too large for one message, or no file to send).
  const why = !refused ? null
    : c.native_skipped === "too_large" ? {
      whole: t("PDF too large to attach — sent as text"),
      part: t("PDF too large to attach — text only, {span}", { span }),
      long: t("The PDF file is too large to attach (a message carries about 15 MB of PDFs), so the document went as extracted text. "),
    } : c.native_skipped === "missing" ? {
      whole: t("PDF file unavailable — sent as text"),
      part: t("PDF file unavailable — text only, {span}", { span }),
      long: t("Gamma couldn't load the PDF file, so the document went as extracted text. "),
    } : {
      whole: t("PDF file not accepted — sent as text"),
      part: t("PDF file not accepted — text only, {span}", { span }),
      long: t("This provider does not accept PDF files, so the document went as extracted text. "),
    };
  const short = why ? (c.partial ? why.part : why.whole) : t("Model saw {span}", { span });
  let long = why ? why.long : "";
  if (!c.partial) {
    long += t("{what} was sent as extracted text.", { what });
  } else if (around) {
    long += t("The model got the text around your selection{place} and the start of {what}, not the whole document.",
      { place: place ? ` (${place})` : "", what })
      + " " + (tools ? t("Its tools can read and search the rest.") : t("Turn on Tools so it can read and search the rest."));
  } else {
    const unseen = unseenSpans(shown, c.pages, read);
    long += t("The context holds {span} of {what}{tokens}.", {
      span: shown ? t("pages 1–{pages} of {pages2}", { pages: shown, pages2: c.pages }) : `${(c.chars || 0).toLocaleString()} characters`,
      what, tokens: tokens ? ` (${tokens})` : "" });
    if (place) long += " " + t("The text around your selection ({place}) went with this message.", { place });
    if (readText) long += " " + t("The assistant read pages {pages} with its tools.", { pages: readText });
    if (unseen.length) long += " " + t("Pages {pages} were not seen.", { pages: formatSpans(unseen) });
    long += " " + (tools
      ? t("Raise the budget in {path} to give it more up front.", { path: SETTINGS_PATH() })
      : t("Raise the budget in {path}, or turn on Tools so it can read and search the whole paper.", { path: SETTINGS_PATH() }));
  }
  return { short, long, refused };
}

// A text file's pill: the entry's `lines` is the file's line count and
// `lines_shown` the last line the context holds (the head; an outline of
// the rest rides with it), the read_page chips' `lines` what the tools read
// on. A text file goes as text, never as a file, so nothing is refused.
function textCoverageNote(c, { actions, tools }) {
  if (!c.partial) return null;
  const num = (n) => n.toLocaleString();
  const what = c.title ? quoted(c.title) : t("the file");
  const shown = c.lines_shown && c.lines ? Math.min(c.lines_shown, c.lines) : 0;
  const read = c.page_id ? readSpans(actions, c.page_id, "lines") : [];
  const readText = read.length ? formatSpans(read, num) : "";
  const tokens = c.chars ? t("about {tokens} tokens", { tokens: fmtTokens(estimateTokens(c.chars)) }) : "";
  const head = shown
    ? t("lines 1–{n} of {total}", { n: num(shown), total: num(c.lines) })
    : t("{chars} characters", { chars: num(c.chars || 0) });
  const span = readText ? `${head} · ${t("read lines {lines} with tools", { lines: readText })}` : head;
  let long = t("The context holds {span} of {what}{tokens}.", { span: head, what, tokens: tokens ? ` (${tokens})` : "" });
  if (readText) long += " " + t("The assistant read lines {lines} with its tools.", { lines: readText });
  const unseen = unseenSpans(shown, c.lines, read);
  if (unseen.length) long += " " + t("Lines {lines} were not seen.", { lines: formatSpans(unseen, num) });
  long += " " + (tools
    ? t("Raise the budget in {path} to give it more up front.", { path: SETTINGS_PATH() })
    : t("Raise the budget in {path}, or turn on Tools so it can read and search the whole file.", { path: SETTINGS_PATH() }));
  return { short: t("Model saw {span}", { span }), long, refused: false };
}

// The pill for the area highlights (Ctrl+drag rectangles) whose pictures
// went with the notes of a context page (`area_pictures` on its entry).
export function areaPicturesNote(c) {
  const n = c?.area_pictures || 0;
  if (!n) return null;
  return {
    short: n === 1 ? t("Picture of 1 area highlight sent") : t("Pictures of {count} area highlights sent", { count: n }),
    long: t("The notes of {what} include rectangle highlights, which have no text; the model got a picture of each region instead (at most a few per page).",
      { what: c.title ? quoted(c.title) : t("the page") }),
    refused: false,
  };
}

// The pill for turns left out of a conversation the model's window can't
// hold any more ({"trimmed": {turns}} on the stream).
export function trimmedNote(trimmed) {
  const turns = trimmed?.turns || 0;
  if (!turns) return null;
  return {
    short: t("Earlier messages left out: {turns}", { turns }),
    long: t("The conversation grew past what this model can hold, so its {turns} oldest messages were not sent with this reply. Start a new chat to free the space, or pick a model with a larger window.", { turns }),
    refused: true,
  };
}

// The pill for the passages and attached blocks the message pointed at
// that went short ({"shortened": {items, budget, asked}} on the stream:
// ai_context's passage budget cut them, or the window fit shrank it below
// what the request asked for, `asked`).
export function shortenedNote(shortened) {
  const items = shortened?.items || [];
  if (!items.length) return null;
  const chars = (n) => (n || 0).toLocaleString();
  const what = (item) => (item.kind === "pdf" ? (item.of > 1 ? t("PDF passage {i}", { i: item.n }) : t("PDF passage"))
    : item.kind === "note" ? t("Selected note text") : t("Attached block"));
  const kept = items.reduce((sum, item) => sum + (item.kept || 0), 0);
  const total = items.reduce((sum, item) => sum + (item.total || 0), 0);
  const fit = shortened.asked > shortened.budget;
  const lines = items.map((item) => t("{what}: first {kept} of {total} characters.",
    { what: what(item), kept: chars(item.kept), total: chars(item.total) }));
  return {
    short: items.length === 1
      ? t("{what} shortened: first {kept} of {total} characters sent", { what: what(items[0]), kept: chars(kept), total: chars(total) })
      : t("{n} passages shortened: {kept} of {total} characters sent", { n: items.length, kept: chars(kept), total: chars(total) }),
    long: `${lines.join(" ")} ${fit
      ? t("This model's window couldn't hold all of it, even with every earlier message left out, so less was sent. Ask about less at a time, or pick a model with a larger window.")
      : t("A message sends up to {budget} characters of what you point at, shared between the passages ({path}). Raise it there, or ask about less at a time.",
        { budget: chars(shortened.budget), path: SETTINGS_PATH() })}`,
    refused: true,
  };
}

// The pill for a reply the provider cut off at the output limit.
export function truncatedNote(truncated) {
  if (!truncated) return null;
  return {
    short: t("Reply cut off at the output limit"),
    long: t("The model reached its output limit before it finished. Continue carries on where it stopped."),
    refused: true,
  };
}

// The pill for a reply that went without tools: its model can't call them
// on this connection (`tools_off` on the stream's {"model"} line, saved on
// the message as `toolsOff`: the provider's words, or how the model
// answered the check). `model` is the reply's model name.
export function toolsOffNote(toolsOff, model) {
  if (!toolsOff) return null;
  return {
    short: t("Answered without tools"),
    long: t("{model} can't call tools on this connection ({reason}), so it answered as a plain chat. Pick another model to let the assistant work in your library, or check this one again from the message box.",
      { model: model || t("This model"), reason: toolsOff }),
    refused: true,
  };
}

const TOOL_LIMITS_PATH = () => t("Settings / AI / Tool usage / Tool limits");

// The pill for a reply a limit stopped ({"limit": {kind, rounds, changes,
// withheld?}}, saved on the message as `limit`): the round limit, the change
// limit or the model's window (gamma/ai_agent.py AgentLoop). `withheld`:
// Unlimited rounds was on, but this shared AI connection keeps the limits
// (routers/ai.py). Null for anything else.
export function limitNote(limit) {
  const kind = limit?.kind;
  if (!["rounds", "changes", "window"].includes(kind)) return null;
  const what = kind === "rounds"
    ? tn("This reply used its {n} tool round before it finished.",
      "This reply used its {n} tool rounds before it finished.", limit.rounds || 0)
    : kind === "changes"
      ? tn("This reply reached the change limit for one message after {n} change.",
        "This reply reached the change limit for one message after {n} changes.", limit.changes || 0)
      : t("This reply grew past what the model can hold.");
  const next = kind === "window"
    ? t("Continue carries on in a new message, which sends the work so far in short form.")
    : limit.withheld
      ? t("Continue carries on where it stopped, with a fresh budget. Unlimited rounds does not apply on a shared AI connection without a daily allowance.")
      : kind === "rounds"
        ? t("Continue carries on where it stopped, with a fresh budget; to go further in one reply, raise the limit or choose Unlimited rounds in {path}.", { path: TOOL_LIMITS_PATH() })
        : t("Continue carries on where it stopped, with a fresh budget; Unlimited rounds in {path} lifts the limit.", { path: TOOL_LIMITS_PATH() });
  return {
    short: kind === "rounds" ? t("Stopped at the tool-round limit")
      : kind === "changes" ? t("Stopped at the change limit") : t("Stopped: the conversation is full"),
    long: `${what} ${next}`,
    refused: true,
  };
}

// Whether a reply that stopped short offers Continue: a limit stopped it or
// the output cap cut it off, it is the conversation's last, nothing runs,
// the composer holds nothing of the user's (the message would carry what is
// attached there), and the chat can send. The chat never sends it by itself
// — a message in the user's name is the user's to send (as
// chat/fetchHandoff.js canContinue).
export function canContinueAfterLimit({ limit, truncated, isLast, idle, readOnly }) {
  return (!!limitNote(limit) || !!truncated) && !!isLast && !!idle && !readOnly;
}

// What Continue sends, as the user's message: a new message, so the loop
// starts with fresh budgets and the stopped reply's calls replayed.
export function continueAfterLimitPrompt() {
  return t("Please continue where you stopped.");
}
