// What the PDF's next press or selection is for while something waits for
// it (useHighlightWait.js): one wait at a time, {kind, id} or null.
//
//   attach  a note's ⊕ waits for a press on a highlight, which it is
//           linked to (App linkHighlightToBlock);
//   move    a highlight waits for a selection, its new place (its menu's
//           Move to a selection…, a lost one's row, its review card's
//           Move…; docs/dev/highlight_relocation.md);
//   extend  a highlight waits for another passage of its page: a
//           selection, or a press on another highlight there, which joins
//           it (its menu's Add a passage…; shared/model/highlightParts.js).
//
// Starting one ends the one before, so one banner shows and the next press
// or selection means one thing. Escape ends it.
import { T } from "../shared/i18n/i18n.js";

export const WAIT_KINDS = Object.freeze(["attach", "move", "extend"]);

// What the banner over the page says while each waits (translated where
// it shows).
export const WAIT_BANNERS = Object.freeze({
  attach: T("Click a PDF highlight to link it"),
  move: T("Select the text where this highlight belongs"),
  extend: T("Select text to add to this highlight, or press another highlight on its page to join them"),
});

// The wait for `kind` on block `id`; null (none) for no id or an unknown kind.
export const startWait = (kind, id) => (id && WAIT_KINDS.includes(kind) ? { kind, id } : null);

// The id of the block that waits, when the wait is of `kind`; else null.
export const waitingFor = (wait, kind) => (wait?.kind === kind ? wait.id : null);

// The wait once another page opens, or another file on this one: a move or
// an extension ends, since the selection it waits for would be on a PDF the
// highlight was not picked from; a note's ⊕ goes on waiting.
export const waitAfterDocument = (wait) => (wait?.kind === "attach" ? wait : null);
