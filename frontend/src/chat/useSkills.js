// The account's skills (GET /api/ai/skills: every built-in and every skill
// of the account's, without their instructions, and the server's limits on
// them; docs/dev/skills.md), read once for the window and shared by every
// reader: the composer's "/" picker, the split of a message as it is sent
// (chat/skills.js) and Settings → Chat's skills sections. Read again when
// the window comes back (Settings in another window may have changed them)
// and by refreshSkills() after each write those sections make. Each read is numbered and only
// the newest lands; what is held is kept with the account it was read for,
// so another account signing in on this tab starts unknown, and a share
// link reads nothing. Until a first read landed the catalog is unknown
// (`ready` false): "/" opens no picker and a "/word" goes as text.
import { useEffect, useMemo, useSyncExternalStore } from "react";
import { API, apiJson, getExpectedUser, getShareToken } from "../shared/lib/utils";

const NO_ROWS = Object.freeze([]);
let held = null;      // {account, rows, limits}: the last listing that landed
let failed = null;    // {account, message}: why the last read failed, null once one landed
let asked = 0;        // the newest read's number
let onItsWay = null;  // the account the newest read is for, while it is on its way
let readers = 0;      // mounted readers, which share one focus listener
const heard = new Set();
const tell = () => { for (const fn of heard) fn(); };
const accountNow = () => getExpectedUser() || "";

// Reads the listing again (a read already on its way is superseded);
// resolves to its rows, null when it failed, was superseded or the
// account changed meanwhile.
export function refreshSkills() {
  if (getShareToken()) return Promise.resolve(null);
  const account = accountNow();
  const n = ++asked;
  onItsWay = account;
  const current = () => n === asked && accountNow() === account;
  return apiJson(`${API}/ai/skills`).then((answer) => {
    if (!current()) return null;
    held = { account, rows: Array.isArray(answer?.skills) ? answer.skills : NO_ROWS, limits: answer?.limits || null };
    failed = null;
    tell();
    return held.rows;
  }, (err) => {
    if (!current()) return null;
    failed = { account, message: err?.message || String(err) };
    tell();
    return null;
  }).finally(() => { if (n === asked) onItsWay = null; });
}

// The skills a message can run now: those the listing marks effective (an
// account's, and the built-ins neither turned off nor replaced by one of
// the account's of the same name), in its order.
export const effectiveSkills = (rows) => (rows || NO_ROWS).filter((row) => row.effective);

const again = () => { if (document.visibilityState !== "hidden") refreshSkills(); };
const subscribe = (fn) => { heard.add(fn); return () => heard.delete(fn); };
const snapshot = () => held;
const failure = () => failed;

// {rows, ready, error, limits}: the listing's rows for this account (every
// built-in and account skill, effective or not), whether a read of it
// landed, why the last read failed ("" once one landed), and the server's
// limits ({name, description, hint, body, import, skills}: characters, the
// bytes of a SKILL.md, the skills an account keeps; null until a read
// landed).
export function useSkills() {
  const now = useSyncExternalStore(subscribe, snapshot);
  const fail = useSyncExternalStore(subscribe, failure);
  const share = !!getShareToken();
  const account = accountNow();
  useEffect(() => {
    if (share) return undefined;
    if (held?.account !== account && onItsWay !== account) refreshSkills();
    if (!readers++) window.addEventListener("focus", again);
    return () => { if (!--readers) window.removeEventListener("focus", again); };
  }, [share, account]);
  const mine = !share && now?.account === account ? now : null;
  const error = !share && fail?.account === account ? fail.message : "";
  return useMemo(() => ({ rows: mine?.rows || NO_ROWS, ready: !!mine, error, limits: mine?.limits || null }), [mine, error]);
}
