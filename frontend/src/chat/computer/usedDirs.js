// The directories this browser's conversations with a computer's agent
// began in, the newest first ("" for a directory of its own), so a new
// conversation starts where the last one did and the composer's folder
// chip lists the folders used last first (computerChat.workPlace), as Codex
// does with its projects. Ids of offered directories (GET /api/local-agents'
// `dirs`, or another computer's), which name nothing on another server, so
// they stay in the browser like the chat model; one not offered any more is
// passed over. Read once for the window, written as a conversation begins.
const KEY = "gamma-agent-dirs-used";
const MAX = 20;

let held = null;

export function usedDirs() {
  if (held) return held;
  try {
    const stored = JSON.parse(localStorage.getItem(KEY) || "[]");
    held = Array.isArray(stored) ? stored.filter((id) => typeof id === "string").slice(0, MAX) : [];
  } catch {
    held = [];
  }
  return held;
}

export function noteDirUsed(id) {
  held = [id, ...usedDirs().filter((d) => d !== id)].slice(0, MAX);
  try { localStorage.setItem(KEY, JSON.stringify(held)); } catch { /* kept for this window alone */ }
}
