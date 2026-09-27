// The keys of a chord as <kbd> caps: "Ctrl" "Shift" "K", or ⇧⌘K on a Mac —
// the one rendering Settings → Keyboard and the guide's copy share
// (shared/lib/hotkeys.js chordParts decides the platform's spelling).
import { chordParts } from "../lib/hotkeys.js";

export function KeyCaps({ chord }) {
  return <span className="keyCaps">{chordParts(chord).map((part, i) => <kbd key={i} className="keyCap">{part}</kbd>)}</span>;
}
