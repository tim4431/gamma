// The text highlighter's session state and keys (docs/dev/settings.md "Text
// highlighters"), pure: which of the account's highlighters is armed (its
// id, or null for off), the last one armed (what turning it on again
// brings back) and the strip button whose menu is open. The list itself is
// the account's (`highlightPresets`, shared/model/markStyle.js); which one
// is armed stays with the session, as the pen strip's armed pen does. The
// armed one is resolved against the list on render, so a highlighter
// removed elsewhere just turns the highlighter off. useTextHighlighter.js
// holds it.
import { presetKeys, shownPresets } from "../shared/model/markStyle.js";

export const HL_IDLE = Object.freeze({ armed: null, last: null, menu: null });

export const armedTo = (s, id) => ({ ...s, armed: id, last: id ?? s.last });
export const disarmed = (s) => (s.armed ? { ...s, armed: null } : s);
// A press on a highlighter: off when it is the armed one, else armed. With
// no id (a command turning it on), the last one armed when it is still in
// the list, else the first shown. An armed id the list no longer has (one
// removed in Settings or on another device) counts as off.
export function toggled(s, id, list) {
  const on = s.armed && list.some((p) => p.id === s.armed) ? s.armed : null;
  if (id == null) {
    if (on) return disarmed(s);
    const back = list.some((p) => p.id === s.last) ? s.last : shownPresets(list)[0]?.id;
    return back ? armedTo(s, back) : disarmed(s);
  }
  return on === id ? disarmed(s) : armedTo(s, id);
}
// The next one in key order over the strip's buttons, wrapping; from off,
// the first.
export function nextArmed(s, list) {
  const ids = stripPresets(list, s.armed).map((p) => p.id);
  if (!ids.length) return s;
  const at = ids.indexOf(s.armed);
  return armedTo(s, ids[(at + 1) % ids.length]);
}
export const menuOpened = (s, id, at) => ({ ...s, menu: { id, x: at.x, y: at.y, anchorBottom: at.anchorBottom } });
export const menuClosed = (s) => (s.menu ? { ...s, menu: null } : s);

// The strip's highlighter buttons: the shown ones, and the armed one after
// them when it is hidden, pinned so it can be turned off. `only` (the
// strip item hidden in Viewer tools, pinned while one is armed): the armed
// one alone.
export function stripPresets(list, armed, only = false) {
  const on = armed ? list.find((p) => p.id === armed) : null;
  if (only) return on ? [on] : [];
  const shown = shownPresets(list);
  return on?.hidden ? [...shown, on] : shown;
}

// The digit a key event is, 1–9, by its code (Digit1…9, Numpad1…9), or 0.
const digitOf = (code) => {
  const m = /^(?:Digit|Numpad)([1-9])$/.exec(code || "");
  return m ? Number(m[1]) : 0;
};
// A key that is someone else's: a modifier chord (AltGr, Ctrl and Alt
// together, counts as none, as on the pen strip), a repeat, an IME's key, a
// field's, a dialog's, or the pen strip's own 1–9 while it is open.
function skipped(e, { inField, dialog, inkOpen }) {
  const altGr = !!(e.ctrlKey && e.altKey);
  if (((e.ctrlKey || e.metaKey || e.altKey) && !altGr) || e.repeat || e.isComposing || e.keyCode === 229) return true;
  return !!(inField || dialog || inkOpen);
}

// A key while a highlighter is armed (and no popup is open) →
// {kind: "arm", id} (1–9: the highlighter at that key; its own key turns it
// off, {kind: "off"}) · {kind: "off"} (Escape) · null.
export function highlighterKey(e, { list, armed, inField = false, dialog = false, inkOpen = false }) {
  if (!armed || skipped(e, { inField, dialog, inkOpen })) return null;
  if (e.code === "Escape") return { kind: "off" };
  const n = digitOf(e.code);
  const id = n ? presetKeys(list)[n - 1] : null;
  if (!id) return null;
  return id === armed ? { kind: "off" } : { kind: "arm", id };
}

// A key while the selection popup is open → {kind: "pick", id} (1–9: the
// highlighter at that key; Enter: the armed one, unless a control has the
// focus, `onControl`, whose Enter is its own press) · null.
export function popupKey(e, { list, armed, inField = false, dialog = false, inkOpen = false, onControl = false }) {
  if (skipped(e, { inField, dialog, inkOpen })) return null;
  if (e.code === "Enter" || e.code === "NumpadEnter") {
    return armed && !onControl && list.some((p) => p.id === armed) ? { kind: "pick", id: armed } : null;
  }
  const n = digitOf(e.code);
  const id = n ? presetKeys(list)[n - 1] : null;
  return id ? { kind: "pick", id } : null;
}
