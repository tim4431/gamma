// Session persistence localStorage wrapper.
// Saves viewer layout state so bare `/` restores the last workspace.

// One saved session per account and workspace. Never read the old unscoped
// cache: it may belong to another account that used this browser.
let scope = "";
const STORAGE_KEY = () => `gamma-session:${scope}`;

export function setSessionScope(user, ws) {
  clearTimeout(saveTimer);
  pendingWrite = null; // it belonged to the scope being left
  scope = user && ws ? `${user}@${ws}` : "";
}

// Fields to persist. Add new ones here and they'll auto-save + restore.
const SESSION_FIELDS = [
  "focusedBlockId",
  "pdfScale",
  "orientation",
  "pdfHidden",
  "notesVisible",
  "sidebarWidth",
  "sidebarHeight",
  "pdfPageNumber",
];

let saveTimer = null;
let pendingWrite = null; // the debounced write, run early when the page is leaving

// The debounce would lose a change made within 300 ms of a reload or a
// navigation away: write it now instead.
export function flushSession() {
  if (!pendingWrite) return;
  clearTimeout(saveTimer);
  const write = pendingWrite;
  pendingWrite = null;
  write();
}
if (typeof window !== "undefined") window.addEventListener("pagehide", flushSession);

export function loadSession() {
  if (!scope) return {};
  try {
    const raw = localStorage.getItem(STORAGE_KEY());
    if (!raw) return {};
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

export function saveSession(state) {
  clearTimeout(saveTimer);
  pendingWrite = null;
  if (!scope) return;
  const write = () => {
    pendingWrite = null;
    try {
      const merged = { ...loadSession(), ...state };
      // Only keep known fields
      const pruned = {};
      for (const k of SESSION_FIELDS) {
        if (k in merged) pruned[k] = merged[k];
      }
      localStorage.setItem(STORAGE_KEY(), JSON.stringify(pruned));
    } catch {
      // localStorage full or blocked; silently ignore
    }
  };
  pendingWrite = write;
  saveTimer = setTimeout(write, 300);
}

export function clearSession() {
  clearTimeout(saveTimer);
  pendingWrite = null;
  if (!scope) return;
  try {
    localStorage.removeItem(STORAGE_KEY());
  } catch {}
}
