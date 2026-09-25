// The guide's event bus: the app emits a few named events at the point where
// a thing happens; tours advance, and triggered tours are offered, from them.
// The catalog is closed — a tour naming an unknown event fails the tests.

export const EVENTS = [
  "popover.opened",   // {name} — a topbar popover opened: add, search, user, share, downloads
  "page.opened",      // {id}
  "home.opened",      // returned to the library
  "palette.opened",   // the Ctrl+P page palette opened
  "highlight.created", // {id, kind: "text" | "area"}
  "block.created",
  "block.indented",
  "chat.sent",
  "chat.cited",       // an AI reply finished with a citation link in it
  "citation.shown",   // a citation's quote was found and marked in the PDF
  "share.created",    // the page got a share link
  "peer.joined",      // someone else came onto the open page
  "ink.stroke",       // a handwriting stroke was drawn
  "ink.options",      // the armed tool's options row opened
  "ink.erased",       // handwriting was erased
  "ink.undone",       // a handwriting change was undone
  "table.shown",      // an editable table rendered in the notes
  "conflict.shown",   // a clone conflict's versions were shown
  "ref.search",       // the [[ block search opened with results
  "math.previewed",   // the live formula preview came up while typing math
  "settings.opened",  // {pane}
];

const listeners = new Set();

export const guideEvents = {
  emit(name, payload = {}) {
    if (!EVENTS.includes(name)) {
      console.warn(`guide: unknown event "${name}"`);
      return;
    }
    for (const fn of listeners) fn(name, payload);
  },
  subscribe(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  },
};

// Does an emitted event satisfy a step's `advanceOn` (or a trigger)?
export function eventMatches(spec, name, payload) {
  if (!spec || spec.event !== name) return false;
  if (!spec.match) return true;
  return Object.entries(spec.match).every(([k, v]) => payload?.[k] === v);
}
