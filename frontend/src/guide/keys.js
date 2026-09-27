// Keys in guide copy: `{key:<command id>}` names a catalog command and
// shows the chord it answers to for this account — rebound in Settings →
// Keyboard, ⌘ only on a Mac; `{key:<chord>}` names a fixed key ("Tab",
// "\"). The overlay draws either as key caps; an unbound command falls back
// to its palette name in quotes. The token stays literal in the i18n key, so
// a translation keeps it (docs/dev/onboarding.md, docs/dev/i18n.md).
import { commandById, commandChord } from "../app/commands.js";
import { chordLabel, normalizeChord } from "../shared/lib/hotkeys.js";

export const KEY_TOKEN = /\{key:([^{}\s]+)\}/g;
const COMMAND_ID = /^[a-z]+\.[A-Za-z]+$/;

// What `{key:name}` shows under these bindings: { chord } to draw as caps,
// { text } for an unbound command, or null when the name is neither a
// catalog command nor a chord (the tests keep tour copy from doing that).
export function resolveKey(name, bindings) {
  if (COMMAND_ID.test(name)) {
    const cmd = commandById(name);
    if (!cmd) return null;
    const chord = commandChord(name, bindings);
    return chord ? { chord } : { text: `“${cmd.label}”` };
  }
  const chord = normalizeChord(name);
  return chord ? { chord } : null;
}

// The same copy as plain text ("Ctrl+P", "⌘P"), for an aria-label.
export function keyText(text, bindings) {
  return String(text ?? "").replace(KEY_TOKEN, (token, name) => {
    const key = resolveKey(name, bindings);
    return key ? key.text || chordLabel(key.chord) : token;
  });
}

// Every `{key:…}` name in a piece of copy.
export const keyNames = (text) => [...String(text ?? "").matchAll(KEY_TOKEN)].map((m) => m[1]);
