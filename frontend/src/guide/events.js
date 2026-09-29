// The guide's event bus: the app emits a few named events at the point where
// a thing happens; tours advance, and triggered tours are offered, from them.
// The catalog is closed — a tour naming an unknown event fails the tests.

export const EVENTS = [
  "window.collapsed", // {id, collapsed}: a window's title was double-clicked
  "window.moved",     // {id, side}: a window was dropped into a dock slot
  "popover.opened",   // {name} — a topbar popover opened: add, search, user, share, downloads
  "page.opened",      // {id, title}
  "home.opened",      // returned to the library
  "nav.pushed",       // a link jump recorded a place for Back to return to
  "nav.back",         // Back (the button or Alt+←) returned to that place
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
  "table.created",    // a table made with /table or a paste first rendered as one
  "table.edited",     // a table cell's in-place editor committed
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
