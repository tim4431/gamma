// What a reply's context pills say: the pages the model was given (the
// stream's {"context"} line, saved on the message) combined with the pages
// its tools read (the read_page actions' `pdf_pages`), the turns left out
// of a conversation too long for the model, and a reply cut off at the
// output limit. Pure functions, so the chat's component and the test share
// them.
import { t } from "../shared/i18n/i18n.js";
import { estimateTokens, fmtTokens } from "./tokenUsage.js";

// The PDF page spans one reply's read_page calls covered on a page, merged
// and sorted: [[3, 5], [8, 8]].
export function readSpans(actions, pageId) {
  const spans = (actions || [])
    .filter((a) => a.kind === "read" && a.page_id === pageId && Array.isArray(a.pdf_pages) && a.pdf_pages[1])
    .map((a) => [a.pdf_pages[0], a.pdf_pages[1]])
    .sort((a, b) => a[0] - b[0]);
  const out = [];
  for (const [from, to] of spans) {
    const last = out[out.length - 1];
    if (last && from <= last[1] + 1) last[1] = Math.max(last[1], to);
    else out.push([from, to]);
  }
  return out;
}

// [[3, 5], [8, 8]] → "3–5, 8"
export function formatSpans(spans) {
  return spans.map(([from, to]) => (to > from ? `${from}–${to}` : `${from}`)).join(", ");
}

// The pages of a document the model never saw: neither in the context's
// head span nor read by a tool. [] when it saw everything or the total is
// unknown.
export function unseenSpans(shown, total, spans) {
  if (!total) return [];
  const seen = shown ? [[1, shown], ...spans] : [...spans];
  const covered = new Array(total + 1).fill(false);
  for (const [from, to] of seen) for (let p = Math.max(1, from); p <= Math.min(total, to); p++) covered[p] = true;
  const out = [];
  for (let p = 1; p <= total; p++) {
    if (covered[p]) continue;
    const last = out[out.length - 1];
    if (last && last[1] === p - 1) last[1] = p;
    else out.push([p, p]);
  }
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

const SETTINGS_PATH = () => t("Settings / AI / Advanced AI settings / Context size");

// The pill for one context entry (a document the model was given), or null
// when nothing needs saying: a paper that fit whole, a native attachment
// the provider took. `actions` are the reply's tool calls (what the
// assistant read on its own), `tools` whether the reply had tools at all —
// the advice differs: with tools the assistant can read the rest itself.
export function coverageNote(c, { actions = [], tools = false } = {}) {
  const refused = c.native_requested && !c.native;
  if (!refused && !c.partial) return null;
  const what = c.title ? `“${c.title.slice(0, 48)}${c.title.length > 48 ? "…" : ""}”` : t("the PDF");
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
  const short = refused && !c.partial
    ? t("PDF file not accepted — sent as text")
    : refused
      ? t("PDF file not accepted — text only, {span}", { span })
      : t("Model saw {span}", { span });
  let long = refused ? t("This provider does not accept PDF files, so the document went as extracted text. ") : "";
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

// The pill for a reply the provider cut off at the output limit.
export function truncatedNote(truncated) {
  if (!truncated) return null;
  return {
    short: t("Reply cut off at the output limit"),
    long: t("The model reached its output limit before it finished. Ask it to continue where it stopped."),
    refused: true,
  };
}
