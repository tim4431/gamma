// What the person points with, asked in one place (docs/dev/ui-design.md,
// "One behaviour for mouse and finger"). Feature code does not branch on a
// device: a gesture goes through shared/ui/press.js and a size or a reveal
// through CSS. These three answers are for what neither can decide.

let last = "mouse";
if (typeof window !== "undefined") {
  window.addEventListener("pointerdown", (e) => { last = e.pointerType || "mouse"; }, { capture: true, passive: true });
}

// The pointer of the last press: "mouse", "touch" or "pen". A `click` does
// not say which everywhere, so a click handler asks here.
export const lastPointer = () => last;

// Text is typed on an on-screen keyboard: the primary pointer is coarse (a
// tablet, a phone), or the last press was a finger or a pen (a touch laptop,
// whose primary pointer is its trackpad). Return is then a line break's
// only key, and Tab and the Ctrl chords are missing.
export const touchTyping = () => !!globalThis.matchMedia?.("(pointer: coarse)").matches || last !== "mouse";

// The device has a touch screen at all, whatever is in use right now: what
// decides whether a finger-only control (the area-note toggle) is offered.
export const touchScreen = () => (globalThis.navigator?.maxTouchPoints || 0) > 0;
