// Pure permission policy shared by preferences, Settings and the chat shortcut.
// Keep defaults and legacy decoding aligned with the backend permission policy.
export const LIBRARY_READ_TOOLS = ["list", "read", "block_read", "view", "search"];
export const READ_TOOLS = [...LIBRARY_READ_TOOLS, "web_search", "web_read"];
export const WRITE_TOOLS = ["rename", "move", "block_edit"];
export const TOOL_PERMISSION_DEFAULTS = Object.freeze(Object.fromEntries([
  ...READ_TOOLS.map((key) => [key, "allow"]),
  ["publisher_cookies", "ask"],
  ...WRITE_TOOLS.map((key) => [key, "ask"]),
]));

const isMap = (value) => !!value && typeof value === "object" && !Array.isArray(value);

// Accept either a permission value or a permissions map. Missing values use the
// tool's default; explicit malformed values and unknown tools fail closed.
export function permissionFor(key, valueOrPermissions) {
  if (!Object.hasOwn(TOOL_PERMISSION_DEFAULTS, key)) return "deny";
  const value = isMap(valueOrPermissions) ? valueOrPermissions[key] : valueOrPermissions;
  if (value === undefined) return TOOL_PERMISSION_DEFAULTS[key];
  if (value === true) return "allow";
  if (value === false) return "deny";
  return ["allow", "ask", "deny"].includes(value) ? value : "deny";
}

export function normalizePermissions(value = {}) {
  return Object.fromEntries(Object.keys(TOOL_PERMISSION_DEFAULTS)
    .map((key) => [key, permissionFor(key, isMap(value) ? value : null)]));
}

export const toolsForKind = (kind) => kind === "folder"
  ? Object.keys(TOOL_PERMISSION_DEFAULTS)
  : [...READ_TOOLS.filter((key) => key !== "list"), "publisher_cookies", "block_edit"];

export function presetPermissions(kind, preset) {
  return Object.fromEntries(toolsForKind(kind).map((key) => {
    if (preset === "edit") return [key, "allow"];
    if (preset === "library") return [key, LIBRARY_READ_TOOLS.includes(key) ? "allow" : "deny"];
    if (preset === "read" && WRITE_TOOLS.includes(key)) return [key, "deny"];
    return [key, TOOL_PERMISSION_DEFAULTS[key]];
  }));
}

export function permissionPreset(kind, permissions = {}) {
  for (const preset of ["ask", "read", "edit", "library"]) {
    const values = presetPermissions(kind, preset);
    if (Object.entries(values).every(([key, value]) => permissionFor(key, permissions) === value)) return preset;
  }
  return "custom";
}
