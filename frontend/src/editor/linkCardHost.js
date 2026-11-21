// The note editors that show a link card (BlockCmEditor.jsx), by their
// CodeMirror view: Ctrl+K and the Link buttons of the selection toolbar and
// the editing bar (markCommands.runInsertLink) open its form on the
// selection. Plain JS, so the command catalog stays free of editor imports.
const hosts = new WeakMap();

export function setLinkCardHost(view, open) {
  if (open) hosts.set(view, open);
  else hosts.delete(view);
}

export const linkCardHost = (view) => hosts.get(view) || null;
