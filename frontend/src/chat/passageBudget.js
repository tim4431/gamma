// How much of what a chat message points at goes to the model, before it
// is sent: the server quotes the selected PDF passages and note text
// within the single-paper budget (Settings → AI → Chat → Context size), one
// budget shared by all of them, and serves the attached blocks within
// another of the same size (gamma/ai_context.py, passage_budget). This is
// the same rule, so a chip can say what it keeps; the reply's "shortened"
// pill (chat/coverage.js) says what the server did. Pure, for the test.
import { t } from "../shared/i18n/i18n.js";

// At most this many PDF passages, attached blocks and attached note
// selections each (ai_context.MAX_SELECTIONS, MAX_CONTEXT_BLOCKS; the
// selection in the open editor comes on top).
export const MAX_CHAT_PASSAGES = 12;
// At most this many groups' selected handwriting, and this many bytes of
// it as JSON together (ai_context.MAX_INK_SELECTIONS,
// MAX_INK_SELECTION_BYTES): the selection menu's Transcribe refuses more
// before anything is sent.
export const MAX_INK_SELECTIONS = 8;
export const MAX_INK_SELECTION_BYTES = 65536;

// How much of each item a shared budget keeps: an item within an even
// share keeps all of it, and what it leaves goes to the rest, again split
// evenly — ai_context.fair_shares, to the character.
export function fairShares(sizes, budget) {
  const shares = sizes.map(() => 0);
  let left = Math.max(0, Math.floor(budget) || 0);
  const order = sizes.map((_, i) => i).sort((a, b) => sizes[a] - sizes[b]);
  order.forEach((i, done) => {
    shares[i] = Math.min(Math.max(0, sizes[i]), Math.floor(left / (order.length - done)));
    left -= shares[i];
  });
  return shares;
}

// The characters each passage keeps, as a Map from the passage (an object
// with `text`) to its share. `passages` is in the order the request sends
// them: the PDF passages, then the note selections, the editor's first.
export function passageShares(passages, budget) {
  const list = passages.filter(Boolean);
  const shares = fairShares(list.map((p) => (p.text || "").length), budget);
  return new Map(list.map((p, i) => [p, shares[i]]));
}

const chars = (n) => (n || 0).toLocaleString();

// "first 12,000 of 31,400 characters" for a passage that won't go whole;
// "" when it will.
export function keptNote(kept, total) {
  return kept != null && kept < total ? t("first {kept} of {total} characters", { kept: chars(kept), total: chars(total) }) : "";
}

// The hover text that says why.
export function keptTitle(kept, total, budget) {
  return keptNote(kept, total)
    ? t("A message quotes up to {budget} characters of the passages you select, shared between them (Settings / AI / Chat / Context size). This one sends its first {kept} of {total}.",
      { budget: chars(budget), kept: chars(kept), total: chars(total) })
    : "";
}
