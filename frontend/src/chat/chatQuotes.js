// What the chat says about its own transcript (docs/dev/ai.md "Selecting
// the transcript"): a passage selected in a message, quoted into the next
// one ("Ask about this"), and a note made of it. A quote is the selection's
// Markdown source (shared/ui/Widgets.jsx selectionMarkdown), so formulas,
// lists and links come back as the reply wrote them.
//
// The quote rides in the message's own text, as a Markdown quote ahead of
// what the user typed: the model reads it there, every agent alike (Gamma
// AI and a computer's), the conversation replays it with the turn, and an
// export keeps it. The transcript draws a sent message's quotes as quotes
// (splitQuoted), the passages' start the bubble shows after the text too.

export const MAX_CHAT_QUOTES = 6;      // quotes one message carries
export const QUOTE_MAX = 8_000;         // characters of one quote (the rest cut, with "…")

// One quote's text as the message carries it: every line behind "> ".
export function quoteMarkdown(text) {
  return String(text || "").split("\n").map((line) => (line.trim() ? `> ${line}` : ">")).join("\n");
}

// A selection as one quote: trimmed, its blank runs folded to one, cut at
// QUOTE_MAX. "" when nothing is left.
export function quoteOf(text) {
  const clean = String(text || "").replace(/\r\n?/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
  return clean.length > QUOTE_MAX ? `${clean.slice(0, QUOTE_MAX).trimEnd()}…` : clean;
}

// `quotes` with `text` added: once (the same passage twice is one quote),
// at most MAX_CHAT_QUOTES. Returns {quotes, full} — `full` when it was
// refused for the cap.
export function addQuote(quotes, text) {
  const quote = quoteOf(text);
  if (!quote || quotes.includes(quote)) return { quotes, full: false };
  if (quotes.length >= MAX_CHAT_QUOTES) return { quotes, full: true };
  return { quotes: [...quotes, quote], full: false };
}

// The message `text` with the `quotes` ahead of it, each a Markdown quote
// of its own.
export function withChatQuotes(text, quotes = []) {
  const quoted = (quotes || []).filter(Boolean).map(quoteMarkdown).join("\n\n");
  return quoted ? (text ? `${quoted}\n\n${text}` : quoted) : text;
}

// A user message's text as the bubble draws it: runs of "> " lines are
// quotes (the passages a message quoted, its marker stripped), the rest the
// user's words. [{quote, text}], in order, none empty.
export function splitQuoted(text) {
  const out = [];
  const push = (quote, line) => {
    const last = out[out.length - 1];
    if (last && last.quote === quote) last.lines.push(line);
    else out.push({ quote, lines: [line] });
  };
  for (const line of String(text || "").split("\n")) {
    const m = /^ {0,3}>( ?)(.*)$/.exec(line);
    push(!!m, m ? m[2] : line);
  }
  return out.map((s) => ({ quote: s.quote, text: s.lines.join("\n").replace(/^\n+|\n+$/g, "") }))
    .filter((s) => s.text.trim());
}

// --- a note made of a selection --------------------------------------------

const TITLE_MAX = 80;

// The title of a page a note makes on its own (a folder's chat): the
// note's first line, its Markdown marks dropped, cut at a word near
// TITLE_MAX.
export function noteTitle(markdown) {
  const line = String(markdown || "").split("\n").map((l) => l.trim()).find((l) => l && !/^(```|\$\$|---|\|)/.test(l)) || "";
  const plain = line
    .replace(/^(#{1,6}\s+|>\s*|[-*+]\s+(\[[ xX]\]\s+)?|\d+[.)]\s+)+/, "")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\$([^$]+)\$/g, "$1")
    .replace(/(\*\*|__|~~)(.+?)\1/g, "$2")
    .replace(/(^|[^\w*])([*_])(?=\S)(.+?)(?<=\S)\2(?!\w)/g, "$1$3")
    .replace(/`([^`]*)`/g, "$1")
    .trim();
  if (plain.length <= TITLE_MAX) return plain;
  const cut = plain.slice(0, TITLE_MAX);
  const space = cut.lastIndexOf(" ");
  return `${(space > TITLE_MAX / 2 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

// What "Structured by AI" asks the model (a plain chat request of its own,
// with no conversation, page or tools): the passage rewritten as a note.
// Not shown to the user, so it is not translated.
export function structurePrompt(markdown) {
  return [
    "Rewrite the text between the <text> tags as a well-structured note: a short title as a heading,",
    "then the content organized under subheadings and in nested bullet points where they help.",
    "Keep every fact, number, formula, link and citation as given, and add nothing that is not in the text.",
    "Keep the text's language. Write math as $…$ inline and $$…$$ on lines of their own.",
    "Answer with the note alone, in Markdown, without an introduction or a closing remark.",
    "",
    "<text>",
    markdown,
    "</text>",
  ].join("\n");
}

// The note out of the model's answer: a fence around the whole of it
// (```markdown … ```) taken off.
export function structuredNote(answer) {
  const text = String(answer || "").trim();
  const fenced = /^```[a-zA-Z]*\n([\s\S]*?)\n```$/.exec(text);
  return (fenced ? fenced[1] : text).trim();
}
