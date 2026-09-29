// Shared permission presets used by Settings and the chat settings shortcut.
export const LIBRARY_READ_TOOLS = ["list", "read", "block_read", "view", "search"];
export const READ_TOOLS = [...LIBRARY_READ_TOOLS, "web_search", "web_read", "publisher_cookies"];
export const WRITE_TOOLS = ["rename", "move", "block_edit"];
export const toolsForKind = (kind) => kind === "folder"
  ? [...READ_TOOLS, ...WRITE_TOOLS]
  : [...READ_TOOLS.filter((key) => key !== "list"), "block_edit"];

export function permissionPreset(kind, permissions = {}) {
  const keys = toolsForKind(kind);
  if (keys.every((key) => permissions[key] !== false)) return "edit";
  if (keys.every((key) => (permissions[key] !== false) === READ_TOOLS.includes(key))) return "read";
  if (keys.every((key) => (permissions[key] !== false) === LIBRARY_READ_TOOLS.includes(key))) return "library";
  return "custom";
}

export function presetPermissions(kind, preset) {
  const readable = preset === "library" ? LIBRARY_READ_TOOLS : READ_TOOLS;
  return Object.fromEntries(toolsForKind(kind).map((key) => [key, preset === "edit" || readable.includes(key)]));
}
