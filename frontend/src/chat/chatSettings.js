// The chat's tool permissions: one catalog shared by Settings, the chat
// popover, the stored preference (app/prefDefs.js) and the approval card.
// Each permission has a state (gamma/ai_permissions.py on the server):
// "allow" runs its tools whenever the assistant calls them, "ask" shows an
// approval card before each call (chat/ApprovalCard.jsx), "off" never
// offers them. Reading is allowed by default, and changes ask.
const PERM_STATES = ["allow", "ask", "off"];

// [key, group, folder chat only]: every permission in display order. The
// groups are Settings' sections; "edit" holds the ones that change the
// library, which is what asks by default.
const CATALOG = [
  ["list", "library", true],
  ["read", "library"],
  ["block_read", "library"],
  ["view", "library"],
  ["search", "library"],
  ["web_search", "web"],
  ["web_read", "web"],
  ["publisher_cookies", "web"],
  ["rename", "edit", true],
  ["move", "edit", true],
  ["block_edit", "edit"],
];
export const PERMISSION_KEYS = CATALOG.map(([key]) => key);
const GROUP = Object.fromEntries(CATALOG.map(([key, group]) => [key, group]));
export const permGroup = (key) => GROUP[key] || "library";
const changesLibrary = (key) => GROUP[key] === "edit";
// Using journal sign-ins is part of fetching a document, not a call of its
// own to approve: it is on or off.
export const permStates = (key) => (key === "publisher_cookies" ? ["allow", "off"] : PERM_STATES);
export const defaultPerm = (key) => (changesLibrary(key) ? "ask" : "allow");

// A stored or sent value as a state. A boolean is the older on / off value:
// off is Off, and on is the permission's default, so a change asks and
// reading is allowed.
export function normalizePerm(key, value) {
  if (value === true) return defaultPerm(key);
  if (value === false) return "off";
  if (permStates(key).includes(value)) return value;
  if (value === "ask") return "allow"; // a permission that cannot ask
  return defaultPerm(key);
}
export const permState = (perms, key) => normalizePerm(key, perms?.[key]);

// The permissions a chat kind has: the folder chat all of them, a page chat
// all but listing pages and renaming or moving them.
export const toolsForKind = (kind) => CATALOG
  .filter(([, , folderOnly]) => kind === "folder" || !folderOnly).map(([key]) => key);

// The reading permissions (the web included) and the changing ones: whether
// a chat arms any tool of either kind.
export const READ_TOOLS = PERMISSION_KEYS.filter((key) => GROUP[key] !== "edit");
export const WRITE_TOOLS = PERMISSION_KEYS.filter(changesLibrary);

// Presets, from least to most the assistant may do without asking.
const PRESETS = ["library", "read", "ask", "all"];
function presetState(preset, key) {
  const group = GROUP[key];
  if (group === "library") return "allow";
  if (group === "web") return preset === "library" ? "off" : "allow";
  return { library: "off", read: "off", ask: "ask", all: "allow" }[preset];
}

export function presetPermissions(kind, preset) {
  return Object.fromEntries(toolsForKind(kind).map((key) => [key, presetState(preset, key)]));
}

// The preset a chat kind's permissions match, else "custom".
export function permissionPreset(kind, permissions = {}) {
  const keys = toolsForKind(kind);
  return PRESETS.find((preset) => keys.every((key) => permState(permissions, key) === presetState(preset, key)))
    || "custom";
}
