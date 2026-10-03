import { EditorView } from "@codemirror/view";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export async function typeDemoNote(text, prepareNote, live, cancelled) {
  const id = prepareNote(text);
  const deadline = performance.now() + 5000;
  let element;
  while (!(element = [...document.querySelectorAll('[data-guide="notes.editor"]')].find((el) => el.dataset.blockId === id))) {
    if (cancelled()) return;
    if (performance.now() > deadline) throw new Error("note editor did not open");
    await sleep(50);
  }
  if (cancelled()) return;
  element.scrollIntoView({ block: "nearest" });
  // The pointer goes to the note, then steps out of the way while the
  // words appear (as the demo's typing in a field does).
  const rect = element.getBoundingClientRect();
  const at = { x: rect.left + 15, y: rect.top + rect.height / 2 };
  live({ anchor: "notes.editor", cursor: at });
  await sleep(550);
  if (cancelled()) return;
  live({ anchor: "notes.editor", cursor: { ...at, faded: true } });
  const view = EditorView.findFromDOM(element.firstElementChild);
  if (!view) throw new Error("note editor is unavailable");
  view.focus();
  // Replaying resumes an unfinished example; never replaces a user's writing.
  const before = view.state.doc.toString();
  if (!text.startsWith(before)) return;
  for (const char of text.slice(before.length)) {
    if (cancelled() || !element.isConnected) return;
    const at = view.state.doc.length;
    view.dispatch({ changes: { from: at, insert: char }, selection: { anchor: at + char.length }, scrollIntoView: true });
    await sleep(32);
  }
  await sleep(1000);
}
