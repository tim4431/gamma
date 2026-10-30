// The two kinds of sync link a workspace can have, where their API lives,
// and the readings only a Zotero link has. A clone (or a publication) is a
// mirror of a workspace on another Gamma server (/api/mirrors,
// docs/dev/mirror.md); a Zotero link keeps a personal workspace in step
// with one Zotero library (/api/zotero/links, docs/dev/zotero_sync.md).
// The pill, its popover, the conflict cards and Settings' Clones and
// Zotero links section serve both, asking this module which URL to call,
// what the actions both surfaces offer are titled, when a round follows an
// edit, which workspaces a new clone or link may go into, and, for a link,
// what a force and "If a page differs" say, what its new workspace is
// named, what its settings for sending PDFs and for its cadence say, what
// its error, its progress and queue, its unsent edits, its log's actions,
// what waits on something outside Gamma, its key's limits and a page's PDF
// in Zotero mean. Pure, so node tests it (tests/syncLinks.test.mjs);
// MirrorPopover.jsx puts the icons on.
import { fmtBytes } from "../shared/lib/format.js";
import { T, t, tn } from "../shared/i18n/i18n.js";

export const LINK_BASES = { mirror: "/mirrors", zotero: "/zotero/links" };

// A Zotero link's Live cadence, its `poll_s` (the backend's LIVE_POLL_S):
// the streaming API says when something changed.
export const ZOTERO_LIVE_S = 5;

// How quiet a Zotero link's workspace stays after an edit before the
// round "Sync after an edit" asks for (gamma/zotero_sync_rows.py
// DEBOUNCE_S; a clone's is a second, gamma/sync_engine.py DEBOUNCE_S), and
// how long a page stays still before the link sends its "Notes from Gamma"
// note, new or changed, whichever round comes (gamma/zotero_sync_blocks.py
// NOTES_QUIET_S).
export const ZOTERO_DEBOUNCE_S = 3;
export const ZOTERO_NOTES_QUIET_S = 60;

// The titles of what both the pill's settings and Settings' row do with a
// link, by its kind (linkKind), for t(): one wording per kind on both. A
// Zotero link's force takes the forced side's trash to the other side
// (gamma/zotero_sync_items.py, zotero_sync_blocks.py): a pull sends to
// Recently deleted the pages whose items are in Zotero's trash and deletes
// from their pages the notes and annotations Zotero trashed, a push sends
// to Zotero's trash the items whose pages are in Recently deleted. A push
// still takes Zotero's version where Gamma's would lose something there
// (ZOTERO_ADOPT_LIMITS).
export const LINK_ACTIONS = {
  mirror: {
    forcePull: T("Make this clone identical to the remote (only differing pages are written)"),
    forcePush: T("Make the remote identical to this clone (only differing pages are written)"),
    pushBlocked: T("A receive-only clone cannot force push"),
    detach: T("Stop pulling and pushing for now; origin is kept, so reattaching merges what both sides did meanwhile"),
    reattach: T("Follow the remote again; what both sides did meanwhile merges"),
    receiveNow: T("Receive the remote's changes now"),
    remove: T("Remove origin"),
    removeTitle: T("Forget the origin for good; the workspace stays as an ordinary one"),
  },
  zotero: {
    forcePull: T("Take Zotero's version of everything both sides have; a page whose item is in Zotero's trash goes to Recently deleted, and a note or annotation Zotero trashed is deleted here"),
    forcePush: T("Send this workspace's version of everything both sides have to Zotero, except where Zotero's note or PDF holds what Gamma cannot write back; an item whose page is in Recently deleted goes to Zotero's trash"),
    pushBlocked: T("A receive-only link cannot force push"),
    detach: T("Stop syncing for now; the link is kept, so reattaching merges what both sides did meanwhile"),
    reattach: T("Sync with Zotero again; what both sides did meanwhile merges"),
    receiveNow: T("Receive Zotero's changes now"),
    remove: T("Remove link"),
    removeTitle: T("End the link for good; the workspace and the library both stay"),
  },
};

// Where Gamma's side does not win though it is the side taken (a force
// push, a first link's Keep Gamma's): a note whose Zotero copy holds what
// Gamma cannot write back takes Zotero's (gamma/zotero_sync_blocks.py,
// zotero_sync_items.py _note_merge), and so does a PDF whose Zotero file
// holds annotations Gamma cannot carry over to its own (another member's,
// refused, waiting or not shown here; the "fetch" of
// gamma/zotero_sync_pdfs.py's plan for a first link's or a force's pair).
export const ZOTERO_ADOPT_LIMITS = T("Zotero's version still wins, replacing Gamma's here, for a note holding what Gamma cannot write back (citations, tables, colours…) and for a PDF whose Zotero annotations Gamma cannot carry over.");

// What a Zotero link's force asks before it runs: the pill's inline Yes /
// No, without names, and Settings' dialog, naming the workspace (`name`)
// and the library (`library`). A pull also deletes here the notes and
// annotations Zotero trashed, edited here or not, and Recently deleted
// does not keep them (gamma/zotero_sync_blocks.py _note_trashed,
// _annotation_trashed); it keeps a page 30 days (gamma/trash.py
// KEEP_DAYS). A force writes the differences it decides as resolved
// conflict rows, which Conflicts does not list; the log counts them
// ("replaced here: 212 records, 31 notes"). Gamma keeps no earlier
// version of a page (no page history, and the force's writes are no edit
// the page's undo takes back), so what a force replaced or deleted here
// comes back only from a workspace backup made before. A push keeps
// Zotero's PDF it writes over here, in an open zotero_file conflict
// (gamma/zotero_sync_pdfs.py _hold_copy).
export function zoteroForceConfirm(direction, { name = "", library = "" } = {}) {
  if (direction === "pull") {
    return name
      ? t("Take {library}'s version of everything “{m}” and Zotero both have? A page whose item is in Zotero's trash goes to Recently deleted, where you can restore it for 30 days; a note or annotation Zotero trashed is deleted here, even one edited here. What it replaces here is counted in the log, not listed under Conflicts, and Gamma keeps no earlier version: only a backup made before brings it back.", { library, m: name })
      : t("Take Zotero's version of everything both sides have? A page whose item is in Zotero's trash goes to Recently deleted, where you can restore it for 30 days; a note or annotation Zotero trashed is deleted here, even one edited here. What it replaces here is counted in the log, not listed under Conflicts, and Gamma keeps no earlier version: only a backup made before brings it back.");
  }
  const ask = name
    ? t("Send “{m}”'s version of everything it and {library} both have to Zotero? An item whose page is in Recently deleted goes to Zotero's trash.", { library, m: name })
    : t("Send this workspace's version of everything both sides have to Zotero? An item whose page is in Recently deleted goes to Zotero's trash.");
  return `${ask} ${t(ZOTERO_ADOPT_LIMITS)} ${t("What it replaces is counted in the log, not listed under Conflicts, except a Zotero PDF it writes over, which is kept here, under Conflicts. Gamma keeps no earlier version: only a backup made before brings it back.")}`;
}

// The hint of a first link's "If a page differs" (Take Zotero's / Keep
// Gamma's), decided as a force decides: its conflict rows resolved and
// counted in the log, Zotero's PDF that Gamma's goes over kept, and Keep
// Gamma's limits.
export const zoteroAdoptHint = () =>
  `${t("Which side wins where a paper differs; the log counts what it replaces, and a Zotero PDF that Gamma's goes over is kept, under Conflicts.")} ${t(ZOTERO_ADOPT_LIMITS)}`;

// "Send PDFs to Zotero"'s words, the pill's settings' and Settings' link
// dialog's. It decides every PDF that goes up, a paper's first one and one
// replaced here alike (gamma/zotero_sync_pdfs.py may_send_files), but none
// goes over or beside a Zotero PDF that is a linked file or not in Zotero
// Storage (the plan parks it); off, a PDF replaced here and Zotero's stay
// apart, and their annotations wait.
export const ZOTERO_UPLOAD_HINT = T("a PDF added or replaced here goes to Zotero, unless the paper's PDF there is a linked file or not in Zotero Storage; off, it stays in Gamma");
export const ZOTERO_UPLOAD_TITLE = T("On: a paper whose item has no PDF gets this one, and a PDF replaced here goes over Zotero's or beside it; where Zotero's is a linked file or not in Zotero Storage, it stays in Gamma. Off: no PDF goes to Zotero; where the two differ, their annotations wait.");

// The words of a link's "Automatic sync": its hint, its tooltip and
// Manual's choice. Live is Zotero's stream (with a round every 30 s
// besides), 30 s and 5 min a check that often, Manual no schedule. Every
// round lists Zotero's changes (gamma/zotero_sync.py _round), and rounds
// come whatever the cadence on Sync, after an edit while "Sync after an
// edit" is on (gamma/sync_clock.py), and for what is still queued (a
// retry, a page's notes until it is still, a PDF waiting on its
// highlights), so Manual means no schedule of its own, not Sync alone.
export const ZOTERO_CADENCE_HINT = T("when Zotero's changes come in");
export const ZOTERO_CADENCE_TITLE = T("Live: Zotero tells Gamma as soon as something changes. 30 s, 5 min: Gamma asks Zotero that often. Manual: no schedule; Zotero's changes come with the next round, on Sync, after an edit (with Sync after an edit on) or for a change still queued.");
export const ZOTERO_MANUAL = T("No schedule: Zotero's changes come with the next round, such as Sync");

// "Sync after an edit"'s hint, by the link's kind: when the round comes
// after an edit, and, for a Zotero link, that a page's "Notes from Gamma"
// note waits until the page is still.
export function onChangeHint(kind) {
  if (linkKind(kind) !== "zotero") return t("a round about a second after you change something");
  return t("a round {debounce} s after your last change; a page's “Notes from Gamma” note goes once the page has been still for {quiet} s",
    { debounce: ZOTERO_DEBOUNCE_S, quiet: ZOTERO_NOTES_QUIET_S });
}

// "zotero" for a Zotero link's info (its `kind`) or the word itself, else
// "mirror": a mirror's rows carry no kind.
export const linkKind = (x) => (x === "zotero" || x?.kind === "zotero" ? "zotero" : "mirror");

// The API path of one link, below /api: `linkPath(info, ws, "/sync?wait=1")`.
export const linkPath = (kindOrInfo, ws, path = "") =>
  `${LINK_BASES[linkKind(kindOrInfo)]}/${encodeURIComponent(ws)}${path}`;

// The current workspace's link, from its /api/session row: "zotero" (a
// Zotero link that is not off), "mirror" (a clone or a publication), or "".
export const workspaceLink = (w) => (w?.zotero_of ? "zotero" : w?.mirror_of || w?.publishing ? "mirror" : "");

// The link the open workspace's sync pill shows: its session row's
// (workspaceLink), or `held` ({ws, kind}), a link detached from the pill
// itself, while the row (read again after the detach) lists it as
// detached: a detached link lists as an ordinary workspace, but the pill
// that detached it stays, in its detached state with Reattach, until the
// workspace is opened again or the link is removed.
export const pillLink = (w, held) => workspaceLink(w) || (held && w && held.ws === w.id
  && (held.kind === "zotero" ? w.zotero_detached : w.mirror_detached) ? held.kind : "");

// The session row once its link is forgotten (Remove link / Remove origin):
// an ordinary workspace, with no pill and no PDF a link brings
// (zoteroPdfSource), a detached link's `zotero_detached` or
// `mirror_detached` included; the row itself when it had no link.
export const linkGone = (w) => (w && (w.mirror_of || w.zotero_of || w.zotero_detached || w.mirror_detached)
  ? { ...w, mirror_of: "", zotero_of: "", zotero_detached: false, mirror_detached: false } : w);

// A Zotero link's log actions (its sync_log rows, the pill's log) in its
// words, for t(). A clone's are MirrorPopover.jsx's ACTION_TEXT. "kept
// here": Zotero trashed the collection of a folder that is shared or kept
// on disk, so the folder stays (gamma/zotero_sync_folders.py).
export const ZOTERO_LOG_ACTIONS = {
  "pulled": T("pulled from Zotero"),
  "pushed": T("pushed to Zotero"),
  "created here": T("pulled from Zotero (new page)"),
  "created there": T("pushed to Zotero (new item)"),
  "deleted here": T("trashed in Zotero — moved to Recently deleted here"),
  "deleted there": T("moved to Recently deleted here — trashed in Zotero"),
  "restored here": T("restored from Zotero (edited there after it was deleted here)"),
  "restored there": T("restored in Zotero (edited here after it was trashed there)"),
  "replaced here": T("Zotero's version replaced this one"),
  "replaced there": T("this version replaced Zotero's"),
  "kept here": T("kept here — in Zotero's trash, but the folder is shared or kept on disk"),
};

// The name the server gives the personal library (zotero_web.libraries).
const MY_LIBRARY = "My Library"; // i18n-ignore: the server's value, compared

// A Zotero library's name on screen: the personal one ("users/…") in the
// interface's language, as Zotero itself names it, a group by its own name.
// Takes a library row ({id, name, kind}) or a link's info ({library,
// library_name}, whose `name` is its workspace's); with neither id nor
// kind (a workspace's `zotero_of`, the name alone) the server's "My
// Library" stands for the personal one.
export function zoteroLibraryLabel({ id = "", library = "", name = "", library_name = "", kind = "" } = {}) {
  const ref = id || library || "";
  const label = (library ? library_name : name) || library_name || name || "";
  const personal = kind === "user" || ref.startsWith("users/") || (!ref && kind !== "group" && label === MY_LIBRARY);
  return personal ? t("My Library") : label;
}

// The name a new workspace of a link takes when the dialog leaves it
// empty, as gamma/zotero_sync.py create_link names it: "Zotero" for the
// personal library, a group by its own name. Takes a library row ({id,
// name, kind}).
const LINK_WORKSPACE = "Zotero"; // i18n-ignore: the server's default name
export const zoteroWorkspaceName = ({ id = "", name = "", kind = "" } = {}) =>
  (kind === "user" || String(id).startsWith("users/") ? LINK_WORKSPACE : name || LINK_WORKSPACE);

// The workspaces a new clone or link (`kind`) may go into: the account's
// personal ones that no clone, publication or Zotero link of the account
// holds, by their session rows (a live or detached clone or link, a
// publication with pages) and by the account's rows (`mirrors`, `links`;
// null while they load), which alone show a detached publication and an
// emptied one. A publication emptied of its pages keeps its row for the
// next publish, but gamma/workspaces.py treats it as none, and so does a
// Zotero link's create_link; a clone takes no workspace with any mirror
// row (gamma/sync_engine.py create_mirror).
export function linkCandidates(workspaces, { kind = "mirror", mirrors = [], links = [] } = {}) {
  const zotero = linkKind(kind) === "zotero";
  const holds = (m) => !zotero || !(Array.isArray(m.page_filter) && !m.page_filter.length);
  const held = new Set([...(mirrors || []).filter(holds), ...(links || [])].map((m) => m.workspace_id));
  return (workspaces || []).filter((w) => w.personal && !w.mirror_of && !w.mirror_detached && !w.publishing
    && !w.zotero_of && !w.zotero_detached && !held.has(w.id));
}

const count = (v) => Number(v || 0).toLocaleString();

// Whether a two-way link only receives because its key cannot write: the
// round's effective mode (`status.mode`, which follows a change of
// direction at once) is pull, or the key's access (`status.access`) says
// it cannot write, while the chosen mode is not pull.
export const pullByKey = (info) => info?.mode !== "pull"
  && (info?.status?.mode === "pull" || info?.status?.access?.write === false);

// What the link's key may not do in its library, from the last round's
// `status.access` ({write, files, notes}): a list of {tag, title}, the
// words of a tag beside the link's name and the sentence explaining it. A
// key that cannot write makes a two-way link receive only; one without
// notes access moves notes neither way. (A key without file access shows
// as the PDFs it holds back, zoteroWaitingState.)
export function zoteroLimits(info) {
  const out = [];
  if (pullByKey(info)) {
    out.push({ tag: t("receive only · your Zotero key cannot write"),
      title: t("Your Zotero key may read this library but not write to it; connect again with a key that writes to send your changes") });
  }
  if (info?.status?.access?.notes === false) {
    out.push({ tag: t("notes don't travel · your Zotero key cannot read notes"),
      title: t("Your Zotero key cannot read notes, so notes travel neither way: Zotero's stay there and this workspace's stay here. Connect again with a key that reads notes to sync them.") });
  }
  return out;
}

// When Zotero is next asked, in the words of the ok state's tooltip: Live
// (ZOTERO_LIVE_S) is the streaming API's word, a poll every so often, or,
// Manual, the next round, whatever starts it (ZOTERO_CADENCE_TITLE): an
// edit's only while "Sync after an edit" is on (`onChange`, the link's
// `on_change`).
export function zoteroCadence(pollS, { onChange = true } = {}) {
  if (pollS === ZOTERO_LIVE_S) return t(" Zotero tells Gamma when something changes.");
  if (pollS) return t(" Zotero is checked every {poll_s} s.", { poll_s: pollS });
  return onChange
    ? t(" Zotero is not checked on a schedule: its changes come with the next round, on Sync, after an edit or for a change still queued.")
    : t(" Zotero is not checked on a schedule: its changes come with the next round, on Sync or for a change still queued.");
}

// A link's failed round, read from `status.error_kind` (never from the
// sentence): {tone, icon, text, title, action?} for mirrorState's error
// branch, null when the last round had no error. `action` "reconnect" asks
// for the button that opens Settings › Account & sync › Zotero; `when` is
// the time of the attempt, for an error without a kind of its own.
export function zoteroError(info, when = "") {
  const s = info?.status || {};
  if (!s.last_error) return null;
  const error = s.last_error;
  switch (s.error_kind) {
    case "offline":
      return { tone: "warn", icon: "offline", text: t("Zotero unreachable · your edits wait here"),
        title: `${error}${t(" — your edits stay here and go to Zotero once it answers again.")}` };
    case "auth":
      return { tone: "error", icon: "key", action: "reconnect", text: t("Reconnect Zotero"),
        title: `${error}${t(" — connect your Zotero account again in Settings › Account & sync; the link resumes by itself.")}` };
    case "not_connected":
      return { tone: "error", icon: "key", action: "reconnect", text: t("Reconnect Zotero"),
        title: t("No Zotero account is connected: connect it in Settings › Account & sync, and the link resumes by itself.") };
    case "library_gone":
      return { tone: "error", icon: "alert", text: t("This key no longer reaches {library}", { library: zoteroLibraryLabel(info) }),
        title: `${error}${t(" — connect again with a key that reaches the library, or remove the link.")}` };
    case "busy":
      return { tone: "warn", icon: "clock", text: t("Zotero is busy · retrying"),
        title: `${error}${t(" — Gamma tries again by itself.")}` };
    default:
      return { tone: "error", icon: "alert", text: `${t("Sync problem")}${when ? ` · ${when}` : ""}`, title: error };
  }
}

// A failed round that only connecting again mends (a refused key, no
// account, a library the key no longer reaches): every later round fails
// the same way, so it reads before the conflicts, whose Resolve would hide
// it. Zotero unreachable or busy, Gamma tries again by itself.
const HALTING = new Set(["auth", "not_connected", "library_gone"]);
export const zoteroHalted = (status) => Boolean(status?.last_error) && HALTING.has(status?.error_kind);

// Whether a link's reading (mirrorState's {state, action}) asks only for a
// new connection, so Settings' row offers Reconnect in Sync's place: the
// error a new key mends. PDFs waiting for file access ask for one too, but
// records, notes and folders still sync, so Sync stays beside Reconnect.
export const reconnectOnly = (st) => st?.state === "error" && st?.action === "reconnect";

// Whether the info's local edits are on their way (the pill's "Syncing
// your edits…", its 2 s poll). A clone's `pending_local` says so. A Zotero
// link's counts a Gamma page queued whatever it waits for (its notes still
// being edited, a write's backoff), so it does only while the last round
// left nothing queued; otherwise those wait with the rest of the queue,
// the queued reading (zoteroQueued).
export function unsentEdits(info) {
  if (!info?.pending_local) return false;
  return linkKind(info) !== "zotero" || !(Number(info.status?.backlog) > 0);
}

// What a round at work is: a force (`status.forced`, until nothing of it is
// left), the first fill (`progress.phase`), or a sync.
function runLabel(s) {
  if (s.forced === "pull") return t("Force pull");
  if (s.forced === "push") return t("Force push");
  return s.progress?.phase === "first" ? t("First sync") : t("Syncing");
}

// Whether the next round follows at once: the last one stopped at its
// bounds with work still due (its `status.fill` carries the fill's counts
// to the next), so between the two the link still reads as syncing. Not
// once a restart cut the fill short (`status.interrupted`, until a round
// starts): nothing asks for that round any more.
export const zoteroFollowing = (status) => Boolean(status?.fill) && Number(status?.backlog) > 0
  && !status?.running && !status?.last_error && !status?.interrupted;

// Whether a Zotero link has never run: not detached, no round running and
// none ended (no `last_sync`, nor the `last_attempt` a round that ends on an
// error writes). Just after a link is made, the pill's first read can come
// before its first round has started.
export const zoteroUnstarted = (info) => Boolean(info) && !info.detached && info.mode !== "off"
  && !info.status?.running && !info.status?.last_sync && !info.status?.last_attempt;

// How often the pill reads a link (ms): every 2 s while a round runs
// (`running`), a local edit is on its way (`pending`) or a Zotero link has
// never run, so "Not synced yet" gives way to the first sync at once;
// else every 20 s. A link whose first round failed reads every 20 s.
export const LINK_POLL_FAST_MS = 2000;
export const LINK_POLL_MS = 20000;
export const linkPollMs = (kind, info, { running = false, pending = false } = {}) =>
  running || pending || (linkKind(kind) === "zotero" && zoteroUnstarted(info)) ? LINK_POLL_FAST_MS : LINK_POLL_MS;

// The pill's words while a link syncs: "First sync 300 / 2,000" as a round
// runs (`progress.done` counts the whole fill, across the bounded rounds
// it takes), "Syncing · 1,700 left" between two of them.
export function zoteroRunText(status) {
  const s = status || {};
  if (zoteroFollowing(s)) return t("{what} · {n} left", { what: runLabel(s), n: count(s.backlog) });
  const p = s.progress;
  return `${runLabel(s)}${p?.total ? ` ${count(p.done)} / ${count(p.total)}` : "…"}`;
}

// The PDFs a fill moves (`progress.bytes`: every transfer of the fill so
// far, and what is still queued): "PDFs 1.2 GB / 8.0 GB", "" when none.
export function zoteroBytesText(progress) {
  const b = progress?.bytes;
  if (!b || !(Number(b.total) > 0)) return "";
  return t("PDFs {done} / {total}", { done: fmtBytes(Number(b.done) || 0), total: fmtBytes(Number(b.total)) });
}

// Changes still queued (`status.backlog`) with no round running or
// following at once: each waits for a retry, a PDF, a page still being
// edited, or the next scheduled round. {text, title}, so the pill never
// says "Up to date" over them; null when nothing is queued, or a round
// takes them now. `when` is the last round's time.
export function zoteroQueued(info, when = "") {
  const s = info?.status || {};
  const n = Number(s.backlog) || 0;
  if (n <= 0 || s.running || zoteroFollowing(s)) return null;
  const force = s.forced === "pull" ? t(" A force pull is under way; it ends once nothing of it is left.")
    : s.forced === "push" ? t(" A force push is under way; it ends once nothing of it is left.") : "";
  return {
    text: tn("{n} change queued · {when}", "{n} changes queued · {when}", n, { n: count(n), when }),
    title: tn("{n} queued change waits for a later round: a retry, a PDF on its way, or a page still being edited.",
      "{n} queued changes wait for later rounds: a retry, a PDF on its way, or a page still being edited.", n, { n: count(n) }) + force,
  };
}

// Why an annotation waits: the engine's sentences (its rows'
// `detail.reason`, counted in `status.waiting.annotations.reasons`), named
// here so the catalog carries them...
export const ZOTERO_WAIT_REASONS = [
  T("the PDF is not here yet"), T("the PDFs differ"), T("being placed in the new PDF"), T("dashed in Gamma until accepted"),
  T("not found in the new PDF"), T("still drawn in the PDF"), T("not placed on the PDF"),
];
// ...and the two that are the link's ordinary course (a PDF not downloaded
// yet, one being placed in its new file): only the others ask for a look.
const WAITS_IN_COURSE = new Set(["the PDF is not here yet", "being placed in the new PDF"]); // i18n-ignore: the server's values, compared

// What waits on something outside Gamma (`status.waiting`), counted: PDFs
// by why they cannot come or go (`files`: a full storage at Zotero, with
// the time it is tried again, or here; a key without file access; linked
// files and files not in Zotero Storage; transfers that failed; `placing`,
// a PDF replaced here held back while its page's highlights are placed in
// it, gamma/zotero_sync_pdfs.py FILE_PLACING), the annotations by their
// reasons, the notes.
export function zoteroWaiting(status) {
  const w = status?.waiting || {};
  const f = w.files || {};
  const num = (v) => (Number.isFinite(v) ? v : 0);
  const reasons = {};
  const given = w.annotations?.reasons;
  if (given && typeof given === "object" && !Array.isArray(given)) {
    for (const [why, n] of Object.entries(given)) if (why && num(n) > 0) reasons[why] = n;
  }
  return {
    linked: num(f.linked), unavailable: num(f.unavailable), noAccess: num(f.no_access), quota: num(f.quota),
    storage: num(f.storage), failed: num(f.failed), placing: num(f.placing), annotations: num(w.annotations?.n), reasons,
    notes: num(w.notes),
    quotaUntil: w.quota_until || "",
  };
}

// The waits, one part each, those that ask for a look first (`warn`, with
// the pill's short `text`; `action` "reconnect" where a new key mends it):
// a full storage at Zotero, then here, a key without file access, PDFs
// Zotero does not serve, failed transfers, annotations held for a reason a
// person settles; then what waits in the ordinary course (a replaced PDF
// whose highlights are being placed, annotations whose PDF is not here
// yet or is being placed). Every part has its `sentence`, for the tooltip.
function waitingParts(w) {
  const parts = [];
  if (w.quota) {
    parts.push({ warn: true, text: tn("Zotero storage is full · {n} PDF waits", "Zotero storage is full · {n} PDFs wait", w.quota, { n: count(w.quota) }),
      sentence: w.quotaUntil
        ? t("Zotero's storage is full, so the PDFs it lacks are not sent. Gamma tries again after {time}, on Sync now, or when the link's settings change; more room is at zotero.org/storage.", { time: new Date(w.quotaUntil).toLocaleString() })
        : t("Zotero's storage is full, so the PDFs it lacks are not sent; Gamma tries again at the next round. More room is at zotero.org/storage.") });
  }
  if (w.storage) {
    parts.push({ warn: true, text: tn("Storage here is full · {n} PDF waits", "Storage here is full · {n} PDFs wait", w.storage, { n: count(w.storage) }),
      sentence: t("This server's storage is full, so PDFs from Zotero wait; they come once space frees.") });
  }
  if (w.noAccess) {
    parts.push({ warn: true, action: "reconnect",
      text: tn("{n} PDF waits for file access", "{n} PDFs wait for file access", w.noAccess, { n: count(w.noAccess) }),
      sentence: t("Your Zotero key cannot read this library's files, so its PDFs stay in Zotero: connect again with a key that has file access.") });
  }
  const missing = w.linked + w.unavailable;
  if (missing) {
    const which = [];
    if (w.linked) which.push(tn("{n} is a linked file", "{n} are linked files", w.linked, { n: count(w.linked) }));
    if (w.unavailable) which.push(tn("{n} is not in Zotero Storage (WebDAV, or not uploaded yet)", "{n} are not in Zotero Storage (WebDAV, or not uploaded yet)", w.unavailable, { n: count(w.unavailable) }));
    parts.push({ warn: true, text: tn("{n} PDF not in Zotero Storage", "{n} PDFs not in Zotero Storage", missing, { n: count(missing) }),
      sentence: t("{parts}: Gamma cannot download them, so they and their annotations stay in Zotero.", { parts: which.join(t(", ")) }) });
  }
  if (w.failed) {
    parts.push({ warn: true, text: tn("{n} PDF transfer failed", "{n} PDF transfers failed", w.failed, { n: count(w.failed) }),
      sentence: tn("{n} PDF could not be moved: Gamma tries a few times, then waits until it changes in Zotero.",
        "{n} PDFs could not be moved: Gamma tries a few times, then waits until they change in Zotero.", w.failed, { n: count(w.failed) }) });
  }
  if (w.placing) {
    parts.push({ sentence: tn("{n} PDF replaced here waits while its highlights are placed in it; then it goes to Zotero.",
      "{n} PDFs replaced here wait while their highlights are placed in them; then they go to Zotero.", w.placing, { n: count(w.placing) }) });
  }
  if (w.annotations) {
    const why = Object.entries(w.reasons).map(([reason, n]) => `${t(reason)} (${count(n)})`).join(t(", "));
    const sentence = why
      ? tn("{n} annotation waits: {why}.", "{n} annotations wait: {why}.", w.annotations, { n: count(w.annotations), why })
      : tn("{n} annotation waits.", "{n} annotations wait.", w.annotations, { n: count(w.annotations) });
    const asking = Object.entries(w.reasons).reduce((sum, [reason, n]) => sum + (WAITS_IN_COURSE.has(reason) ? 0 : n), 0);
    parts.push(asking ? { warn: true, text: tn("{n} annotation waits", "{n} annotations wait", asking, { n: count(asking) }), sentence } : { sentence });
  }
  if (w.notes) parts.push({ sentence: tn("{n} note waits.", "{n} notes wait.", w.notes, { n: count(w.notes) }) });
  return parts;
}

// The waiting reading (a warn note before "Up to date"): {text, title,
// action?} when something waits that asks for a look, else null. The text
// is the weightiest wait's, the title says everything that waits.
export function zoteroWaitingState(info) {
  const parts = waitingParts(zoteroWaiting(info?.status));
  const first = parts.find((p) => p.warn);
  if (!first) return null;
  const action = parts.find((p) => p.warn && p.action)?.action;
  return { text: first.text, title: parts.map((p) => p.sentence).join(" "), ...(action ? { action } : {}) };
}

// What waits in the link's ordinary course (a replaced PDF whose
// highlights are being placed, annotations whose PDF is not here yet or
// is being placed), as sentences for the tooltip of a link otherwise up to
// date; "" when nothing waits.
export const zoteroWaitingNote = (info) => waitingParts(zoteroWaiting(info?.status)).map((p) => p.sentence).join(" ");

// Why a page's PDF stays in Zotero, by the reason the engine writes in the
// page's `zotero_pdf` (docs/dev/zotero_sync.md); another reason is the
// server's own sentence.
export const ZOTERO_PDF_REASONS = {
  linked: T("a linked file in Zotero"),
  unavailable: T("not in Zotero Storage (WebDAV)"),
  files: T("your Zotero key cannot read files"),
  quota: T("the storage here is full"),
  failed: T("the download failed; it is tried again"),
};

// What a page's `zotero_pdf` property (a PDF Zotero holds and Gamma not
// yet: {key, state, reason, size, filename}) says: {state, reason, fetch},
// null without one. `fetch` is whether "Download now" can help: not while
// it downloads already, not for a file Gamma cannot download, but yes for
// one whose download failed (it is tried again at once).
export function zoteroPdfState(pdf) {
  if (!pdf || typeof pdf !== "object") return null;
  const state = pdf.state || "remote";
  const known = ZOTERO_PDF_REASONS[pdf.reason];
  const reason = known ? t(known) : pdf.reason ? t(String(pdf.reason)) : "";
  if (state === "unavailable") return { state, reason: reason || t(ZOTERO_PDF_REASONS.unavailable), fetch: pdf.reason === "failed" };
  if (state === "downloading") return { state, reason: "", fetch: false };
  return { state, reason, fetch: true };
}

// What brings a page's PDF in Zotero (its `zotero_pdf`), by the open
// workspace's session row: "zotero", its live link; "detached", its link
// detached (reattached, it brings it); "mirror", a clone, whose origin's
// link brings it; "mirror-detached", a clone detached (reattached, its
// origin brings it); "" nothing any more (the link or the origin was
// removed, the page came in from a backup). The server refuses an attach
// exactly while one of the first four holds (routers/pages.py
// _zotero_keeps), so the card takes the attach prompt's place then.
export const zoteroPdfSource = (w) => (w?.zotero_of ? "zotero" : w?.zotero_detached ? "detached"
  : w?.mirror_of ? "mirror" : w?.mirror_detached ? "mirror-detached" : "");

// The reasons a PDF cannot come now but may later (a key given file
// access, a download tried again, room made here): a copy of the page's
// own attached meanwhile is then a PDF replaced here, which, with "Send
// PDFs to Zotero" on, goes to Zotero once Zotero's file is read, Zotero's
// kept here in a zotero_file conflict (gamma/zotero_sync_pdfs.py, "both").
// A linked file or one not in Zotero Storage never comes, and nothing goes
// over it.
const PDF_LATER = new Set(["files", "failed", "quota"]); // i18n-ignore: the server's values, compared

// The card of a page's PDF in Zotero (ZoteroPage.jsx), for the attach
// popover: {takesOver, hint, title, coming, download, note}, null without one.
// It takes the attach prompt's place (`takesOver`) while something brings
// that PDF (the server then refuses another, ZOTERO_PDF_COMING); a PDF
// that cannot come (a linked file, WebDAV, no file access, a failed
// download) or that nothing brings sits above the prompt, so the page can
// take a copy of its own (`note`: whether it stays in Gamma). `coming` shows it
// downloading (`asked`: Download now was pressed), `download` offers
// Download now: a live link's, for a PDF that can come. `title` is the
// page's PDF button's tooltip while the card takes over.
export function zoteroPdfCard(pdf, { source = "", asked = false } = {}) {
  const st = zoteroPdfState(pdf);
  if (!st) return null;
  const takesOver = Boolean(source) && st.state !== "unavailable";
  const live = source === "zotero";
  const coming = live && (st.state === "downloading" || (asked && st.fetch));
  const failed = st.state === "unavailable" && st.fetch;
  const hint = st.state === "unavailable" && !st.fetch ? t("It cannot come here: {reason}.", { reason: st.reason })
    : source === "detached" ? t("The Zotero link is detached: reattach it to bring this PDF.")
    : source === "mirror" ? t("It comes from the workspace this one is a clone of, whose Zotero link brings it.")
    : source === "mirror-detached" ? t("This clone is detached: reattach it to bring this PDF from the workspace it copies.")
    : !live ? t("No Zotero link brings it here any more.")
    : coming ? t("Downloading…")
    : failed ? t("Its download failed; Gamma tries again by itself, or now.")
    : st.state === "queued" ? t("Queued: it comes after the PDFs before it.") : t("It comes with the next PDFs, or now.");
  const title = !takesOver ? ""
    : live ? t("PDF in Zotero: it comes here by itself, or download it now")
    : source === "mirror" ? t("PDF in Zotero: it comes here by itself, from the workspace this one is a clone of")
    : source === "mirror-detached" ? t("PDF in Zotero: it comes here once this clone is reattached")
    : t("PDF in Zotero: it comes here once the Zotero link is reattached");
  return {
    takesOver, hint, title, coming, download: live && st.fetch && !coming,
    note: takesOver ? t("Gamma takes Zotero's file, so the annotations on both sides stay on one PDF.")
      : source && PDF_LATER.has(pdf.reason)
        ? t("Attach your own copy below. With the link's Send PDFs to Zotero on, it goes to Zotero once Gamma can read Zotero's file, which is then kept here, under Conflicts; off, it stays in Gamma.")
        : t("Attach your own copy below: it stays in Gamma and is not sent to Zotero."),
  };
}

// The attach endpoints' 409 while a page waits for the PDF its Zotero item
// holds (gamma/routers/pages.py ZOTERO_PDF_COMING), which a client that
// did not know of it yet meets.
export const ZOTERO_PDF_COMING = T("This page's PDF is in Zotero and comes from there: use Download now on the page");
export const isZoteroPdfComing = (err) => err?.status === 409 && err?.message === ZOTERO_PDF_COMING;
