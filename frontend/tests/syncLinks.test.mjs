// node --test tests/  (from frontend/) — the two kinds of sync link
// (src/collaboration/syncLinks.js): which API a clone and a Zotero link
// call, which link the open workspace has, and what a link's error, its
// progress and queue, what waits, its key's limits and a page's PDF in
// Zotero read as — against the words the engine writes.
import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import { test } from "node:test";
import {
  LINK_ACTIONS, LINK_BASES, ZOTERO_ADOPT_LIMITS, ZOTERO_CADENCE_HINT, ZOTERO_CADENCE_TITLE, ZOTERO_DEBOUNCE_S, ZOTERO_LIVE_S,
  ZOTERO_LOG_ACTIONS, ZOTERO_MANUAL, ZOTERO_NOTES_QUIET_S, ZOTERO_PDF_COMING, ZOTERO_PDF_REASONS, ZOTERO_UPLOAD_HINT, ZOTERO_UPLOAD_TITLE,
  ZOTERO_WAIT_REASONS, isZoteroPdfComing, linkCandidates, linkGone, linkKind, linkPath, linkPollMs, onChangeHint, pillLink, pullByKey,
  reconnectOnly, zoteroUnstarted,
  unsentEdits, workspaceLink, zoteroAdoptHint, zoteroBytesText, zoteroCadence, zoteroError, zoteroFollowing, zoteroForceConfirm,
  zoteroHalted, zoteroLibraryLabel, zoteroLimits, zoteroPdfCard, zoteroPdfSource, zoteroPdfState, zoteroQueued, zoteroRunText,
  zoteroWaiting, zoteroWaitingNote, zoteroWaitingState, zoteroWorkspaceName,
} from "../src/collaboration/syncLinks.js";

const GAMMA = new URL("../../backend/gamma/", import.meta.url);

// The engine's source: gamma/zotero_sync.py and whatever zotero_sync_*.py
// modules it is split into, and zotero_annotations.py, which holds the rule
// for when an annotation waits (hold_reason) and some of its reasons.
async function engineSource() {
  const names = (await readdir(GAMMA)).filter((name) => /^zotero_(sync.*|annotations)\.py$/.test(name)).sort();
  assert.ok(names.length, "the Zotero link's engine is there");
  return (await Promise.all(names.map((name) => readFile(new URL(name, GAMMA), "utf8")))).join("\n");
}

// A module-level constant of a backend module, as written there.
async function backendConstant(file, name) {
  const found = (await readFile(new URL(file, GAMMA), "utf8")).match(new RegExp(`^${name}\\s*=\\s*([0-9.]+)`, "m"));
  assert.ok(found, `${file} sets ${name}`);
  return Number(found[1]);
}

test("a mirror's row has no kind; a Zotero link's says so, and each calls its own API", () => {
  assert.equal(linkKind({ workspace_id: "w1", remote_url: "http://nas" }), "mirror");
  assert.equal(linkKind(undefined), "mirror");
  assert.equal(linkKind({ kind: "zotero" }), "zotero");
  assert.equal(linkKind("zotero"), "zotero");
  assert.equal(linkPath({}, "w 1", "/sync?wait=1"), "/mirrors/w%201/sync?wait=1");
  assert.equal(linkPath({ kind: "zotero" }, "w1"), "/zotero/links/w1");
  assert.equal(linkPath("zotero", "w1", "/conflicts/7"), "/zotero/links/w1/conflicts/7");
  assert.deepEqual(LINK_BASES, { mirror: "/mirrors", zotero: "/zotero/links" });
});

test("the open workspace's link comes from its session row", () => {
  assert.equal(workspaceLink({ zotero_of: "My Library" }), "zotero");
  assert.equal(workspaceLink({ mirror_of: "Lab" }), "mirror");
  assert.equal(workspaceLink({ publishing: true }), "mirror");
  assert.equal(workspaceLink({ zotero_of: "", mirror_of: "" }), "");
  assert.equal(workspaceLink(null), "");
  assert.equal(workspaceLink({ zotero_detached: true }), "", "a detached link lists as an ordinary workspace");
  assert.equal(workspaceLink({ mirror_detached: true }), "");
});

test("a link detached from its pill keeps the pill, detached, though the session row read again lists an ordinary workspace", () => {
  const zotero = { ws: "w", kind: "zotero" };
  const clone = { ws: "w", kind: "mirror" };
  // the row's own link, held or not
  assert.equal(pillLink({ id: "w", zotero_of: "My Library" }, null), "zotero");
  assert.equal(pillLink({ id: "w", mirror_of: "Lab" }, zotero), "mirror", "the row says best");
  assert.equal(pillLink({ id: "w", publishing: true }, null), "mirror");
  // detached from the pill: it stays, with Reattach, until the workspace is opened again
  assert.equal(pillLink({ id: "w", zotero_detached: true }, zotero), "zotero");
  assert.equal(pillLink({ id: "w", mirror_detached: true }, clone), "mirror");
  // opened detached (a reload, another tab, Settings): an ordinary workspace, no pill
  assert.equal(pillLink({ id: "w", zotero_detached: true }, null), "");
  assert.equal(pillLink({ id: "w", mirror_detached: true }, null), "");
  // held for another workspace, or another kind of link
  assert.equal(pillLink({ id: "v", zotero_detached: true }, zotero), "");
  assert.equal(pillLink({ id: "w", mirror_detached: true }, zotero), "");
  assert.equal(pillLink({ id: "w", zotero_detached: true }, clone), "");
  // the link removed (Remove link / Remove origin): the pill goes
  assert.equal(pillLink(linkGone({ id: "w", zotero_detached: true }), zotero), "");
  assert.equal(pillLink(linkGone({ id: "w", mirror_detached: true }), clone), "");
  assert.equal(pillLink(null, zotero), "");
});

test("My Library is named in the interface's language, a group by its own name", () => {
  assert.equal(zoteroLibraryLabel({ id: "users/123", name: "My Library", kind: "user" }), "My Library");
  assert.equal(zoteroLibraryLabel({ library: "users/9", library_name: "Whatever" }), "My Library");
  assert.equal(zoteroLibraryLabel({ id: "groups/5", name: "Lab", kind: "group" }), "Lab");
  assert.equal(zoteroLibraryLabel({ library: "groups/5", library_name: "Lab" }), "Lab");
  assert.equal(zoteroLibraryLabel({ library: "groups/5", library_name: "Lab", name: "Lab papers" }), "Lab", "a link's name is its workspace's");
  assert.equal(zoteroLibraryLabel({ name: "My Library" }), "My Library", "a workspace's zotero_of alone");
  assert.equal(zoteroLibraryLabel({ name: "My Library", kind: "group" }), "My Library", "a group named so keeps its name");
});

test("a two-way link whose key cannot write only receives; a key without notes access moves none", () => {
  assert.equal(pullByKey({ mode: "two-way", status: { mode: "pull" } }), true);
  assert.equal(pullByKey({ mode: "pull", status: { mode: "pull" } }), false, "chosen, not forced");
  assert.equal(pullByKey({ mode: "two-way", status: {} }), false);
  assert.equal(pullByKey({ mode: "two-way", status: { mode: "two-way" } }), false, "status.mode follows a PATCH at once");
  assert.equal(pullByKey({ mode: "two-way", status: { access: { write: false, files: true, notes: true } } }), true, "by the key's access");
  assert.deepEqual(zoteroLimits({ mode: "two-way", status: { mode: "two-way", access: { write: true, files: true, notes: true } } }), []);
  const limits = zoteroLimits({ mode: "two-way", status: { mode: "pull", access: { write: false, files: true, notes: false } } });
  assert.deepEqual(limits.map((l) => l.tag), ["receive only · your Zotero key cannot write", "notes don't travel · your Zotero key cannot read notes"]);
  assert.match(limits[1].title, /notes travel neither way/);
  assert.deepEqual(zoteroLimits({ mode: "pull", status: { mode: "pull", access: { write: false } } }), [], "receive only by choice is no limit");
  assert.match(zoteroCadence(ZOTERO_LIVE_S), /tells Gamma/);
  assert.equal(ZOTERO_LIVE_S, 5, "Live is the poll_s the backend calls LIVE_POLL_S");
  assert.match(zoteroCadence(300), /every 300 s/);
});

// The engine's behaviour behind these words is held by
// backend/tests/test_zotero_sync_words.py: a Manual link still rounds
// after an edit, and that round brings Zotero's changes.
test("Manual is no schedule, not only Sync: a round after an edit or for a queued change brings Zotero's changes too", () => {
  assert.equal(ZOTERO_CADENCE_HINT, "when Zotero's changes come in");
  const [live, polls, manual] = ZOTERO_CADENCE_TITLE.split(/(?<=\.) (?=30 s|Manual)/);
  assert.match(live, /^Live: Zotero tells Gamma/);
  assert.match(polls, /^30 s, 5 min: Gamma asks Zotero that often\.$/);
  assert.match(manual, /^Manual: no schedule; Zotero's changes come with the next round, on Sync, after an edit \(with Sync after an edit on\) or for a change still queued\.$/);
  assert.match(ZOTERO_MANUAL, /^No schedule: Zotero's changes come with the next round/);
  // the pill's tooltip names an edit's round only while Sync after an edit is on
  assert.match(zoteroCadence(0), /not checked on a schedule: its changes come with the next round, on Sync, after an edit or for a change still queued\.$/);
  assert.equal(zoteroCadence(0, { onChange: true }), zoteroCadence(0));
  assert.match(zoteroCadence(0, { onChange: false }), /on Sync or for a change still queued\.$/);
  assert.doesNotMatch(zoteroCadence(0, { onChange: false }), /edit/);
  for (const words of [ZOTERO_CADENCE_TITLE, ZOTERO_MANUAL, zoteroCadence(0), zoteroCadence(0, { onChange: false })]) {
    assert.doesNotMatch(words, /only when you (press Sync|sync)/i, words);
  }
});

test("Sync after an edit says when the round comes, a link's notes from Gamma later still, as the backend times them", async () => {
  assert.equal(ZOTERO_LIVE_S, await backendConstant("zotero_sync_rows.py", "LIVE_POLL_S"));
  assert.equal(ZOTERO_DEBOUNCE_S, await backendConstant("zotero_sync_rows.py", "DEBOUNCE_S"));
  assert.equal(ZOTERO_NOTES_QUIET_S, await backendConstant("zotero_sync_blocks.py", "NOTES_QUIET_S"));
  assert.equal(await backendConstant("sync_engine.py", "DEBOUNCE_S"), 1, "a clone's round: about a second");
  assert.equal(onChangeHint("mirror"), "a round about a second after you change something");
  assert.equal(onChangeHint({ workspace_id: "w" }), onChangeHint("mirror"), "a mirror's row has no kind");
  const link = onChangeHint({ kind: "zotero" });
  assert.equal(link, onChangeHint("zotero"));
  assert.match(link, new RegExp(`^a round ${ZOTERO_DEBOUNCE_S} s after your last change`));
  assert.match(link, new RegExp(`“Notes from Gamma” note goes once the page has been still for ${ZOTERO_NOTES_QUIET_S} s$`));
});

test("a new link's workspace is named as the server names it: Zotero for My Library, a group after the group", async () => {
  assert.equal(zoteroWorkspaceName({ id: "users/123", name: "My Library", kind: "user" }), "Zotero");
  assert.equal(zoteroWorkspaceName({ id: "users/123", name: "My Library" }), "Zotero", "by its id");
  assert.equal(zoteroWorkspaceName({ id: "groups/5", name: "Lab", kind: "group" }), "Lab");
  assert.equal(zoteroWorkspaceName({ id: "groups/5", name: "", kind: "group" }), "Zotero", "a group without a name");
  assert.equal(zoteroWorkspaceName(undefined), "Zotero");
  const engine = await readFile(new URL("zotero_sync.py", GAMMA), "utf8");
  assert.match(engine, /"Zotero" if lib\.get\("kind"\) == "user" else str\(lib\.get\("name"\) or "Zotero"\)/,
    "create_link's default name");
});

test("Into offers a workspace no clone, publication or link holds; a publication emptied of its pages holds none for a link", () => {
  const ws = (id, more = {}) => ({ id, name: id, personal: true, ...more });
  const workspaces = [
    ws("free"), ws("emptied"), ws("published", { publishing: true }), ws("clone", { mirror_of: "Lab" }), ws("detached-clone"),
    ws("linked", { zotero_of: "My Library" }), ws("detached-link"), ws("shared", { personal: false }),
  ];
  const mirrors = [
    { workspace_id: "emptied", page_filter: [] },
    { workspace_id: "published", page_filter: ["p1"] },
    { workspace_id: "clone", page_filter: null },
    { workspace_id: "detached-clone", mode: "off" },
  ];
  const links = [{ kind: "zotero", workspace_id: "linked" }, { kind: "zotero", workspace_id: "detached-link", mode: "off" }];
  const ids = (kind) => linkCandidates(workspaces, { kind, mirrors, links }).map((w) => w.id);
  assert.deepEqual(ids("zotero"), ["free", "emptied"], "gamma/workspaces.py treats an emptied publication as none");
  assert.deepEqual(ids("mirror"), ["free"], "a clone takes no workspace with a mirror row");
  // a publication emptied of its pages, its session row no longer publishing, before the mirrors are read again
  assert.deepEqual(linkCandidates([ws("emptied")], { kind: "zotero", mirrors: [{ workspace_id: "emptied", page_filter: [] }] })
    .map((w) => w.id), ["emptied"]);
  assert.deepEqual(linkCandidates(null, { kind: "zotero" }), []);
  assert.deepEqual(linkCandidates([ws("a")], { kind: "zotero", mirrors: null, links: null }).map((w) => w.id), ["a"]);
  // the account's rows still loading: the session rows alone keep out a detached clone or link and a publication
  const listed = [
    ws("free"), ws("detached-clone", { mirror_detached: true }), ws("detached-link", { zotero_detached: true }),
    ws("published", { publishing: true }), ws("clone", { mirror_of: "Lab" }), ws("linked", { zotero_of: "My Library" }),
  ];
  for (const kind of ["zotero", "mirror"]) {
    assert.deepEqual(linkCandidates(listed, { kind, mirrors: null, links: null }).map((w) => w.id), ["free"], kind);
  }
});

// The engine's behaviour behind these words is held by
// backend/tests/test_zotero_sync_words.py: a force pull deletes here a note
// Zotero trashed though it was edited here, and Recently deleted does not
// keep it; a force push takes Zotero's version of a note Gamma cannot write
// back and of a PDF whose annotations cannot move.
test("a force says where each side's trash goes, what it deletes here, where Gamma's does not win, and what brings it back", async () => {
  const pull = zoteroForceConfirm("pull");
  const push = zoteroForceConfirm("push");
  const keep = await backendConstant("trash.py", "KEEP_DAYS");
  assert.match(pull, /^Take Zotero's version of everything both sides have\?/);
  assert.match(pull, new RegExp(`A page whose item is in Zotero's trash goes to Recently deleted, where you can restore it for ${keep} days;`));
  assert.match(pull, /a note or annotation Zotero trashed is deleted here, even one edited here\./);
  assert.match(pull, /counted in the log, not listed under Conflicts/);
  assert.match(pull, /Gamma keeps no earlier version: only a backup made before brings it back\.$/);
  assert.match(push, /^Send this workspace's version of everything both sides have to Zotero\? An item whose page is in Recently deleted goes to Zotero's trash\. /);
  assert.ok(push.includes(` ${ZOTERO_ADOPT_LIMITS} `), "a push says where Zotero's still replaces Gamma's");
  assert.match(ZOTERO_ADOPT_LIMITS, /replacing Gamma's here, for a note holding what Gamma cannot write back \(citations, tables, colours…\) and for a PDF whose Zotero annotations Gamma cannot carry over\.$/);
  assert.match(push, /not listed under Conflicts, except a Zotero PDF it writes over, which is kept here, under Conflicts\./);
  assert.match(push, /Gamma keeps no earlier version: only a backup made before brings it back\.$/);
  const named = zoteroForceConfirm("pull", { name: "Papers", library: "My Library" });
  assert.match(named, /^Take My Library's version of everything “Papers” and Zotero both have\?/);
  assert.equal(named.slice(named.indexOf("?")), pull.slice(pull.indexOf("?")), "named or not, the same consequences");
  const namedPush = zoteroForceConfirm("push", { name: "Papers", library: "Lab" });
  assert.match(namedPush, /^Send “Papers”'s version of everything it and Lab both have to Zotero\?/);
  assert.equal(namedPush.slice(namedPush.indexOf("?")), push.slice(push.indexOf("?")));
  assert.match(LINK_ACTIONS.zotero.forcePull, /in Zotero's trash goes to Recently deleted, and a note or annotation Zotero trashed is deleted here$/);
  assert.match(LINK_ACTIONS.zotero.forcePush, /except where Zotero's note or PDF holds what Gamma cannot write back; an item whose page is in Recently deleted goes to Zotero's trash$/);
  // a first link's Keep Gamma's decides as a push does
  assert.match(zoteroAdoptHint(), /^Which side wins where a paper differs; the log counts what it replaces, and a Zotero PDF that Gamma's goes over is kept, under Conflicts\. /);
  assert.ok(zoteroAdoptHint().endsWith(` ${ZOTERO_ADOPT_LIMITS}`));
  // Conflicts lists the open rows only, and a force writes its decisions resolved
  const conflicts = await readFile(new URL("sync_engine.py", GAMMA), "utf8");
  assert.match(conflicts, /def list_conflicts\(ws: str, \*, resolved: bool = False/);
  // and no surface of the link says a force deletes nothing or lists what it replaces there
  const src = new URL("../src/", import.meta.url);
  for (const file of ["collaboration/syncLinks.js", "collaboration/MirrorPopover.jsx", "settings/SettingsMirrors.jsx", "settings/ZoteroLinkDialog.jsx"]) {
    const text = await readFile(new URL(file, src), "utf8");
    assert.ok(!/listed under Conflicts\.|is listed under Conflicts|Nothing is deleted on either side|nothing is deleted for good/i.test(text), file);
  }
});

// The engine's behaviour behind these words is held by
// backend/tests/test_zotero_sync_words.py: with the toggle on, a PDF
// replaced on a paper whose Zotero PDF is a linked file stays here.
test("Send PDFs to Zotero says which PDFs go and which stay in Gamma with it on", () => {
  assert.match(ZOTERO_UPLOAD_HINT, /^a PDF added or replaced here goes to Zotero, unless the paper's PDF there is a linked file or not in Zotero Storage; off, it stays in Gamma$/);
  assert.match(ZOTERO_UPLOAD_TITLE, /^On: a paper whose item has no PDF gets this one, and a PDF replaced here goes over Zotero's or beside it; where Zotero's is a linked file or not in Zotero Storage, it stays in Gamma\. Off: /);
});

test("an error reads by its kind, never by its sentence", () => {
  const info = (status) => ({ kind: "zotero", library: "groups/5", library_name: "Lab", status });
  assert.equal(zoteroError(info({})), null);
  // a refused key's sentence says "refused"; the mirror's regex called that unreachable
  const auth = zoteroError(info({ last_error: "Zotero refused the key: reconnect your account in Settings", error_kind: "auth" }));
  assert.equal(auth.text, "Reconnect Zotero");
  assert.equal(auth.action, "reconnect");
  assert.equal(auth.tone, "error");
  const offline = zoteroError(info({ last_error: "Zotero could not be reached: [Errno 11001] getaddrinfo failed", error_kind: "offline" }));
  assert.equal(offline.text, "Zotero unreachable · your edits wait here");
  assert.equal(offline.tone, "warn");
  assert.match(offline.title, /^Zotero could not be reached.*once it answers again\.$/);
  assert.equal(zoteroError(info({ last_error: "x", error_kind: "not_connected" })).action, "reconnect");
  assert.equal(zoteroError(info({ last_error: "x", error_kind: "library_gone" })).text, "This key no longer reaches Lab");
  assert.equal(zoteroError(info({ last_error: "x", error_kind: "busy" })).text, "Zotero is busy · retrying");
  const other = zoteroError(info({ last_error: "Something odd", error_kind: "other" }), "10:42");
  assert.equal(other.text, "Sync problem · 10:42");
  assert.equal(other.title, "Something odd");
  assert.equal(zoteroError(info({ last_error: "old server" })).text, "Sync problem", "no kind: a server from before error_kind");
});

test("an error only a new connection mends comes before the conflicts; one Gamma retries by itself does not", () => {
  // a first link left conflicts, then the key was revoked: every round fails, and the pill must say so
  for (const kind of ["auth", "not_connected", "library_gone"]) {
    assert.equal(zoteroHalted({ last_error: "x", error_kind: kind }), true, kind);
  }
  for (const kind of ["offline", "busy", "other", undefined]) {
    assert.equal(zoteroHalted({ last_error: "x", error_kind: kind }), false, String(kind));
  }
  assert.equal(zoteroHalted({ error_kind: "auth" }), false, "no error now: the last round went through");
  assert.equal(zoteroHalted(undefined), false);
  // Settings' row: Reconnect takes Sync's place only on such an error; PDFs waiting for file access
  // ask for a new key too, while records, notes and folders still sync, so Sync stays beside it
  const auth = zoteroError({ status: { last_error: "x", error_kind: "auth" } });
  assert.equal(reconnectOnly({ state: "error", action: auth.action }), true);
  const files = zoteroWaitingState({ status: { waiting: { files: { no_access: 2 } } } });
  assert.equal(files.action, "reconnect");
  assert.equal(reconnectOnly({ state: "waiting", action: files.action }), false);
  assert.equal(reconnectOnly({ state: "error" }), false, "an error a new key does not mend");
});

test("the pill reads every 2 s while a round runs, an edit waits or a Zotero link has never run", () => {
  const fresh = { kind: "zotero", detached: false, status: {} };
  assert.equal(zoteroUnstarted(fresh), true);
  assert.equal(linkPollMs("zotero", fresh), 2000, "a link just made, read before its first round started");
  assert.equal(linkPollMs("zotero", null), 20000, "nothing read yet: the mount read comes anyway");
  assert.equal(linkPollMs("zotero", { ...fresh, status: { running: true } }), 20000, "the round's own running is the caller's");
  assert.equal(linkPollMs("zotero", { ...fresh, status: { last_sync: "x" } }), 20000, "a link that has synced");
  assert.equal(linkPollMs("zotero", { ...fresh, status: { last_attempt: "x", last_error: "Zotero could not be reached" } }), 20000,
    "a first round that ended on an error");
  assert.equal(linkPollMs("zotero", { ...fresh, detached: true }), 20000, "a detached link");
  assert.equal(linkPollMs("zotero", { ...fresh, mode: "off" }), 20000);
  assert.equal(linkPollMs("mirror", { status: {} }), 20000, "a clone that has not synced polls as before");
  assert.equal(linkPollMs("mirror", { status: {} }, { running: true }), 2000);
  assert.equal(linkPollMs("zotero", { ...fresh, status: { last_sync: "x" } }, { pending: true }), 2000);
});

test("a fill counts across its bounded rounds, still syncing between two of them; a force says so", () => {
  const first = { running: true, progress: { phase: "first", done: 300, total: 2000 } };
  assert.equal(zoteroRunText(first), `First sync 300 / ${(2000).toLocaleString()}`);
  assert.equal(zoteroRunText({ running: true, progress: null }), "Syncing…", "a round before its first unit");
  assert.equal(zoteroRunText({ running: true, forced: "pull", progress: { phase: "sync", done: 3, total: 9 } }), "Force pull 3 / 9");
  const between = { running: false, fill: { units: 300, bytes: 0 }, backlog: 1700, last_sync: "x" };
  assert.equal(zoteroFollowing(between), true);
  assert.equal(zoteroRunText(between), `Syncing · ${(1700).toLocaleString()} left`);
  assert.equal(zoteroRunText({ ...between, forced: "push" }), `Force push · ${(1700).toLocaleString()} left`);
  assert.equal(zoteroFollowing({ ...between, backlog: 0 }), false);
  assert.equal(zoteroFollowing({ ...between, last_error: "Zotero could not be reached" }), false, "a round that stopped on an error does not go on");
  assert.equal(zoteroFollowing({ backlog: 4 }), false, "queued, with no round coming at once");
  // a fill a restart cut short (reset_interrupted): no round is asked for any more, so it is queued, not syncing
  assert.equal(zoteroFollowing({ ...between, interrupted: true }), false, "an interrupted fill does not follow at once");
  assert.equal(zoteroQueued({ status: { ...between, interrupted: true } }, "10:42").text, `${(1700).toLocaleString()} changes queued · 10:42`);
  assert.equal(zoteroBytesText({ bytes: { done: 1288490189, total: 8589934592 } }), "PDFs 1.2 GB / 8.0 GB");
  assert.equal(zoteroBytesText({ bytes: { done: 0, total: 0 } }), "");
  assert.equal(zoteroBytesText({ done: 1, total: 2 }), "", "a progress without bytes");
});

test("changes queued for a later round never read as up to date", () => {
  assert.equal(zoteroQueued({ status: { backlog: 0 } }), null);
  assert.equal(zoteroQueued({ status: { backlog: 3, running: true } }), null, "a round takes them now");
  assert.equal(zoteroQueued({ status: { backlog: 3, fill: { units: 1 } } }), null, "the next round follows at once");
  const queued = zoteroQueued({ status: { backlog: 3 } }, "10:42");
  assert.equal(queued.text, "3 changes queued · 10:42");
  assert.match(queued.title, /^3 queued changes wait for later rounds/);
  assert.equal(zoteroQueued({ status: { backlog: 1 } }, "10:42").text, "1 change queued · 10:42");
  assert.match(zoteroQueued({ status: { backlog: 2, forced: "pull" } }).title, /A force pull is under way/);
});

test("a Zotero link's unsent edits are on their way only while nothing else is queued for a later round", () => {
  // the server's pending_local counts a Gamma page queued whatever it waits for (its notes still being
  // edited, a write's backoff): with the last round's backlog, that is the queued reading, not a spinner
  assert.equal(unsentEdits({ kind: "zotero", pending_local: true, status: { backlog: 0 } }), true, "a write since the last round");
  assert.equal(unsentEdits({ kind: "zotero", pending_local: true, status: {} }), true);
  assert.equal(unsentEdits({ kind: "zotero", pending_local: true, status: { backlog: 1 } }), false, "a page waiting for quiet, a retry");
  assert.equal(unsentEdits({ kind: "zotero", pending_local: false, status: { backlog: 0 } }), false);
  assert.ok(zoteroQueued({ kind: "zotero", pending_local: true, status: { backlog: 1 } }, "10:42"), "which reads as queued");
  // a clone's pending_local is its own
  assert.equal(unsentEdits({ pending_local: true, status: { backlog: 3 } }), true);
  assert.equal(unsentEdits({ pending_local: false }), false);
  assert.equal(unsentEdits(null), false);
});

test("what waits on something outside Gamma reads by its reason, a full storage first", () => {
  assert.equal(zoteroWaitingState({ status: {} }), null);
  assert.equal(zoteroWaitingState({ status: { waiting: { files: { linked: 0 }, annotations: { n: 0, reasons: {} }, notes: 0 } } }), null);
  const missing = zoteroWaitingState({ status: { waiting: { files: { linked: 2, unavailable: 310 } } } });
  assert.equal(missing.text, "312 PDFs not in Zotero Storage");
  assert.match(missing.title, /^2 are linked files, 310 are not in Zotero Storage/);
  assert.equal(zoteroWaitingState({ status: { waiting: { files: { linked: 1 } } } }).text, "1 PDF not in Zotero Storage");
  // Zotero's 413, with the time it is tried again; without one (Sync now lifted it) the next round
  const quota = zoteroWaitingState({ status: { waiting: { files: { quota: 40, linked: 3 }, quota_until: "2026-10-29T10:00:00Z" } } });
  assert.equal(quota.text, "Zotero storage is full · 40 PDFs wait");
  assert.match(quota.title, /tries again after .*zotero\.org\/storage.* 3 are linked files/);
  assert.match(zoteroWaitingState({ status: { waiting: { files: { quota: 1 } } } }).title, /at the next round/);
  // this server's 507
  const storage = zoteroWaitingState({ status: { waiting: { files: { storage: 1 } } } });
  assert.equal(storage.text, "Storage here is full · 1 PDF waits");
  assert.match(storage.title, /^This server's storage is full/);
  // a key without file access: connecting again with another key mends it
  const access = zoteroWaitingState({ status: { waiting: { files: { no_access: 5 } } } });
  assert.equal(access.text, "5 PDFs wait for file access");
  assert.equal(access.action, "reconnect");
  const failed = zoteroWaitingState({ status: { waiting: { files: { failed: 2 } } } });
  assert.equal(failed.text, "2 PDF transfers failed");
  assert.match(failed.title, /^2 PDFs could not be moved/);
  assert.deepEqual(zoteroWaiting({ waiting: { files: { linked: "x" } } }).linked, 0, "a count that is no number counts nothing");
  // a PDF replaced here, held back while its highlights are placed: the ordinary course, said in the tooltip
  const placing = { status: { waiting: { files: { placing: 2 } } } };
  assert.equal(zoteroWaitingState(placing), null);
  assert.equal(zoteroWaitingNote(placing), "2 PDFs replaced here wait while their highlights are placed in them; then they go to Zotero.");
  const behind = zoteroWaitingState({ status: { waiting: { files: { placing: 1, failed: 1 } } } });
  assert.equal(behind.text, "1 PDF transfer failed");
  assert.match(behind.title, /1 PDF replaced here waits while its highlights are placed in it; then it goes to Zotero\.$/);
});

test("every reason the engine gives a PDF for waiting is one the pill words", async () => {
  const pdfs = await readFile(new URL("zotero_sync_pdfs.py", GAMMA), "utf8");
  const of = pdfs.match(/^_WAIT_OF = \{([^}]*)\}/m);
  assert.ok(of, "zotero_sync_pdfs.py maps what kept a PDF from coming to its reason");
  const reasons = new Set([...of[1].matchAll(/:\s*"([a-z_]+)"/g)].map((m) => m[1]));
  for (const [, value] of pdfs.matchAll(/^FILE_[A-Z_]+\s*=\s*"([a-z_]+)"/gm)) reasons.add(value);
  reasons.add("quota"); // Zotero's 413 (_quota_full)
  assert.ok(reasons.size >= 6, [...reasons].join(" | "));
  for (const reason of reasons) {
    assert.ok(zoteroWaitingNote({ status: { waiting: { files: { [reason]: 1 } } } }), `the pill words a PDF waiting for "${reason}"`);
  }
});

test("annotations wait with the engine's reasons and their counts; only some ask for a look", () => {
  const reasons = (r, n) => ({ status: { waiting: { files: {}, annotations: { n, reasons: r }, notes: 0 } } });
  const held = zoteroWaitingState(reasons({ "the PDFs differ": 3, "dashed in Gamma until accepted": 1, "the PDF is not here yet": 6 }, 10));
  assert.equal(held.text, "4 annotations wait", "the ones a person settles");
  assert.equal(held.title, "10 annotations wait: the PDFs differ (3), dashed in Gamma until accepted (1), the PDF is not here yet (6).");
  // a PDF not here yet (When opened), one being placed: the ordinary course, no warn note, but said
  const course = reasons({ "the PDF is not here yet": 1200, "being placed in the new PDF": 2 }, 1202);
  assert.equal(zoteroWaitingState(course), null);
  assert.equal(zoteroWaitingNote(course), `${(1202).toLocaleString()} annotations wait: the PDF is not here yet (${(1200).toLocaleString()}), being placed in the new PDF (2).`);
  assert.equal(zoteroWaitingNote({ status: {} }), "");
  // with PDFs not in Zotero Storage, the annotations on them follow in the title
  const both = zoteroWaitingState({ status: { waiting: { files: { unavailable: 1 }, annotations: { n: 4, reasons: { "the PDF is not here yet": 4 } } } } });
  assert.equal(both.text, "1 PDF not in Zotero Storage");
  assert.match(both.title, /4 annotations wait: the PDF is not here yet \(4\)\.$/);
});

test("every reason the engine gives an annotation for waiting is one the pill words", async () => {
  const engine = await engineSource();
  const sentences = [...engine.matchAll(/^WAIT_[A-Z_]+\s*=\s*"([^"]+)"/gm)].map((m) => m[1]);
  assert.ok(sentences.length >= 7, sentences.join(" | "));
  assert.deepEqual([...sentences].sort(), [...ZOTERO_WAIT_REASONS].sort());
});

test("every action a Zotero link's log names has its words, a folder kept here on a Zotero trash too", async () => {
  const engine = await engineSource();
  // the log's actions are the engine's "<verb> here" / "<verb> there" words, and pulled / pushed
  const actions = new Set(["pulled", "pushed", ...[...engine.matchAll(/"([a-z]+ (?:here|there))"/g)].map((m) => m[1])]);
  assert.ok(actions.has("kept here"), [...actions].join(" | "));
  for (const action of actions) assert.ok(ZOTERO_LOG_ACTIONS[action], `the log words "${action}"`);
  assert.match(ZOTERO_LOG_ACTIONS["kept here"], /Zotero's trash/);
});

test("a page's PDF in Zotero: downloadable, downloading, or why it cannot come", async () => {
  assert.equal(zoteroPdfState(null), null);
  assert.deepEqual(zoteroPdfState({ key: "K", state: "remote", size: 10 }), { state: "remote", reason: "", fetch: true });
  assert.deepEqual(zoteroPdfState({ state: "queued" }), { state: "queued", reason: "", fetch: true });
  assert.equal(zoteroPdfState({ state: "downloading" }).fetch, false);
  assert.deepEqual(zoteroPdfState({ state: "unavailable", reason: "linked" }), { state: "unavailable", reason: "a linked file in Zotero", fetch: false });
  assert.equal(zoteroPdfState({ state: "unavailable" }).reason, "not in Zotero Storage (WebDAV)");
  assert.equal(zoteroPdfState({ state: "unavailable", reason: "Zotero said no" }).reason, "Zotero said no");
  assert.equal(zoteroPdfState({ state: "unavailable", reason: "quota" }).fetch, false, "this server's storage is full: asking again does not help");
  assert.deepEqual(zoteroPdfState({ state: "unavailable", reason: "failed" }),
    { state: "unavailable", reason: "the download failed; it is tried again", fetch: true }, "a failed download may be asked for again");
  // the reasons the engine writes in a page's zotero_pdf (its _MARK_REASON's values) each have their words
  const engine = await engineSource();
  const table = engine.match(/^_MARK_REASON\s*=\s*\{([^}]*)\}/m);
  assert.ok(table, "the engine names the zotero_pdf reasons");
  const written = [...new Set([...table[1].matchAll(/:\s*"([a-z_]+)"/g)].map((m) => m[1]))].sort();
  assert.deepEqual(written, Object.keys(ZOTERO_PDF_REASONS).sort());
  // ...and the file waits it counts (its _WAIT_OF's values) are each read by zoteroWaiting
  const waits = engine.match(/^_WAIT_OF\s*=\s*\{([^}]*)\}/m);
  assert.ok(waits, "the engine names the file waits");
  const keys = [...new Set([...waits[1].matchAll(/:\s*"([a-z_]+)"/g)].map((m) => m[1]))];
  for (const key of [...keys, "quota"]) {
    const counted = zoteroWaiting({ waiting: { files: { [key]: 1 } } });
    assert.equal(Object.values(counted).filter((v) => v === 1).length, 1, `waiting.files.${key} is counted`);
    assert.ok(zoteroWaitingState({ status: { waiting: { files: { [key]: 1 } } } }), `waiting.files.${key} is worded`);
  }
});

test("a page's PDF card: what brings the PDF, whether it takes the attach prompt's place, Download now", () => {
  // what brings it, by the open workspace's session row
  assert.equal(zoteroPdfSource({ zotero_of: "My Library" }), "zotero");
  assert.equal(zoteroPdfSource({ zotero_of: "", zotero_detached: true }), "detached");
  assert.equal(zoteroPdfSource({ mirror_of: "Lab" }), "mirror", "a clone of a linked workspace: its origin's link");
  assert.equal(zoteroPdfSource({ publishing: true }), "", "a publication brings no PDF here");
  assert.equal(zoteroPdfSource({}), "", "the link removed, or the page came in from a backup");
  // the link forgotten (Remove link): nothing brings it any more, a detached link's row included
  assert.equal(zoteroPdfSource(linkGone({ id: "w", zotero_of: "", zotero_detached: true })), "");
  assert.equal(zoteroPdfSource(linkGone({ id: "w", zotero_of: "My Library" })), "");
  assert.equal(workspaceLink(linkGone({ id: "w", mirror_of: "Lab" })), "");
  const plain = { id: "w", zotero_of: "", mirror_of: "" };
  assert.equal(linkGone(plain), plain, "a workspace without a link is left as it is");
  assert.equal(linkGone(null), null);
  const remote = { key: "K", state: "remote", size: 10 };
  const linked = { key: "K", state: "unavailable", reason: "linked" };
  const failed = { key: "K", state: "unavailable", reason: "failed" };
  // a live link: the card takes the prompt's place while the PDF can come, and Download now asks for it
  let card = zoteroPdfCard(remote, { source: "zotero" });
  assert.equal(card.takesOver, true);
  assert.equal(card.download, true);
  assert.equal(card.hint, "It comes with the next PDFs, or now.");
  card = zoteroPdfCard(remote, { source: "zotero", asked: true });
  assert.equal(`${card.coming}/${card.download}`, "true/false", "asked: downloading");
  assert.equal(zoteroPdfCard({ ...remote, state: "downloading" }, { source: "zotero" }).coming, true);
  // a PDF that can never come (a linked file, WebDAV, no file access): above the prompt, so the page takes its own copy
  card = zoteroPdfCard(linked, { source: "zotero" });
  assert.equal(`${card.takesOver}/${card.download}/${card.coming}`, "false/false/false");
  assert.equal(card.hint, "It cannot come here: a linked file in Zotero.");
  assert.equal(card.note, "Attach your own copy below: it stays in Gamma and is not sent to Zotero.");
  // a failed download: above the prompt, with Download now, and asking again shows it downloading
  card = zoteroPdfCard(failed, { source: "zotero" });
  assert.equal(`${card.takesOver}/${card.download}`, "false/true");
  assert.equal(card.hint, "Its download failed; Gamma tries again by itself, or now.");
  assert.equal(zoteroPdfCard(failed, { source: "zotero", asked: true }).coming, true);
  // a copy attached while Zotero's cannot come yet goes to Zotero once it can (backend/tests/test_zotero_sync_words.py)
  const later = "Attach your own copy below. With the link's Send PDFs to Zotero on, it goes to Zotero once Gamma can read Zotero's file, which is then kept here, under Conflicts; off, it stays in Gamma.";
  assert.equal(card.note, later);
  for (const reason of ["files", "quota"]) {
    assert.equal(zoteroPdfCard({ key: "K", state: "unavailable", reason }, { source: "zotero" }).note, later, reason);
  }
  for (const reason of ["linked", "unavailable"]) {
    assert.equal(zoteroPdfCard({ key: "K", state: "unavailable", reason }, { source: "zotero" }).note,
      "Attach your own copy below: it stays in Gamma and is not sent to Zotero.", `${reason}: nothing goes over a file Zotero Storage does not serve`);
  }
  assert.equal(zoteroPdfCard(failed, { source: "" }).note, "Attach your own copy below: it stays in Gamma and is not sent to Zotero.",
    "no link sends it");
  // a detached link brings it once reattached; a clone's origin brings it; with nothing to bring it, the page takes its own
  card = zoteroPdfCard(remote, { source: "detached" });
  assert.equal(`${card.takesOver}/${card.download}`, "true/false");
  assert.equal(card.hint, "The Zotero link is detached: reattach it to bring this PDF.");
  card = zoteroPdfCard(remote, { source: "mirror" });
  assert.equal(`${card.takesOver}/${card.download}`, "true/false");
  assert.equal(card.hint, "It comes from the workspace this one is a clone of, whose Zotero link brings it.");
  assert.doesNotMatch(card.hint, /reattach/, "a clone has no link of its own to reattach");
  card = zoteroPdfCard(remote, { source: "" });
  assert.equal(`${card.takesOver}/${card.download}`, "false/false");
  assert.equal(card.hint, "No Zotero link brings it here any more.");
  assert.equal(zoteroPdfCard(failed, { source: "detached" }).download, false, "a detached link downloads nothing");
  assert.equal(zoteroPdfCard(null, { source: "zotero" }), null);
});

test("a page's PDF card agrees with the attach refusal in every state: a detached clone brings the PDF once reattached", async () => {
  // the session row's link fields (workspaces.list_for_user), which the
  // backend test of mirror_detached holds against routers/pages._zotero_keeps
  const listed = (await readFile(new URL("workspaces.py", GAMMA), "utf8")).replace(/\r\n/g, "\n").match(/^def list_for_user\([\s\S]*?\n    return out\n/m);
  assert.ok(listed, "the workspace list is there");
  for (const key of ["mirror_of", "mirror_detached", "zotero_of", "zotero_detached", "publishing"]) {
    assert.ok(listed[0].includes(`"${key}":`), `the session row says ${key}`);
  }
  const remote = { key: "K", state: "remote", size: 10 };
  // [row, what brings the PDF, whether the server refuses an attach meanwhile]
  const states = [
    [{ zotero_of: "My Library" }, "zotero", true],
    [{ zotero_of: "", zotero_detached: true }, "detached", true],
    [{ mirror_of: "Lab" }, "mirror", true],
    [{ mirror_of: "", mirror_detached: true }, "mirror-detached", true],
    [{ publishing: true }, "", false],
    [{}, "", false],
  ];
  for (const [row, source, refused] of states) {
    assert.equal(zoteroPdfSource(row), source, JSON.stringify(row));
    const card = zoteroPdfCard(remote, { source });
    assert.equal(card.takesOver, refused, `${JSON.stringify(row)}: the card takes the attach prompt's place exactly while the server refuses one`);
    assert.equal(card.download, source === "zotero", "only a live link downloads");
    // the page's PDF button says so too: Download now only where the card offers it
    assert.equal(Boolean(card.title), refused, `${source}: the button speaks of Zotero's PDF while the card shows`);
    assert.equal(/download it now/.test(card.title), source === "zotero", `${source}: ${card.title}`);
  }
  const card = zoteroPdfCard(remote, { source: "mirror-detached" });
  assert.equal(card.hint, "This clone is detached: reattach it to bring this PDF from the workspace it copies.");
  assert.equal(card.note, "Gamma takes Zotero's file, so the annotations on both sides stay on one PDF.");
  // a PDF that can never come: above the prompt, as for every source
  assert.equal(zoteroPdfCard({ key: "K", state: "unavailable", reason: "linked" }, { source: "mirror-detached" }).takesOver, false);
  // Remove origin of a detached clone: nothing brings it any more
  assert.equal(zoteroPdfSource(linkGone({ id: "w", mirror_of: "", mirror_detached: true })), "");
});

test("an attach refused while the page's PDF comes from Zotero is told apart by its sentence", async () => {
  const pages = await readFile(new URL("routers/pages.py", GAMMA), "utf8");
  const sentence = pages.match(/^ZOTERO_PDF_COMING\s*=\s*"([^"]+)"/m);
  assert.ok(sentence, "routers/pages.py says why it refuses");
  assert.equal(sentence[1], ZOTERO_PDF_COMING);
  assert.equal(isZoteroPdfComing({ status: 409, message: ZOTERO_PDF_COMING }), true);
  assert.equal(isZoteroPdfComing({ status: 409, message: "page already has an attachment" }), false);
  assert.equal(isZoteroPdfComing({ status: 400, message: ZOTERO_PDF_COMING }), false);
});

test("the actions both surfaces offer are worded once per kind, and the links' API base is written once", async () => {
  assert.deepEqual(Object.keys(LINK_ACTIONS.zotero).sort(), Object.keys(LINK_ACTIONS.mirror).sort());
  assert.equal(LINK_ACTIONS.zotero.remove, "Remove link");
  assert.equal(LINK_ACTIONS.mirror.remove, "Remove origin");
  const src = new URL("../src/", import.meta.url);
  const files = [];
  const walk = async (dir) => {
    for (const entry of await readdir(dir, { withFileTypes: true })) {
      const next = new URL(`${entry.name}${entry.isDirectory() ? "/" : ""}`, dir);
      if (entry.isDirectory()) await walk(next);
      else if (/\.(jsx?|mjs)$/.test(entry.name)) files.push(next);
    }
  };
  await walk(src);
  const writing = [];
  for (const file of files) {
    const text = await readFile(file, "utf8");
    if (/["'`]\/zotero\/links/.test(text)) writing.push(file.pathname.split("/src/")[1]);
  }
  assert.deepEqual(writing, ["collaboration/syncLinks.js"], "LINK_BASES is the one place");
});
