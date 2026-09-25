// Keyboard shortcuts, the pure part (docs/dev/hotkeys.md): a chord is a
// string in CodeMirror's spelling — "Mod-Shift-k", "Alt-ArrowUp", "F2" —
// where Mod is Ctrl on Windows/Linux and ⌘ on a Mac. A command declares its
// default chord(s); the account's `keybindings` preference overrides them
// per command id (a string rebinds, null unbinds). Dispatch runs a catalog
// against one keydown; the Settings pane records chords with the same
// reader (chordFromEvent) the dispatcher matches with, so what it shows is
// what will fire. No DOM, no React: node --test covers it.

const MOD_ORDER = ["Mod", "Ctrl", "Alt", "Shift"];

// The physical key behind a keydown, by `code`, so a chord means the same
// key whatever the layout or the Shift state: Ctrl+Shift+[ is the "[" key
// with Shift held, not "{" (which is what `key` reports).
const CODE_KEYS = {
  BracketLeft: "[", BracketRight: "]", Slash: "/", Backslash: "\\", Comma: ",",
  Period: ".", Semicolon: ";", Quote: "'", Minus: "-", Equal: "=", Backquote: "`",
  Space: "Space", Enter: "Enter", NumpadEnter: "Enter", Tab: "Tab", Escape: "Escape",
  Backspace: "Backspace", Delete: "Delete", Insert: "Insert", Home: "Home", End: "End",
  PageUp: "PageUp", PageDown: "PageDown",
  ArrowUp: "ArrowUp", ArrowDown: "ArrowDown", ArrowLeft: "ArrowLeft", ArrowRight: "ArrowRight",
};

export const IS_MAC = typeof navigator !== "undefined"
  && /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgentData?.platform || "");

// The key part of a chord in its canonical spelling: letters lowercase,
// named keys as the DOM names them.
function canonicalKey(key) {
  if (!key) return "";
  if (key.length === 1) return key.toLowerCase();
  const lower = key.toLowerCase();
  if (lower === "esc") return "Escape";
  if (lower === "space" || key === " ") return "Space";
  if (/^f\d{1,2}$/.test(lower)) return lower.toUpperCase();
  const named = Object.values(CODE_KEYS).find((k) => k.toLowerCase() === lower);
  return named || key;
}

// A canonical chord's modifiers and key ("Mod--" is Mod plus the minus key).
function splitChord(c) {
  const key = c.endsWith("--") ? "-" : c.slice(c.lastIndexOf("-") + 1);
  const head = c.slice(0, c.length - key.length - (c.length > key.length ? 1 : 0));
  return { mods: head ? head.split("-") : [], key };
}

// "shift-mod-K" → "Mod-Shift-k". Invalid input → "".
export function normalizeChord(chord) {
  if (typeof chord !== "string" || !chord.trim()) return "";
  const parts = chord.trim().split("-");
  // A trailing "-" means the minus key: "Mod--" splits into ["Mod", "", ""].
  const key = parts[parts.length - 1] === "" ? "-" : parts[parts.length - 1];
  const mods = new Set();
  for (const p of parts.slice(0, parts[parts.length - 1] === "" ? -2 : -1)) {
    const m = { mod: "Mod", cmd: "Mod", meta: "Mod", ctrl: "Ctrl", control: "Ctrl", alt: "Alt", option: "Alt", shift: "Shift" }[p.toLowerCase()];
    if (!m) return "";
    mods.add(m);
  }
  const k = canonicalKey(key);
  if (!k) return "";
  return [...MOD_ORDER.filter((m) => mods.has(m)), k].join("-");
}

// The chord a keydown event is, or "" for a bare modifier press.
export function chordFromEvent(e, mac = IS_MAC) {
  const mods = [];
  if (mac ? e.metaKey : e.ctrlKey) mods.push("Mod");
  if (mac && e.ctrlKey) mods.push("Ctrl");
  if (e.altKey) mods.push("Alt");
  if (e.shiftKey) mods.push("Shift");
  const code = e.code || "";
  let key;
  if (/^Key[A-Z]$/.test(code)) key = code[3].toLowerCase();
  else if (/^Digit\d$/.test(code)) key = code[5];
  else if (CODE_KEYS[code]) key = CODE_KEYS[code];
  else if (/^F\d{1,2}$/.test(code)) key = code;
  else key = canonicalKey(e.key);
  if (!key || ["Control", "Meta", "Alt", "Shift", "AltGraph", "CapsLock", "Dead", "Unidentified"].includes(key)) return "";
  return [...mods, key].join("-");
}

export function matchesChord(e, chord, mac = IS_MAC) {
  const c = normalizeChord(chord);
  return !!c && chordFromEvent(e, mac) === c;
}

const KEY_LABELS = {
  ArrowUp: "↑", ArrowDown: "↓", ArrowLeft: "←", ArrowRight: "→", Escape: "Esc",
  Delete: "Del", PageUp: "PgUp", PageDown: "PgDn",
};
const MAC_KEY_LABELS = { ...KEY_LABELS, Backspace: "⌫", Delete: "⌦", PageUp: "⇞", PageDown: "⇟", Enter: "↩" };
const MAC_MODS = { Mod: "⌘", Ctrl: "⌃", Alt: "⌥", Shift: "⇧" };
const PC_MODS = { Mod: "Ctrl", Ctrl: "Ctrl", Alt: "Alt", Shift: "Shift" };

// The parts a chord is shown as: ["Ctrl", "Shift", "K"] / ["⇧", "⌘", "K"].
// Macs order the glyphs ⌃⌥⇧⌘ the way the system menus do.
export function chordParts(chord, mac = IS_MAC) {
  const c = normalizeChord(chord);
  if (!c) return [];
  const { mods, key } = splitChord(c);
  const order = mac ? ["Ctrl", "Alt", "Shift", "Mod"] : MOD_ORDER;
  const table = mac ? MAC_MODS : PC_MODS;
  const labels = mac ? MAC_KEY_LABELS : KEY_LABELS;
  const label = labels[key] || (key.length === 1 ? key.toUpperCase() : key);
  return [...order.filter((m) => mods.includes(m)).map((m) => table[m]), label];
}

// "Ctrl+Shift+K" / "⇧⌘K".
export function chordLabel(chord, mac = IS_MAC) {
  const parts = chordParts(chord, mac);
  return mac ? parts.join("") : parts.join("+");
}

// The chords a command answers to under these bindings: the override when
// the preference names the command (null → none), else its defaults.
export function effectiveKeys(command, bindings) {
  if (bindings && Object.prototype.hasOwnProperty.call(bindings, command.id)) {
    const b = bindings[command.id];
    return b ? [normalizeChord(b)].filter(Boolean) : [];
  }
  const keys = Array.isArray(command.keys) ? command.keys : command.keys ? [command.keys] : [];
  return keys.map(normalizeChord).filter(Boolean);
}

// A chord that would replace typing (a bare letter, digit or punctuation)
// cannot be bound; function keys and anything with a modifier can.
export function bindable(chord) {
  const c = normalizeChord(chord);
  if (!c) return false;
  const { mods, key } = splitChord(c);
  return mods.length > 0 || /^F\d{1,2}$/.test(key);
}

// Run the first command of `commands` whose chord matches the event and
// whose `when(ctx)` (if any) holds; a `run` returning false declines and
// the search goes on. Returns the command that handled the event (its
// default is prevented and propagation stopped), or null. `edits` commands
// are skipped while ctx.readOnly.
export function dispatch(commands, e, ctx, bindings, mac = IS_MAC) {
  if (!e || e.isComposing || e.keyCode === 229) return null;
  const chord = chordFromEvent(e, mac);
  if (!chord) return null;
  for (const cmd of commands) {
    if (!effectiveKeys(cmd, bindings).includes(chord)) continue;
    if (cmd.edits && ctx?.readOnly) continue;
    if (cmd.when && !cmd.when(ctx)) continue;
    if (cmd.run(ctx, e) === false) continue;
    e.preventDefault?.();
    e.stopPropagation?.();
    return cmd;
  }
  return null;
}

// chord → the commands bound to it, for every chord more than one command
// answers to. Same-scope collisions are real conflicts; a block chord that
// shadows an app chord only applies while an editor is open. The pane flags
// both kinds alike.
export function conflicts(commands, bindings) {
  const byChord = new Map();
  for (const cmd of commands) {
    for (const k of effectiveKeys(cmd, bindings)) {
      if (!byChord.has(k)) byChord.set(k, []);
      byChord.get(k).push(cmd);
    }
  }
  const out = new Map();
  for (const [k, list] of byChord) if (list.length > 1) out.set(k, list);
  return out;
}
