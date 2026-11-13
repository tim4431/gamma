// Where the feedback view is open (docs/dev/feedback.md): a module store, as
// pdf/translateState.js and guide/recent.js keep theirs, so each opener —
// the explorer view's bar, the Comments window's header, the vault chip's
// card — opens it where it is, and App renders the one dialog
// (feedback/FeedbackHost.jsx), which reads it and holds no state of App's.
import { useSyncExternalStore } from "react";

// {kind: "file" | "folder", id} while the dialog is open, else null.
let scope = null;
const listeners = new Set();
const emit = () => { for (const fn of listeners) fn(); };
const subscribe = (fn) => {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
};

// Open the dialog on a work folder's file (`kind` "file", its page's id) or
// on a folder (`kind` "folder": the work folder's own, or one of its
// directories). Another scope takes the place of the one open.
export function openFeedback(next) {
  if (!next?.id || (next.kind !== "file" && next.kind !== "folder")) return;
  if (scope?.kind === next.kind && scope.id === next.id) return;
  scope = { kind: next.kind, id: next.id };
  emit();
}

export function closeFeedback() {
  if (!scope) return;
  scope = null;
  emit();
}

// The scope the dialog is open on, null while it is closed.
export const useFeedbackDialog = () => useSyncExternalStore(subscribe, () => scope, () => null);
