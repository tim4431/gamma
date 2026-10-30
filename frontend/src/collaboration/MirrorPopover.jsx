// The sync pill in the topbar of a clone (docs/dev/mirror.md) and its
// popover, in git's words: the workspace is a clone, the workspace it
// follows on the other server is its origin (the remote), a round pulls
// then pushes. The pill is an icon whose state is drawn on it, like the
// background-tasks button: spinning while a round runs, a badge with the
// count when conflicts wait, a dot — accent for local edits not pushed yet,
// green when up to date, red on a problem — and an unlink glyph when
// detached; the words live in its tooltip and in `data-state`. The popover:
// the clone's name with its origin, a Sync button and a gear in the head,
// the state line (with the progress bars while a round runs and the
// conflicts chip when any wait), and the log — a direction arrow per row,
// its `+3 −1 ~2` block counts, and on click the row's changes block by
// block as a diff (added, removed, changed with a word diff); an arrow
// opens the page. The gear turns the popover into the clone's sync
// settings (settings-kit rows: cadence, push after an edit, direction,
// then force pull / force push, detach / reattach, remove origin). While the
// pill is shown and the tab visible, polls /api/mirrors/{ws} every 20 s,
// every 2 s while a round runs or a local edit waits (the page's collab
// session raises `gamma:local-edit` when it queues ops) and while a Zotero
// link has never run (syncLinks.js `linkPollMs`); the log too while open.
//
// A publication (a mirror with a page filter: the pages this workspace
// publishes to Gamma Cloud, docs/dev/mirror.md "Publishing") gets the same
// pill and reading, but its name line says "Published to Gamma Cloud" and
// its settings keep only the cadence: direction, the forces, detach and
// remove origin would break it.
//
// A Zotero link (`kind` "zotero", docs/dev/zotero_sync.md) gets the same
// pill and popover over /api/zotero/links/{ws} (syncLinks.js), in Zotero's
// words: its errors read by kind (Zotero unreachable, Reconnect Zotero with
// the way to Account & sync, a library the key no longer reaches, Zotero
// busy), what waits on something outside Gamma (PDFs not in Zotero Storage,
// a full storage, annotations held for a look) is a warn note before "Up to
// date", the first fill reads "First sync n / N" across its bounded rounds
// ("Syncing · n left" between two of them, a force in its own words), the
// changes still queued for a later round never read as up to date, what
// the key may not do (write, read notes) is said under the state, and its
// settings add what PDFs come and go and whether Zotero's automatic tags
// become labels.
import React from "react";
import { API, apiJson, fmtBytes } from "../shared/lib/utils";
import { Row, Segmented, Toggle } from "../settings/SettingsKit";
import { ConflictCard, Marked, useConflicts, wordDiff } from "./MergeResolver";
import {
  LINK_ACTIONS, ZOTERO_CADENCE_HINT, ZOTERO_CADENCE_TITLE, ZOTERO_LIVE_S, ZOTERO_LOG_ACTIONS, ZOTERO_MANUAL, ZOTERO_UPLOAD_HINT,
  ZOTERO_UPLOAD_TITLE, linkKind, linkPath, linkPollMs, onChangeHint, pullByKey, unsentEdits, zoteroBytesText, zoteroCadence, zoteroError,
  zoteroFollowing, zoteroForceConfirm, zoteroHalted, zoteroLibraryLabel, zoteroLimits, zoteroQueued, zoteroRunText, zoteroWaitingNote,
  zoteroWaitingState,
} from "./syncLinks.js";
import { needsZoteroConnect } from "../transfers/zoteroApi";
import { MenuItem } from "../shared/ui/Menus";
import { T, t } from "../shared/i18n/i18n.js";
import {
  ActivityIcon, AlertCircleIcon, ArrowDownIcon, ArrowLeftIcon, ArrowUpDownIcon, ArrowUpIcon, CheckIcon,
  ClockIcon, CloudDownloadIcon, CloudOffIcon, DownloadIcon, ExternalLinkIcon, FileIcon, FolderIcon, HandIcon, HistoryIcon, KeyIcon,
  LabelIcon, LinkIcon, PenIcon, RefreshIcon, SettingsIcon, TrashIcon, UnlinkIcon, UploadIcon,
} from "../shared/ui/Icons";
import { Collapse } from "../shared/ui/MotionWidgets";
import { Popover } from "../shared/ui/Surfaces";
import "./zoteroLink.css";

// The server's sync_log actions, in git's words.
export const ACTION_TEXT = {
  "pulled": t("pulled from remote"),
  "pushed": t("pushed to remote"),
  "created here": t("pulled from remote (new page)"),
  "created there": t("pushed to remote (new page)"),
  "deleted here": t("deleted on remote — removed here"),
  "deleted there": t("deleted here — removed on remote"),
  "restored here": t("restored from remote (edited there after it was deleted here)"),
  "restored there": t("restored on remote (kept here after it was deleted there)"),
  "replaced here": t("force-pulled: remote's version replaced this one"),
  "replaced there": t("force-pushed: this version replaced remote's"),
};

// The log rows' glyphs, both kinds' (a Zotero link's words are syncLinks.js
// ZOTERO_LOG_ACTIONS).
const ACTION_ICON = {
  "pulled": ArrowDownIcon, "created here": ArrowDownIcon, "restored here": ArrowDownIcon, "replaced here": ArrowDownIcon,
  "pushed": ArrowUpIcon, "created there": ArrowUpIcon, "restored there": ArrowUpIcon, "replaced there": ArrowUpIcon,
  "deleted here": TrashIcon, "deleted there": TrashIcon, "kept here": FolderIcon,
};

export function clock(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  return sameDay ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

// The nouns n() is called with, so the catalog carries them.
export const COUNT_NOUNS = [T("page"), T("file"), T("conflict"), T("published page"), T("annotation"), T("note")];

// A count with its noun: `word` is an English singular (translated here),
// the plural s is English-only grammar ({_s}).
export function n(count, word) {
  return t("{count} {word}{_s}", { count: count || 0, word: t(word), _s: count === 1 ? "" : "s" });
}

// The git-style block counts of a log row or a round: "+3 −1 ~2" ("" when
// nothing changed). `stats` is {add, del, mod}.
export function diffStat(stats) {
  if (!stats) return "";
  const parts = [];
  if (stats.add) parts.push(`+${stats.add}`);
  if (stats.del) parts.push(`−${stats.del}`);
  if (stats.mod) parts.push(`~${stats.mod}`);
  return parts.join(" ");
}

// The same, coloured like a diff stat.
export function DiffStat({ stats, title }) {
  if (!stats || !(stats.add || stats.del || stats.mod)) return null;
  return (
    <span className="mirrorDiff" title={title || t("blocks added · removed · changed")}>
      {stats.add ? <span className="add">+{stats.add}</span> : null}
      {stats.del ? <span className="del">−{stats.del}</span> : null}
      {stats.mod ? <span className="mod">~{stats.mod}</span> : null}
    </span>
  );
}

// A round's block totals from its status.
export function roundBlocks(s) {
  return { add: s.blocks_added || 0, del: s.blocks_removed || 0, mod: s.blocks_changed || 0 };
}

// What the last round moved, as one sentence ("" when nothing moved). A
// Zotero link's round also counts annotations and notes.
export function roundSummary(s) {
  const parts = [];
  if (s.pages_pulled) parts.push(t("{pages} pulled", { pages: n(s.pages_pulled, "page") }));
  if (s.pages_pushed) parts.push(t("{pages} pushed", { pages: n(s.pages_pushed, "page") }));
  if (s.pages_deleted) parts.push(t("{pages} removed", { pages: n(s.pages_deleted, "page") }));
  const files = (s.files_pulled || 0) + (s.files_pushed || 0);
  if (files) parts.push(n(files, "file"));
  const annotations = (s.annotations_pulled || 0) + (s.annotations_pushed || 0);
  if (annotations) parts.push(n(annotations, "annotation"));
  const notes = (s.notes_pulled || 0) + (s.notes_pushed || 0);
  if (notes) parts.push(n(notes, "note"));
  const blocks = diffStat(roundBlocks(s));
  if (blocks) parts.push(t("{blocks} blocks", { blocks }));
  return parts.join(t(", "));
}

// A mirror with a page filter is a publication, not a clone.
export function isPublication(info) {
  return Array.isArray(info?.page_filter);
}

export function isPullOnly(info) {
  return info?.status?.mode === "pull" || info?.mode === "pull";
}

export function hostOf(url) {
  try { return new URL(url).host; } catch { return url || ""; }
}

// Which side must be updated before a paused clone syncs again: "here" or
// "remote" when the last round stopped because the two servers keep the
// workspace's data in different shapes (the status's `version_gap`, one of
// them runs a release with a migration the other lacks), else null.
export function versionGap(info) {
  const g = info?.status?.version_gap;
  if (!g) return null;
  return g.here < g.remote ? "here" : "remote";
}

// The glyphs of a Zotero link's error kinds (syncLinks.js zoteroError).
const ZOTERO_ERROR_ICON = { offline: CloudOffIcon, key: KeyIcon, clock: ClockIcon, alert: AlertCircleIcon };

// The link's state, one reading for the pill, its popover and the
// Settings row: `state` (detached | busy | outdated | conflicts | error |
// new | pending | waiting | queued | ok), `tone` (the colour), `Icon`, a
// short `text`, the longer `title`; `badge` (a count) or `dot` for the
// pill; `stats` when up to date; `detail` (the page being worked) while a
// round runs; `action` "reconnect" when only connecting Zotero again (or
// with another key) helps. A Zotero link reads busy too between two
// bounded rounds of one fill, and `queued` while changes wait for a later
// round; an error only a new connection mends reads before its conflicts.
// `pending` adds local edits the client knows about before the server
// does; `kind` says a Zotero link's words before its info arrives.
export function mirrorState(info, { busy = false, pending = false, kind = linkKind(info) } = {}) {
  const zotero = kind === "zotero";
  const s = info?.status || {};
  const p = s.progress;
  if (info?.detached || info?.mode === "off") {
    return { state: "detached", tone: "", Icon: UnlinkIcon, text: `${t("Detached")}${s.detached_at ? ` · ${clock(s.detached_at)}` : ""}`,
      title: T("Detached: nothing is pulled or pushed until you reattach. Nothing is lost.") };
  }
  const following = zotero && zoteroFollowing(s);
  if (s.running || busy || following) {
    if (zotero) {
      return { state: "busy", tone: "busy", Icon: RefreshIcon, text: zoteroRunText(s), detail: p?.page || "",
        title: s.running || busy ? T("A round is running") : T("A round has just ended; the next one follows at once") };
    }
    return { state: "busy", tone: "busy", Icon: RefreshIcon,
      text: `${p?.first ? t("Cloning") : t("Syncing")}${p?.total ? ` ${p.done} / ${p.total}` : "…"}`,
      detail: p?.page || "", title: T("A round is running") };
  }
  const gap = versionGap(info);
  if (gap === "here") {
    return { state: "outdated", tone: "warn", Icon: DownloadIcon, dot: true, text: t("Paused · update Gamma"),
      title: t("The remote runs a newer version of Gamma. Sync is paused until you update this one; your edits stay here and go out then.") };
  }
  if (gap === "remote") {
    return { state: "outdated", tone: "warn", Icon: ClockIcon, dot: true, text: t("Paused · the remote needs an update"),
      title: t("The remote runs an older version of Gamma. Sync is paused until it is updated; your edits stay here and go out then.") };
  }
  const failed = () => {
    const e = zoteroError(info, clock(s.last_attempt || s.last_sync));
    return { state: "error", tone: e.tone, Icon: ZOTERO_ERROR_ICON[e.icon] || AlertCircleIcon, dot: true, text: e.text, title: e.title, action: e.action };
  };
  // (a refused key fails every round: Resolve would hide it)
  if (zotero && zoteroHalted(s)) return failed();
  if (info?.conflicts_open) {
    return { state: "conflicts", tone: "warn", Icon: RefreshIcon, badge: info.conflicts_open,
      text: n(info.conflicts_open, "conflict"), title: zotero ? T("Changes both sides made wait for a decision") : T("Blocks both sides changed wait for a decision") };
  }
  if (s.last_error && zotero) return failed();
  if (s.last_error) {
    const unreachable = /cannot reach|timed out|refused|unreachable/i.test(s.last_error);
    return { state: "error", tone: "error", Icon: AlertCircleIcon, dot: true,
      text: `${unreachable ? t("Remote unreachable") : t("Sync problem")}${s.last_attempt || s.last_sync ? ` · ${clock(s.last_attempt || s.last_sync)}` : ""}`,
      title: `${s.last_error}${unreachable ? t(" — your edits stay here and are pushed once the remote is reachable again.") : ""}` };
  }
  if (!s.last_sync) {
    if (zotero) {
      return { state: "new", tone: "", Icon: CloudDownloadIcon, text: s.interrupted ? t("Interrupted · resuming") : t("Not synced yet"),
        title: s.interrupted ? t("The first sync was interrupted; it continues in a moment.") : t("The first sync starts in a moment.") };
    }
    return { state: "new", tone: "", Icon: CloudDownloadIcon, text: s.interrupted ? t("Interrupted · resuming") : t("Not cloned yet"),
      title: s.interrupted ? t("The clone was interrupted; it continues in a moment.") : t("The clone starts in a moment.") };
  }
  if (unsentEdits(info) || pending) {
    // with sync-after-edit on, a round follows within a second: the icon
    // spins from the edit until the round is confirmed done, so a round too
    // short for a poll to catch still reads as "syncing"
    if (info?.on_change) {
      return { state: "busy", tone: "busy", Icon: RefreshIcon, text: T("Syncing your edits…"), title: T("A round follows every edit; your changes are on their way.") };
    }
    return { state: "pending", tone: "pending", Icon: RefreshIcon, dot: true, text: T("Local edits not synced yet"),
      title: T("Press Sync to send them, or wait for the next automatic round.") };
  }
  const moved = roundSummary(s);
  if (zotero) {
    const last = clock(s.last_sync);
    const queued = zoteroQueued(info, last);
    const since = queued ? t("Last round {last_sync}.", { last_sync: last }) : t("Up to date since {last_sync}.", { last_sync: last });
    // (the waiting and queued readings' titles show under the state, beside the key's limits' own line)
    const waiting = zoteroWaitingState(info);
    if (waiting) {
      return { state: "waiting", tone: "warn", Icon: CloudOffIcon, dot: true, text: waiting.text, stats: roundBlocks(s), action: waiting.action,
        title: `${since} ${waiting.title}${queued ? ` ${queued.title}` : ""}` };
    }
    const note = zoteroWaitingNote(info);
    const side = moved || t("nothing had changed on either side");
    if (queued) {
      return { state: "queued", tone: "pending", Icon: RefreshIcon, dot: true, text: queued.text, stats: roundBlocks(s),
        title: `${t("Last round {last_sync}: {side}.", { last_sync: last, side })} ${queued.title}${note ? ` ${note}` : ""}` };
    }
    const limits = zoteroLimits(info).map((l) => ` ${l.title}`).join("");
    return { state: "ok", tone: "ok", Icon: RefreshIcon, dot: true, text: t("Up to date · {last_sync}", { last_sync: last }), stats: roundBlocks(s),
      title: `${t("Up to date since {last_sync}. Last round: {side}.", { last_sync: last, side })}${note ? ` ${note}` : ""}${limits}${zoteroCadence(info?.poll_s, { onChange: info?.on_change !== false })}` };
  }
  return { state: "ok", tone: "ok", Icon: RefreshIcon, dot: true, text: t("Up to date · {last_sync}", { last_sync: clock(s.last_sync) }), stats: roundBlocks(s),
    title: t("Up to date since {last_sync}. Last round: {side}.{sync}", { last_sync: clock(s.last_sync), side: moved || "nothing had changed on either side", sync: info?.poll_s ? t(" The remote is checked every {poll_s} s.", { poll_s: info.poll_s }) : t(" The remote is checked only when you sync.") }) };
}

// The choices the sync settings offer, shared with Settings' clone dialog.
export const CADENCE = [
  [5, t("Live"), ActivityIcon, t("Sync every 5 seconds")],
  [30, "30 s", null, t("Sync every 30 seconds")],
  [300, "5 min", null, t("Sync every five minutes")],
  [0, t("Manual"), HandIcon, t("Only when you press Sync")],
];
export const DIRECTION = [
  ["two-way", t("Two-way"), ArrowUpDownIcon, t("Your changes go to the remote and the remote's arrive here")],
  ["pull", t("Receive only"), ArrowDownIcon, t("The remote's changes arrive here; yours stay here until you switch back to two-way")],
];
// A Zotero link's, shared with Settings' link dialog. Live is Zotero's
// streaming API: it says when something changed (a round every 30 s
// besides, in case it missed one). Manual is no schedule: Zotero's changes
// come with whatever round comes next (syncLinks.js ZOTERO_CADENCE_TITLE).
export const ZOTERO_CADENCE = [
  [ZOTERO_LIVE_S, T("Live"), ActivityIcon, T("Instant: Zotero tells Gamma when something changes")],
  [30, "30 s", null, T("Sync every 30 seconds")],
  [300, "5 min", null, T("Sync every five minutes")],
  [0, T("Manual"), HandIcon, ZOTERO_MANUAL],
];
export const ZOTERO_DIRECTION = [
  ["two-way", T("Two-way"), ArrowUpDownIcon, T("Your changes go to Zotero and Zotero's arrive here")],
  ["pull", T("Receive only"), ArrowDownIcon, T("Zotero's changes arrive here; yours stay here until you switch back to two-way")],
];
export const ZOTERO_DOWNLOAD = [
  ["all", T("All"), DownloadIcon, T("Every PDF in Zotero Storage comes here")],
  ["open", T("When opened"), FileIcon, T("A PDF comes when you open its page, and the 50 newest at once")],
];
export const ZOTERO_UNRECORDED = [
  [false, T("Keep in Gamma"), null, T("A PDF page without a paper record stays in Gamma until it has one")],
  [true, T("Send as documents"), null, T("A PDF page without a paper record goes to Zotero as a document")],
];

// The progress bars of a running round: the pages, then the file in
// flight; a Zotero link's fill adds its PDFs' bytes, all of it so far.
export function Progress({ progress: p, kind = "mirror" }) {
  if (!p) return null;
  const f = p.file;
  const zotero = kind === "zotero";
  const bytes = zotero ? zoteroBytesText(p) : "";
  const way = f?.dir === "up" ? (zotero ? t("Sending to Zotero") : t("Pushing to the remote"))
    : zotero ? t("Downloading from Zotero") : t("Pulling from the remote");
  return (
    <>
      {p.total ? <div className="mirrorBar"><span style={{ width: `${Math.round((100 * p.done) / p.total)}%` }} /></div> : null}
      {f ? (
        <div className="mirrorFile" title={way}>
          {f.dir === "up" ? <ArrowUpIcon size={14} /> : <ArrowDownIcon size={14} />}
          <span className="mirrorEllipsis">{f.name}</span>
          <span className="mirrorFileBytes">{fmtBytes(f.done)}{f.total ? ` / ${fmtBytes(f.total)}` : ""}</span>
          {f.total ? <div className="mirrorBar thin"><span style={{ width: `${Math.round((100 * f.done) / f.total)}%` }} /></div> : null}
        </div>
      ) : null}
      {bytes ? (
        <div className="mirrorFile" title={t("The PDFs this sync moves, those still queued included")}>
          <FileIcon size={14} />
          <span className="mirrorFileBytes">{bytes}</span>
          <div className="mirrorBar thin"><span style={{ width: `${Math.min(100, Math.round((100 * p.bytes.done) / p.bytes.total))}%` }} /></div>
        </div>
      ) : null}
    </>
  );
}

// The state block: an icon, a short line, the bars while a round runs, the
// conflicts chip when any wait; for a key Zotero refuses, the way to
// connect it again.
function StateBlock({ info, kind, busy, pending, onConflicts, onReconnect }) {
  const st = mirrorState(info, { busy, pending, kind });
  const p = info?.status?.progress;
  return (
    <div className={`mirrorState ${st.tone}`} title={t(st.title)}>
      <st.Icon size={14} />
      <div className="mirrorStateBody">
        <div className="mirrorStateLine">
          <span>{t(st.text)}</span>
          {st.detail ? <span className="popoverHint mirrorEllipsis" title={st.detail}>{st.detail}</span> : null}
          {st.stats ? <DiffStat stats={st.stats} title={t("Last round: blocks added · removed · changed")} /> : null}
          {info?.conflicts_open && st.state !== "conflicts" ? (
            <button className="uiBtn sm mirrorConflictBtn" onClick={onConflicts} title={t("Blocks both sides changed: resolve them here, or open each on its block")}>
              <AlertCircleIcon size={14} /> {n(info.conflicts_open, "conflict")}
            </button>
          ) : null}
          {st.state === "conflicts" ? (
            <button className="uiBtn sm primary" onClick={onConflicts} title={t("Resolve them here, or open each on its block")}>{t("Resolve")}</button>
          ) : null}
          {st.action === "reconnect" && onReconnect ? (
            <button className="uiBtn sm primary" onClick={onReconnect} title={t("Settings › Account & sync › Zotero")}>
              <KeyIcon size={14} /> {t("Reconnect")}
            </button>
          ) : null}
        </div>
        {st.tone === "busy" ? <Progress progress={p} kind={kind} /> : null}
        {st.state === "outdated" || st.state === "waiting" || st.state === "queued" ? <div className="popoverHint">{st.title}</div> : null}
      </div>
    </div>
  );
}

// Force pull / force push / remove origin ask once, inline: a warning line
// with Yes / No. A Zotero link's force asks in syncLinks.js's words
// (zoteroForceConfirm).
function Confirm({ what, kind, busy, onYes, onNo }) {
  const text = kind === "zotero"
    ? what === "pull" || what === "push" ? zoteroForceConfirm(what)
      : t("Remove the Zotero link? The workspace and the library both stay; they never sync again.")
    : what === "pull" ? t("Force pull: make this clone identical to the remote? Local texts it replaces or removes are kept as conflicts.") : what === "push" ? t("Force push: make the remote identical to this clone? Remote texts it replaces or removes are kept as conflicts.") : t("Remove origin? The workspace stays; it never syncs again.");
  return (
    <div className="mirrorConfirm">
      <AlertCircleIcon size={14} />
      <span>{text}</span>
      <span className="mirrorConfirmBtns">
        <button type="button" className="uiBtn sm primary danger" disabled={busy} onClick={onYes}>{t("Yes")}</button>
        <button type="button" className="uiBtn sm" disabled={busy} onClick={onNo}>{t("No")}</button>
      </span>
    </div>
  );
}

// What a Zotero link adds to its sync settings: which PDFs come, whether
// PDFs go up, a paper's PDF Zotero lacks and a PDF replaced here alike
// (syncLinks.js ZOTERO_UPLOAD_HINT; not with a key that cannot store
// files there, by the last round's `status.access`), Zotero's automatic
// tags, PDF pages without a record. `patch(body)` saves one.
export function ZoteroLinkSettings({ info, busy, patch }) {
  const access = info?.status?.access;
  const noUpload = access?.write === false || access?.files === false;
  return (
    <>
      <Row icon={DownloadIcon} label={t("PDFs from Zotero")} hint={t("all at once, or each as its page opens")}
        title={t("All: every PDF in Zotero Storage comes here. When opened: a PDF (and its annotations) comes when you open its page, and the 50 newest at once.")}>
        <Segmented value={info?.download || "all"} disabled={busy} onChange={(v) => patch({ download: v })} options={ZOTERO_DOWNLOAD} />
      </Row>
      <Toggle icon={UploadIcon} checked={Boolean(info?.upload)} disabled={busy || noUpload} onChange={(v) => patch({ upload: v })}
        label={t("Send PDFs to Zotero")} title={t(ZOTERO_UPLOAD_TITLE)}
        hint={noUpload ? t("Your Zotero key cannot store files in this library") : t(ZOTERO_UPLOAD_HINT)} />
      <Toggle icon={LabelIcon} checked={Boolean(info?.auto_tags)} disabled={busy} onChange={(v) => patch({ auto_tags: v })}
        label={t("Automatic tags as labels")} hint={t("publishers' keywords too, not only your own tags")} />
      <Row icon={FileIcon} label={t("PDFs without a record")} hint={t("a PDF page whose paper record is not found")}>
        <Segmented value={Boolean(info?.send_unrecorded)} disabled={busy} onChange={(v) => patch({ send_unrecorded: v })} options={ZOTERO_UNRECORDED} />
      </Row>
    </>
  );
}

// `onOpenAccount` opens Settings › Account & sync, for a refusal a
// connection mends (a Zotero link's reattach with no account connected);
// `onLinkMoved(path)` follows a detach or reattach done ("/detach",
// "/relink"): the workspace's session row changes with it.
function SettingsView({ info, wsId, kind, publication, onBack, reload, onOpenSettings, onOpenAccount, onLinkMoved }) {
  const [confirm, setConfirm] = React.useState(null); // "pull" | "push" | "forget" | null
  const [busy, setBusy] = React.useState(false);
  // why the last change was refused: {message, connect} (connect: a Zotero connection mends it)
  const [refused, setRefused] = React.useState(null);
  const detached = info?.detached;
  const pull = isPullOnly(info);
  const zotero = kind === "zotero";
  const keyPull = zotero && pullByKey(info);
  const words = LINK_ACTIONS[linkKind(kind)];
  async function call(path, init, body) {
    setBusy(true);
    setRefused(null);
    try {
      await apiJson(`${API}${linkPath(kind, wsId, path)}`, {
        method: init, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined,
      });
      if (path === "/detach" || path === "/relink") onLinkMoved?.(path);
    } catch (err) {
      setRefused({ message: err.message || String(err), connect: zotero && needsZoteroConnect(err) });
    }
    setBusy(false);
    setConfirm(null);
    if (init === "DELETE") { window.dispatchEvent(new CustomEvent("gamma:mirror-gone")); return; }
    reload();
    window.dispatchEvent(new CustomEvent("gamma:mirror"));
  }
  return (
    <>
      <div className="mirrorPopHead">
        <button className="iconBtn sm" onClick={onBack} title={t("Back")} aria-label={t("Back")}><ArrowLeftIcon size={14} /></button>
        <span className="popoverTitle">{t("Sync settings")}</span>
      </div>
      {zotero ? (
        <Row icon={ClockIcon} label={t("Automatic sync")} hint={t(ZOTERO_CADENCE_HINT)} title={t(ZOTERO_CADENCE_TITLE)}>
          <Segmented value={info?.poll_s ?? ZOTERO_LIVE_S} onChange={(v) => call("", "PATCH", { poll_s: v })} options={ZOTERO_CADENCE} />
        </Row>
      ) : (
        <Row icon={ClockIcon} label={t("Automatic sync")} hint={t("how often the remote is checked")}
          title={t("A sync round every so often. Live: every 5 s. Manual: only when you press Sync.")}>
          <Segmented value={info?.poll_s ?? 30} onChange={(v) => call("", "PATCH", { poll_s: v })} options={CADENCE} />
        </Row>
      )}
      <Toggle icon={PenIcon} checked={Boolean(info?.on_change)} disabled={busy} onChange={(v) => call("", "PATCH", { on_change: v })}
        label={t("Sync after an edit")} hint={onChangeHint(kind)} />
      {!publication && !detached ? (zotero ? (
        <Row icon={ArrowUpDownIcon} label={t("Direction")} hint={keyPull ? t("receive only · your Zotero key cannot write") : undefined}
          title={t("Two-way: your changes go to Zotero. Receive only: Zotero's changes arrive, yours stay here until you switch back.")}>
          <Segmented value={info?.mode === "pull" ? "pull" : "two-way"} onChange={(v) => call("", "PATCH", { mode: v })} options={ZOTERO_DIRECTION} />
        </Row>
      ) : (
        <Row icon={ArrowUpDownIcon} label={t("Direction")}
          title={t("Two-way: your changes go to the remote. Receive only: the remote's changes arrive, yours stay here until you switch back.")}>
          <Segmented value={pull ? "pull" : "two-way"} onChange={(v) => call("", "PATCH", { mode: v })} options={DIRECTION} />
        </Row>
      )) : null}
      {zotero && !detached ? <ZoteroLinkSettings info={info} busy={busy} patch={(body) => call("", "PATCH", body)} /> : null}
      {publication && !detached ? null : <div className="popoverDivider" />}
      {publication ? (detached ? (
        <div className="mirrorSetActions">
          <button className="uiBtn sm primary" disabled={busy} onClick={() => call("/relink", "POST", {})} title={t("Publish again; what both sides did meanwhile merges")}>
            <LinkIcon size={14} /> {t("Reattach")}
          </button>
        </div>
      ) : null) : confirm ? (
        <Confirm what={confirm} kind={kind} busy={busy} onNo={() => setConfirm(null)}
          onYes={() => confirm === "forget" ? call("", "DELETE") : call("/force", "POST", { direction: confirm })} />
      ) : (
        <div className="mirrorSetActions">
          {!detached ? <>
            <button className="uiBtn sm" disabled={busy} onClick={() => setConfirm("pull")} title={t(words.forcePull)}>
              <CloudDownloadIcon size={14} /> {t("Force pull")}
            </button>
            <button className="uiBtn sm" disabled={busy || pull} onClick={() => setConfirm("push")} title={t(pull ? words.pushBlocked : words.forcePush)}>
              <UploadIcon size={14} /> {t("Force push")}
            </button>
            <button className="uiBtn sm" disabled={busy} onClick={() => call("/detach", "POST")} title={t(words.detach)}>
              <UnlinkIcon size={14} /> {t("Detach")}
            </button>
          </> : (
            <button className="uiBtn sm primary" disabled={busy} onClick={() => call("/relink", "POST", {})} title={t(words.reattach)}>
              <LinkIcon size={14} /> {t("Reattach")}
            </button>
          )}
          <button className="uiBtn sm danger" disabled={busy} onClick={() => setConfirm("forget")} title={t(words.removeTitle)}>
            <TrashIcon size={14} /> {t(words.remove)}
          </button>
        </div>
      )}
      {refused ? (
        <div className="mirrorRefused" role="alert">
          <span className="settingsPaneHint aiKeysError">{t("Could not do that: {message}", { message: refused.message })}</span>
          {refused.connect && onOpenAccount ? (
            <button className="uiBtn sm primary" onClick={onOpenAccount} title={t("Settings › Account & sync › Zotero")}>
              <KeyIcon size={14} /> {t("Reconnect")}
            </button>
          ) : null}
        </div>
      ) : null}
      <MenuItem icon={SettingsIcon} onClick={onOpenSettings}>
        {publication ? t("Publishing in Settings") : zotero ? t("All links in Settings") : t("All clones in Settings")}
      </MenuItem>
    </>
  );
}

// The conflicts, each a card resolved here or opened on its block; a
// Zotero link's list can keep them all as they are at once.
function ReviewView({ wsId, kind, onBack, jumpTo }) {
  const [items, busy, resolve, keepAll] = useConflicts(wsId, { kind });
  return (
    <>
      <div className="mirrorPopHead">
        <button className="iconBtn sm" onClick={onBack} title={t("Back")} aria-label={t("Back")}><ArrowLeftIcon size={14} /></button>
        <span className="popoverTitle">{t("Conflicts")}</span>
        {items?.length ? <span className="popoverHint">{items.length}</span> : null}
        {kind === "zotero" && items?.length > 1 ? (
          <span className="mirrorPopBtns">
            <button className="uiBtn sm" disabled={busy} onClick={keepAll} title={t("Keep what this workspace holds for every conflict listed, and mark them resolved")}>
              <CheckIcon size={14} /> {t("Keep all")}
            </button>
          </span>
        ) : null}
      </div>
      {items === null ? <div className="popoverHint">{t("Loading…")}</div>
        : items.length ? (
          <div className="mirrorCards">
            {items.map((c) => (
              <ConflictCard key={c.id} conflict={c} busy={busy} showPage onResolve={resolve} onOpen={() => jumpTo(c.page_id, c.block_id)} />
            ))}
          </div>
        ) : <div className="mirrorState ok"><CheckIcon size={14} /><span>{t("No conflicts — every change merged cleanly.")}</span></div>}
    </>
  );
}

const CHANGE_GLYPH = { add: "+", del: "−", mod: "~", move: "↕", props: "·" };
const CHANGE_TITLE = {
  add: t("block added"), del: t("block removed"), mod: t("text changed"), move: t("block moved"), props: t("properties changed"),
};

// A log row's changes, block by block, as a diff: added and removed blocks
// tinted, a changed block as a word diff of old → new.
export function ChangeList({ changes }) {
  return (
    <ul className="mirrorChanges">
      {changes.map((c, i) => (
        <li key={i} className={`mirrorChange ${c.k}`} title={CHANGE_TITLE[c.k] || c.k}>
          <span className="mirrorChangeGlyph">{CHANGE_GLYPH[c.k] || "·"}</span>
          <span className="mirrorChangeText">
            {c.k === "mod" ? <Marked parts={wordDiff(c.old, c.text)} /> : c.text || <i>{t("(empty)")}</i>}
          </span>
        </li>
      ))}
    </ul>
  );
}

// The log: one row per page a round touched; click a row for its changes,
// the arrow opens the page.
function LogList({ log, kind, jumpTo }) {
  const [openId, setOpenId] = React.useState(null);
  const actionText = (action) => (kind === "zotero" ? (ZOTERO_LOG_ACTIONS[action] ? t(ZOTERO_LOG_ACTIONS[action]) : action) : ACTION_TEXT[action] || action);
  return (
    <ul className="mirrorPopLog">
      {log.map((c) => {
        const Icon = ACTION_ICON[c.action] || RefreshIcon;
        const open = openId === c.id;
        return (
          <li key={c.id} className={open ? "open" : ""}>
            <div className="mirrorPopItemRow">
              <button className="popoverItem mirrorPopItem" aria-expanded={open} onClick={() => setOpenId(open ? null : c.id)}
                title={t("{action} — click for the changes", { action: actionText(c.action) })}>
                <span className="mirrorPopItemTitle"><Icon size={14} /> {c.title || c.page_id}</span>
                <span className="mirrorPopItemMeta">{clock(c.at)}<DiffStat stats={c.stats} /></span>
              </button>
              <button className="ctlBtn mirrorPopOpen" disabled={!c.exists} onClick={() => jumpTo(c.page_id)}
                aria-label={t("Open the page")} title={c.exists ? t("Open the page") : t("The page is gone")}>
                <ExternalLinkIcon size={16} />
              </button>
            </div>
            <Collapse open={open}>
              {c.changes?.length ? <ChangeList changes={c.changes} /> : <div className="popoverHint mirrorChangesNone">{t("No block-level detail for this row.")}</div>}
            </Collapse>
          </li>
        );
      })}
    </ul>
  );
}

const PENDING_GRACE_MS = 6000; // how long a local edit counts as pending before the server confirms it

// `publication` says what the workspace listing knows before the mirror's
// own answer (its page filter) arrives. `kind` is the workspace's link
// ("mirror" | "zotero", syncLinks.js pillLink); `mirrorOf` the name of
// what it follows as the workspace lists it (a clone's origin, a Zotero
// library), else the link's own `remote_name` (a clone detached here keeps
// its pill, though the workspace lists no origin any more);
// `onOpenAccount` opens Settings › Account & sync, where a refused Zotero
// key is mended; `onLinkMoved(path)` hears of a detach or reattach done
// in the pill's settings ("/detach", "/relink"), which changes the
// workspace's session row.
export function MirrorPopover({ wsId, kind = "mirror", mirrorOf: listedAs, publication: listedAsPublication = false, pageId = "", everyPage = false, button = true, open, onToggle, jumpTo, onOpenSettings, onOpenAccount, onLinkMoved }) {
  const zotero = kind === "zotero";
  const [info, setInfo] = React.useState(null);
  const [log, setLog] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [view, setView] = React.useState("main");
  // A local edit the page's session just queued: pending until a poll
  // (after the grace) says the server pushed it.
  const [editAt, setEditAt] = React.useState(0);
  // A round that ran on its own (the loop, an edit) may open or close
  // conflicts: the page's merge chips (App) hear about it through
  // "gamma:mirror-changed", raised when the open conflicts' (count, newest
  // id) moves — not on every round.
  const seenRef = React.useRef("");
  const loadedAtRef = React.useRef(0);
  const load = React.useCallback(async () => {
    loadedAtRef.current = Date.now();
    try {
      const next = await apiJson(`${API}${linkPath(kind, wsId)}`);
      setInfo(next);
      if (!unsentEdits(next) && !next.status?.running) {
        setEditAt((at) => (at && Date.now() - at >= PENDING_GRACE_MS ? 0 : at));
      }
      const mark = `${next.conflicts_open}|${next.conflicts_newest ?? ""}`;
      if (seenRef.current && seenRef.current !== mark) window.dispatchEvent(new CustomEvent("gamma:mirror-changed"));
      seenRef.current = mark;
    } catch {}
  }, [wsId, kind]);
  const loadLog = React.useCallback(async () => {
    try { setLog((await apiJson(`${API}${linkPath(kind, wsId, "/log?limit=20")}`)).changes || []); } catch { setLog([]); }
  }, [wsId, kind]);
  // (between two bounded rounds of a Zotero link's fill the next follows at once)
  const running = Boolean(info?.status?.running) || busy || (zotero && zoteroFollowing(info?.status));
  // a round under way, not one only expected: Sync waits for that alone
  const roundNow = Boolean(info?.status?.running) || busy;
  const pending = Boolean(editAt) || unsentEdits(info);
  const publication = zotero ? false : info ? isPublication(info) : listedAsPublication;
  // A clone syncs the whole workspace, so its pill is on every page; a
  // publication syncs the pages in its filter, so its pill is on those
  // pages only (and nowhere until the filter is known) — unless Settings →
  // Appearance › Top bar › Sync pill says every page (`everyPage`).
  const covers = !publication || everyPage || Boolean(pageId && info?.page_filter?.includes(pageId));
  // With the pill off the bar (`button` false: the same setting hides it)
  // only its popover shows, opened from the account menu's row, on any page.
  const shown = button ? covers : open;
  const coversRef = React.useRef(covers);
  coversRef.current = covers;
  // One read on mount and whenever the pill comes into view (unless a read
  // just ran — the mount read is what shows a publication's pill); the polls
  // only while it is shown and the tab is visible (a tab coming back reads at
  // once). A hidden publication pill still reloads on "gamma:mirror" (a
  // publish or unpublish here changes its filter). Off the bar, opening the
  // popover is the read.
  React.useEffect(() => { if (button && Date.now() - loadedAtRef.current > 2000) load(); }, [load, shown, button]);
  const pollMs = linkPollMs(kind, info, { running, pending });
  React.useEffect(() => {
    if (!shown) return undefined;
    const poll = () => { if (!document.hidden) load(); };
    const t = setInterval(poll, pollMs);
    document.addEventListener("visibilitychange", poll);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", poll); };
  }, [load, pollMs, shown]);
  React.useEffect(() => { if (open) { setView("main"); load(); loadLog(); } }, [open, load, loadLog]);
  const lastSync = info?.status?.last_sync;
  React.useEffect(() => { if (open) loadLog(); }, [open, loadLog, lastSync]);
  React.useEffect(() => {
    if (!open || !running) return undefined;
    const t = setInterval(loadLog, 3000);
    return () => clearInterval(t);
  }, [open, running, loadLog]);
  // Other surfaces (Settings, the row chips) change the mirror too; the
  // page's session says when it queued a local edit.
  React.useEffect(() => {
    const h = () => { load(); if (open) loadLog(); };
    // an edit on a page the pill does not cover (a publication's other pages) waits for nothing
    const edited = () => { if (coversRef.current) setEditAt(Date.now()); };
    window.addEventListener("gamma:mirror", h);
    window.addEventListener("gamma:local-edit", edited);
    return () => { window.removeEventListener("gamma:mirror", h); window.removeEventListener("gamma:local-edit", edited); };
  }, [load, loadLog, open]);

  async function syncNow() {
    setBusy(true);
    setInfo((prev) => prev ? { ...prev, status: { ...prev.status, running: true } } : prev);
    try {
      await apiJson(`${API}${linkPath(kind, wsId, "/sync?wait=1")}`, { method: "POST" });
    } catch {}
    setEditAt(0);
    await load();
    await loadLog();
    setBusy(false);
    window.dispatchEvent(new CustomEvent("gamma:mirror"));
  }

  const mirrorOf = listedAs || info?.remote_name || "";
  const st = mirrorState(info, { busy, pending: Boolean(editAt), kind });
  const s = info?.status || {};
  const host = zotero ? "" : hostOf(info?.remote_url);
  const pull = isPullOnly(info);
  const name = publication ? t("Published to Gamma Cloud") : zotero && info ? zoteroLibraryLabel(info) : mirrorOf;
  const limits = zotero ? zoteroLimits(info) : [];
  if (!shown) return null;
  const pillTitle = zotero ? [t("Linked to Zotero · {library}", { library: name }), "—", t(st.text)]
    : [publication ? name : t("Clone of {name}", { name: mirrorOf }), host ? t("on {host}", { host }) : "", "—", t(st.text)];
  const headTitle = zotero
    ? pull ? t("Receive only: Zotero's changes arrive here, yours stay here") : t("Two-way: your changes go to Zotero, Zotero's arrive here")
    : pull ? t("Receive only: the remote's changes arrive here, yours stay here") : t("Two-way: your changes go to the remote, the remote's arrive here");
  const headHint = publication ? `${n(info?.page_filter?.length, "page")} · ${host}`
    : zotero ? (s.username ? t("Zotero · {user}", { user: s.username }) : t("Zotero"))
    : t("remote · {host}", { host }) + (s.remote_user ? ` · ${s.remote_user}` : "");

  return (
    <span data-popover="mirror" className={`popoverAnchor${button ? "" : " offBar"}`}>
      {button ? (
      <button
        className={`iconBtn mirrorPill ${st.tone} ${open ? "activeIcon" : ""}`}
        data-guide="sync.pill"
        data-state={st.state}
        onClick={onToggle}
        title={pillTitle.filter(Boolean).join(" ")}
        aria-label={t("Sync status")}
      >
        <st.Icon size={16} />
        <span className="barLabel">{t("Sync")}</span>
        {st.badge ? <span className={`mirrorPillBadge ${st.tone}`}>{st.badge}</span>
          : st.dot ? <span className={`mirrorPillDot ${st.tone}`} /> : null}
      </button>
      ) : null}
      {open ? (
        <Popover className="mirrorPopover" role="dialog" aria-label={t("Sync status")}>
          {view === "settings" ? <SettingsView info={info} wsId={wsId} kind={kind} publication={publication} onBack={() => setView("main")} reload={load}
            onOpenSettings={onOpenSettings} onOpenAccount={onOpenAccount} onLinkMoved={onLinkMoved} />
            : view === "review" ? <ReviewView wsId={wsId} kind={kind} onBack={() => setView("main")} jumpTo={jumpTo} />
            : (
              <>
                <div className="mirrorPopHead">
                  <span className="mirrorPopIcon" title={headTitle}>
                    {pull ? <ArrowDownIcon size={16} /> : <ArrowUpDownIcon size={16} />}
                  </span>
                  <span className="mirrorPopTitle">
                    <span className="popoverTitle mirrorEllipsis">{name}</span>
                    <span className="popoverHint mirrorEllipsis" title={info?.remote_url}>{headHint}</span>
                  </span>
                  <span className="mirrorPopBtns">
                    <button className={`iconBtn sm ${running ? "mirrorSpin" : ""}`} disabled={roundNow || info?.detached} onClick={syncNow} aria-label={t("Sync")}
                      title={roundNow ? t("A round is running") : info?.detached ? t("Detached — reattach in the sync settings")
                        : pull ? t(LINK_ACTIONS[linkKind(kind)].receiveNow) : t("Sync now")}>
                      <RefreshIcon size={14} />
                    </button>
                    <button className="iconBtn sm" onClick={() => setView("settings")} title={t("Sync settings")} aria-label={t("Sync settings")}><SettingsIcon size={14} /></button>
                  </span>
                </div>
                <StateBlock info={info} kind={kind} busy={busy} pending={Boolean(editAt)} onConflicts={() => setView("review")} onReconnect={onOpenAccount} />
                {/* what the key may not do: write (receive only), read notes */}
                {limits.length ? (
                  <div className="popoverHint zoteroLimits" title={limits.map((l) => l.title).join(" ")}>
                    <KeyIcon size={14} /> <span>{limits.map((l) => l.tag).join(" · ")}</span>
                  </div>
                ) : null}
                {/* docs/dev/comments.md "Not built": threads are no part of what travels */}
                <div className="popoverHint">{zotero ? t("Discussion threads stay in Gamma. Highlight notes travel as Zotero comments.") : t("Comments stay on each server: a sync carries none.")}</div>
                <div className="popoverLabel"><HistoryIcon size={14} /><span>{t("Log")}</span></div>
                {log === null ? <div className="popoverHint">{t("Loading…")}</div>
                  : log.length ? <LogList log={log} kind={kind} jumpTo={jumpTo} />
                  : <div className="popoverHint">{running ? t("Pages show up here as they are pulled.") : s.last_sync ? t("Nothing pulled or pushed yet.") : zotero ? t("Nothing synced yet.") : t("Nothing cloned yet.")}</div>}
              </>
            )}
        </Popover>
      ) : null}
    </span>
  );
}
