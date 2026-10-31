// Settings → Workspaces → Clones and Zotero links: the mirrors of this
// account — local workspaces that follow a workspace on another Gamma
// server, in git's words a clone and its origin (docs/dev/mirror.md, GUI
// for /api/mirrors*) — and then its Zotero links, personal workspaces kept
// in step with a Zotero library (docs/dev/zotero_sync.md,
// /api/zotero/links*). A row per clone or link, its avatar the state
// (syncing, up to date, a problem, detached): Open, Sync (Reattach when
// detached), the conflicts, and a "more" menu with force pull / force
// push, detach and remove origin (a link's own settings, and Remove
// link). The conflicts view lists the same cards as the row chips, each
// resolved here or opened on its block.
// "Clone a remote workspace" asks for the server address and a write token
// made there (Settings → Integrations on that server), into a new workspace
// or an existing one; "Link a Zotero library…" opens ZoteroLinkDialog, and
// the link it makes opens its workspace, the first sync filling it.
//
// A publication (a mirror with a page filter, the pages a workspace
// publishes to Gamma Cloud; docs/dev/mirror.md "Publishing") is not a clone:
// PublishingSection lists it in Settings → Account & sync with its state, the count
// of published pages and a "more" menu of Sync now and Stop publishing all
// (DELETE /api/pages/{id}/publish for each page), never the clone actions.
import React from "react";
import { guideEvents } from "../guide/events.js";
import { API, apiJson, fmtBytes } from "../shared/lib/utils";
import { Section, SubDialog, Field, DialogButtons, IconChoices, Segmented, Empty, WorkspaceFolder } from "./SettingsKit";
import { ActionMenu, MenuSelect } from "../shared/ui/Menus";
import {
  AlertCircleIcon, ArrowDownIcon, ArrowUpDownIcon, BookIcon, CheckIcon, CloudDownloadIcon, CloudIcon, CloudOffIcon, HardDriveIcon,
  KeyIcon, LinkIcon, MoreIcon, PlusIcon, RefreshIcon, SettingsIcon, TrashIcon, UnlinkIcon, UploadIcon,
} from "../shared/ui/Icons";
import { MorphText } from "../shared/ui/MotionWidgets";
import { clock, hostOf, isPublication, isPullOnly, mirrorState, n, roundSummary, versionGap } from "../collaboration/MirrorPopover";
import { ConflictCard, useConflicts } from "../collaboration/MergeResolver";
import {
  LINK_ACTIONS, LINK_BASES, linkCandidates, linkKind, linkPath, pullByKey, reconnectOnly, unsentEdits, zoteroBytesText, zoteroFollowing,
  zoteroForceConfirm, zoteroLibraryLabel, zoteroLimits, zoteroQueued, zoteroWaitingState,
} from "../collaboration/syncLinks.js";
import { createZoteroLink } from "../transfers/zoteroApi";
import { ZoteroLinkDialog, ZoteroLinkSettingsDialog } from "./ZoteroLinkDialog";
import { useSettingsRead } from "./settingsReads.js";
import { T, t } from "../shared/i18n/i18n.js";

// A list the pane reads (null while loading; [] when not read, or failed
// with nothing read yet), and while one of its rounds runs its row moves
// ("21 of 79 pages", the file in flight): `running` asks the read to come
// again every 2 s, as does a Zotero link between two bounded rounds of one
// fill.
function useListed(enabled, { data, error, reload }, key, running, setRunning) {
  const list = !enabled || (error && !data) ? [] : data ? data[key] || [] : null;
  const now = !!list?.some((m) => m.status?.running || (linkKind(m) === "zotero" && zoteroFollowing(m.status)));
  if (now !== running) setRunning(now);
  const refresh = React.useCallback(() => { reload().catch(() => {}); }, [reload]);
  return [list, refresh];
}

// The account's mirrors (null while loading), the pane's read, kept while
// the dialog is open (settingsReads.js). `enabled` false (a guest, or
// signed out) reads nothing: the endpoint would refuse.
export function useMirrors(enabled = true) {
  const [running, setRunning] = React.useState(false);
  const read = useSettingsRead(enabled ? LINK_BASES.mirror : null, { poll: running ? 2000 : 0 });
  return useListed(enabled, read, "mirrors", running, setRunning);
}

// The account's Zotero links, read as the mirrors are.
export function useZoteroLinks(enabled = true) {
  const [running, setRunning] = React.useState(false);
  const read = useSettingsRead(enabled ? LINK_BASES.zotero : null, { poll: running ? 2000 : 0 });
  return useListed(enabled, read, "links", running, setRunning);
}

// The row's status line: the state in a few words, the file in flight
// while a round runs (a Zotero link's fill: its PDFs' bytes too), the last
// round's totals when up to date, what waits and what is still queued.
export function mirrorStatusLine(m) {
  const s = m.status || {};
  const p = s.progress;
  const st = mirrorState(m);
  const zotero = linkKind(m) === "zotero";
  if (m.detached || m.mode === "off") return t("detached{detached_at} · reattach to merge what both sides did meanwhile", { detached_at: s.detached_at ? ` ${clock(s.detached_at)}` : "" });
  if (st.tone === "busy") {
    const way = p?.file ? (p.file.dir === "up" ? (zotero ? t("sending") : t("pushing")) : zotero ? t("downloading") : t("pulling")) : "";
    const file = p?.file ? ` · ${way} ${p.file.name} ${fmtBytes(p.file.done)}${p.file.total ? ` / ${fmtBytes(p.file.total)}` : ""}` : "";
    if (zotero) {
      const bytes = zoteroBytesText(p);
      return `${t(st.text)}${bytes ? ` · ${bytes}` : ""}${file}`;
    }
    const pages = !p?.total ? t("syncing…")
      : p.first ? t("cloning {done} of {total} pages…", { done: p.done, total: p.total }) : t("syncing {done} of {total} pages…", { done: p.done, total: p.total });
    return `${pages}${file}`;
  }
  const gap = versionGap(m);
  if (gap) return gap === "here" ? t("paused · update this Gamma to sync again") : t("paused · sync resumes once the remote is updated");
  if (s.last_error) return t("problem: {error}", { error: s.last_error });
  if (!s.last_sync) return s.interrupted ? t("interrupted · continues at the next round") : zotero ? t("not synced yet") : t("not cloned yet");
  if (unsentEdits(m)) return zotero ? t("local edits not sent yet · up to date {last_sync}", { last_sync: clock(s.last_sync) })
    : t("local edits not pushed yet · up to date {last_sync}", { last_sync: clock(s.last_sync) });
  const changed = roundSummary(s) || t("nothing had changed");
  if (!zotero) return t("up to date {last_sync} · {changed}", { last_sync: clock(s.last_sync), changed });
  const waiting = zoteroWaitingState(m);
  const queued = zoteroQueued(m) ? t(" · {n} changes queued", { n: Number(s.backlog).toLocaleString() }) : "";
  const line = queued ? t("last round {last_sync} · {changed}", { last_sync: clock(s.last_sync), changed })
    : t("up to date {last_sync} · {changed}", { last_sync: clock(s.last_sync), changed });
  return `${waiting ? `${waiting.text} · ` : ""}${line}${queued}`;
}

// Busy flag plus "Sync now" for one mirror or link, waiting for the round
// and reporting its outcome.
function useSyncNow(refresh, setStatus) {
  const [busy, setBusy] = React.useState(false);
  async function syncNow(m) {
    setBusy(true);
    try {
      const d = await apiJson(`${API}${linkPath(m, m.workspace_id, "/sync?wait=1")}`, { method: "POST" });
      const s = d.status || {};
      const side = roundSummary(s) || t("nothing had changed on either side");
      // (a Zotero link's round is bounded: what it left goes in the next rounds)
      const left = linkKind(m) === "zotero" ? Number(s.backlog) || 0 : 0;
      setStatus?.(versionGap(d) ? mirrorState(d).title
        : s.last_error ? t("Sync problem: {last_error}", { last_error: s.last_error })
        : left ? t("Round done — {side}; {n} queued changes go in the next rounds.", { side, n: left.toLocaleString() })
        : t("Up to date — {side}.", { side }));
    } catch (err) {
      setStatus?.(t("Sync failed: {message}", { message: err.message }));
    } finally {
      setBusy(false);
      refresh();
    }
  }
  return [busy, setBusy, syncNow];
}

const DIRECTION_TILES = [
  { value: "two-way", label: T("Two-way"), hint: T("your edits go to the remote too"), Icon: ArrowUpDownIcon },
  { value: "pull", label: T("Receive only"), hint: T("the remote's edits arrive here"), Icon: ArrowDownIcon },
];

export function MirrorDialog({ busy, error, onSubmit, onClose, candidates = [] }) {
  const [url, setUrl] = React.useState("");
  const [token, setToken] = React.useState("");
  const [name, setName] = React.useState("");
  const [mode, setMode] = React.useState("two-way");
  const [into, setInto] = React.useState("");
  const [adopt, setAdopt] = React.useState("theirs");
  const ok = url.trim() && token.trim();
  return (
    <SubDialog title={t("Clone a remote workspace")} onClose={onClose} draft={url || token}>
      <div className="settingsForm">
      <Field label={t("Origin server")} hint={t("The other Gamma, e.g. https://nas.local:8000")}>
        <input className="aiKeyInput" value={url} autoFocus placeholder="https://" onChange={(e) => setUrl(e.target.value)} />
      </Field>
      <Field label={t("Token")} hint={t("Made on that server: Settings → Integrations → Manual setup, with the “Read and write” scope, for the workspace to clone")}>
        <input className="aiKeyInput" type="password" value={token} placeholder={t("gamma_…")} onChange={(e) => setToken(e.target.value)} />
      </Field>
      {candidates.length ? (
        <Field label={t("Into")} hint={t("A new workspace, or one of yours that already holds a copy (an imported backup, a clone whose origin was removed)")}>
          <MenuSelect block value={into} onChange={setInto}
            options={[["", t("A new workspace")], ...candidates.map((w) => [w.id, w.name])]} />
        </Field>
      ) : null}
      {into ? (
        <Field label={t("If a page differs")} hint={t("Both versions are kept; the other waits under Conflicts")}>
          <Segmented value={adopt} onChange={setAdopt} options={[["theirs", t("Take remote's")], ["mine", t("Keep local")]]} />
        </Field>
      ) : (
        <Field label={t("Name here")} hint={t("Optional — defaults to the origin workspace's name")}>
          <input className="aiKeyInput" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
      )}
      <Field label={t("Direction")}>
        <IconChoices label={t("Direction")} value={mode} onChange={setMode} options={DIRECTION_TILES} />
      </Field>
      {error ? <div className="settingsPaneHint aiKeysError">{error}</div> : null}
      <DialogButtons footnote={ok ? null : t("Fill in the server and its token to continue.")}>
        <button className="uiBtn" onClick={onClose} disabled={busy}>{t("Cancel")}</button>
        <button className="uiBtn primary" disabled={!ok || busy}
          onClick={() => onSubmit({ remote_url: url.trim(), token: token.trim(), name: name.trim(), mode, workspace_id: into, adopt })}>
          <MorphText>{busy ? t("Connecting…") : t("Clone")}</MorphText>
        </button>
      </DialogButtons>
      </div>
    </SubDialog>
  );
}

export function MirrorConflicts({ mirror, onClose, setStatus, closeSettings }) {
  const onError = React.useCallback((message) => setStatus?.(t("Could not resolve: {message}", { message })), [setStatus]);
  const kind = linkKind(mirror);
  const [items, busy, resolve, keepAll] = useConflicts(mirror.workspace_id, { onError, kind });
  function open(c) {
    closeSettings?.();
    window.dispatchEvent(new CustomEvent("gamma:jump", { detail: { page: c.page_id, block: c.block_id } }));
  }
  return (
    <Section title={t("Conflicts · {name}", { name: mirror.name })} action={(
      <span className="aiProvActions">
        {kind === "zotero" && items?.length > 1 ? (
          <button className="uiBtn sm" disabled={busy} onClick={keepAll} title={t("Keep what this workspace holds for every conflict listed, and mark them resolved")}>
            <CheckIcon size={14} /> {t("Keep all")}
          </button>
        ) : null}
        <button className="uiBtn sm" onClick={onClose}>{t("Back")}</button>
      </span>
    )}>
      {items === null ? <Empty icon={HardDriveIcon} loading>{t("Loading…")}</Empty>
        : items.length ? <div className="mirrorCards">{items.map((c) => <ConflictCard key={c.id} conflict={c} busy={busy} showPage onResolve={resolve} onOpen={open} />)}</div>
        : <Empty icon={CheckIcon}>{t("No conflicts — every change merged cleanly.")}</Empty>}
    </Section>
  );
}

// `links` are the account's Zotero links (useZoteroLinks); `onAccount`
// opens Account & sync › Zotero, where a key Zotero refuses is replaced.
// `refreshSession` reads the session's workspace rows again (App's): the
// open workspace linked, reattached or detached here changes what its
// pages show (the pill, a PDF card's Download now). `switchWorkspace`
// moves the tab to another workspace, a navigation whose session read
// lists a workspace made here.
export function MirrorsSection({ mirrors, links, refresh, workspaces, currentId, switchWorkspace, closeSettings, confirm, setStatus, onAccount,
  refreshSession }) {
  const [creating, setCreating] = React.useState(null); // "clone" | "zotero" | null
  const [createError, setCreateError] = React.useState("");
  const [busy, setBusy, syncNow] = useSyncNow(refresh, setStatus);
  const [conflictsOf, setConflictsOf] = React.useState(null);
  const [settingsOf, setSettingsOf] = React.useState(null);
  const byWs = Object.fromEntries((workspaces || []).map((w) => [w.id, w]));

  async function submit(body) {
    setBusy(true);
    setCreateError("");
    try {
      await apiJson(`${API}${LINK_BASES.mirror}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      setCreating(null);
      setStatus?.(t("Cloning in the background."));
      refresh();
      setTimeout(refresh, 4000);
      if (body.workspace_id && body.workspace_id === currentId) refreshSession?.();
    } catch (err) {
      setCreateError(err.message);
    } finally {
      setBusy(false);
    }
  }
  // A link made opens its workspace, as a row's Open does: Settings closes
  // and the tab moves there, where the pill shows the first sync filling
  // it. The workspace open here, linked, stays open: its session row is
  // read again, so its pill and its pages' PDF cards follow at once.
  async function submitLink(body) {
    setBusy(true);
    setCreateError("");
    try {
      const link = await createZoteroLink(body);
      // before the switch below reloads the app: a setup guide resumes there
      guideEvents.emit("zotero.linked", { workspace: link.workspace_id });
      setCreating(null);
      setStatus?.(t("Syncing with Zotero in the background."));
      closeSettings?.();
      if (link.workspace_id === currentId) refreshSession?.();
      else switchWorkspace(link.workspace_id);
    } catch (err) {
      setCreateError(err.message);
    } finally {
      setBusy(false);
    }
  }
  async function call(m, path, init, ok, gone = false) {
    setBusy(true);
    try {
      await apiJson(`${API}${linkPath(m, m.workspace_id, path)}`, init);
      if (ok) setStatus?.(ok);
    } catch (err) {
      setStatus?.(t("Could not do that: {message}", { message: err.message }));
    } finally {
      setBusy(false);
      refresh();
      // the page's pill and merge chips follow; a forgotten link is told apart (nothing to reload)
      if (!gone) window.dispatchEvent(new CustomEvent("gamma:mirror"));
      // the open workspace's link on or off: the session row says so (zotero_of, mirror_of)
      if (m.workspace_id === currentId && (path === "/relink" || path === "/detach")) refreshSession?.();
    }
  }
  const json = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  function nameOf(m) {
    if (linkKind(m) === "zotero") return m.name || byWs[m.workspace_id]?.name || zoteroLibraryLabel(m);
    return m.name || byWs[m.workspace_id]?.name || t("this clone");
  }
  function force(m, direction) {
    const zotero = linkKind(m) === "zotero";
    const library = zoteroLibraryLabel(m);
    confirm({
      title: direction === "pull" ? t("Force pull") : t("Force push"),
      message: zotero
        ? zoteroForceConfirm(direction, { library, name: nameOf(m) })
        : direction === "pull"
          ? t("Make “{m}” identical to {remote_name} on {remote_url}? Only differing pages are written; your texts it replaces or removes are kept as conflicts.", { m: nameOf(m), remote_name: m.remote_name, remote_url: hostOf(m.remote_url) })
          : t("Make {remote_name} on {remote_url} identical to “{m}”? Only differing pages are written; origin's texts it replaces or removes are kept as conflicts.", { remote_name: m.remote_name, remote_url: hostOf(m.remote_url), m: nameOf(m) }),
      confirmLabel: direction === "pull" ? t("Force pull") : t("Force push"), danger: true,
      onConfirm: () => call(m, "/force", json({ direction }), t("{action} running in the background.", { action: direction === "pull" ? t("Force pull") : t("Force push") })),
    });
  }
  function forget(m) {
    const zotero = linkKind(m) === "zotero";
    confirm({
      title: LINK_ACTIONS[linkKind(m)].remove,
      message: zotero
        ? t("“{name}” stays as an ordinary workspace of yours and {library} stays in Zotero; they never sync again.", { name: nameOf(m), library: zoteroLibraryLabel(m) })
        : t("“{name}” stays as an ordinary workspace of yours; it never pulls from or pushes to {remote} again.", { name: nameOf(m), remote: m.remote_name }),
      confirmLabel: t(LINK_ACTIONS[linkKind(m)].remove), danger: true,
      onConfirm: async () => {
        if (m.workspace_id === currentId) window.dispatchEvent(new CustomEvent("gamma:mirror-gone"));
        await call(m, "", { method: "DELETE" }, undefined, true);
      },
    });
  }
  async function patchLink(m, body) {
    const next = await apiJson(`${API}${linkPath(m, m.workspace_id)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    refresh();
    window.dispatchEvent(new CustomEvent("gamma:mirror"));
    return next;
  }

  if (conflictsOf) {
    return <MirrorConflicts mirror={conflictsOf} setStatus={setStatus} closeSettings={closeSettings} onClose={() => { setConflictsOf(null); refresh(); }} />;
  }

  function row(m) {
    const zotero = linkKind(m) === "zotero";
    const current = m.workspace_id === currentId;
    const s = m.status || {};
    const detached = m.detached || m.mode === "off";
    const pullOnly = isPullOnly(m);
    const st = mirrorState(m);
    const words = LINK_ACTIONS[linkKind(m)];
    const more = detached ? [
      { icon: LinkIcon, label: T("Reattach"), title: words.reattach,
        onClick: () => call(m, "/relink", json({}), t("Reattached — syncing in the background.")) },
    ] : [
      ...(zotero ? [{ icon: SettingsIcon, label: T("Link settings…"), title: T("When Zotero's changes come in, the direction, and what PDFs and tags travel"),
        onClick: () => setSettingsOf(m) }] : []),
      { icon: CloudDownloadIcon, label: T("Force pull"), title: words.forcePull, onClick: () => force(m, "pull") },
      { icon: UploadIcon, label: T("Force push"), title: pullOnly ? words.pushBlocked : words.forcePush, disabled: pullOnly, onClick: () => force(m, "push") },
      { icon: UnlinkIcon, label: T("Detach"), title: words.detach,
        onClick: () => call(m, "/detach", { method: "POST" }, t("Detached — reattach whenever you like.")) },
    ];
    more.push({ icon: TrashIcon, label: words.remove, danger: true, title: words.removeTitle, onClick: () => forget(m) });
    // a key only a new connection mends: Reconnect in Sync's place (PDFs waiting for file access: beside it)
    const reconnect = st.action === "reconnect" && onAccount;
    const onlyReconnect = reconnect && reconnectOnly(st);
    const keyPull = zotero && pullByKey(m);
    const limits = zotero ? zoteroLimits(m) : [];
    return (
      <div key={`${zotero ? "zotero" : "clone"}:${m.workspace_id}`} className="aiProvRow" data-link={zotero ? "zotero" : undefined}>
        <span className={`aiProvAvatar mirrorAvatar ${st.tone} ${current ? "active" : ""}`} title={t(st.title)}>
          <st.Icon size={16} />
        </span>
        <span className="aiProvMeta">
          <span className="aiProvName">
            {nameOf(m)}
            {current ? <span className="uiTag">{t("open")}</span> : null}
            {pullOnly && !keyPull ? (
              <span className="uiTag" title={zotero ? t("Zotero's changes arrive here; yours stay here until you switch to two-way")
                : t("The remote's changes arrive here; yours stay here until you switch to two-way")}>
                {t("receive only")}
              </span>
            ) : null}
            {/* what the link's key may not do: write (receive only), read notes */}
            {limits.map((l) => <span key={l.tag} className="uiTag" title={l.title}>{l.tag}</span>)}
            {detached ? <span className="uiTag">{t("detached")}</span> : null}
            {!detached && s.last_error ? (
              <span className="uiTag warn" title={zotero ? t(st.title) : undefined}>
                {versionGap(m) ? t("update needed") : st.action === "reconnect" ? t("reconnect Zotero") : t("problem")}
              </span>
            ) : null}
            {!detached && !s.last_error && st.state === "waiting" ? <span className="uiTag warn" title={t(st.title)}>{t("waiting")}</span> : null}
            {!detached && !s.last_error && unsentEdits(m) ? (zotero
              ? <span className="uiTag pending" title={t("Local edits the next round sends")}>{t("unsent edits")}</span>
              : <span className="uiTag pending" title={t("Local edits the next round pushes")}>{t("unpushed edits")}</span>) : null}
            {m.conflicts_open ? <span className="uiTag warn">{n(m.conflicts_open, "conflict")}</span> : null}
          </span>
          {zotero ? (
            <span className="aiProvDesc" title={m.remote_url}>
              <BookIcon size={14} /> {t("Zotero")} · {zoteroLibraryLabel(m)}{s.username ? ` · ${s.username}` : ""}{" · "}
              <WorkspaceFolder id={m.workspace_id} />
            </span>
          ) : (
            <span className="aiProvDesc" title={m.remote_url}>
              {t("clone of")} {m.remote_name} {t("· origin")} {hostOf(m.remote_url)}{" · "}
              <WorkspaceFolder id={m.workspace_id} />
            </span>
          )}
          <span className="aiProvDesc">{mirrorStatusLine(m)}</span>
        </span>
        <span className="aiProvActions">
          {!current ? <button className="uiBtn sm" onClick={() => { closeSettings?.(); switchWorkspace(m.workspace_id); }}>{t("Open")}</button> : null}
          {reconnect ? (
            <button className="uiBtn sm primary" onClick={onAccount} title={t("Settings › Account & sync › Zotero")}>
              <KeyIcon size={14} /> {t("Reconnect")}
            </button>
          ) : null}
          {onlyReconnect ? null : detached ? (
            <button className="uiBtn sm primary" disabled={busy} title={t(words.reattach)}
              onClick={() => call(m, "/relink", json({}), t("Reattached — syncing in the background."))}>
              <LinkIcon size={14} /> {t("Reattach")}
            </button>
          ) : (
            <button className="uiBtn sm" disabled={busy || s.running} onClick={() => syncNow(m)}
              title={pullOnly ? t(words.receiveNow) : t("Sync now")}>
              <RefreshIcon size={14} /> {t("Sync")}
            </button>
          )}
          <button className={`uiBtn sm ${m.conflicts_open ? "primary" : ""}`} disabled={busy} onClick={() => setConflictsOf({ ...m, name: nameOf(m) })}
            title={zotero ? t("Changes both sides made: Gamma's was kept; Zotero's waits here for you to take it or keep Gamma's")
              : t("Blocks both sides changed: the sync merged them or took one side; they wait here for you to resolve")}>
            <AlertCircleIcon size={14} /> {t("Conflicts")}{m.conflicts_open ? ` (${m.conflicts_open})` : ""}
          </button>
          <ActionMenu label={t("More")} icon={MoreIcon} iconOnly disabled={busy} items={more} />
        </span>
      </div>
    );
  }

  const clones = (mirrors || []).filter((m) => !isPublication(m));
  const zoteroLinks = links || [];
  // the workspaces "Into" offers (a publication emptied of its pages holds none for a Zotero link), read
  // once the account's clones and links are: until then the dialogs do not open
  const loading = mirrors === null || links === null;
  const candidates = (kind) => linkCandidates(workspaces, { kind, mirrors, links: zoteroLinks });

  return (
    <>
      <Section
        title={t("Clones and Zotero links")}
        action={(
          <span className="aiProvActions">
            <button className="uiBtn sm" disabled={busy || loading} onClick={() => { setCreateError(""); setCreating("clone"); }}>
              <PlusIcon size={14} /> {t("Clone a remote workspace")}
            </button>
            <button className="uiBtn sm" disabled={busy || loading} data-guide="settings.zoteroLink" onClick={() => { setCreateError(""); setCreating("zotero"); }}>
              <BookIcon size={14} /> {t("Link a Zotero library…")}
            </button>
          </span>
        )}
      >
        {loading ? <Empty icon={CloudDownloadIcon} loading>{t("Loading…")}</Empty>
          : clones.length || zoteroLinks.length ? [...clones.map(row), ...zoteroLinks.map(row)]
          : <Empty icon={CloudDownloadIcon}>
              <span>{t("No clones or Zotero links yet.")}</span>
              <span className="settingDesc">{t("A clone follows a workspace on another Gamma server, pulling and pushing changes, and opens without a connection.")}</span>
              <span className="settingDesc">{t("A Zotero link keeps a workspace in step with a Zotero library, both ways.")}</span>
            </Empty>}
      </Section>
      {creating === "clone" ? (
        <MirrorDialog busy={busy} error={createError} onSubmit={submit} onClose={() => setCreating(null)}
          candidates={candidates("mirror")} />
      ) : null}
      {creating === "zotero" ? (
        <ZoteroLinkDialog busy={busy} error={createError} onSubmit={submitLink} onClose={() => setCreating(null)}
          linked={zoteroLinks.map((l) => l.library)}
          candidates={candidates("zotero")} />
      ) : null}
      {settingsOf ? (
        <ZoteroLinkSettingsDialog link={{ ...settingsOf, name: nameOf(settingsOf) }} patch={(body) => patchLink(settingsOf, body)}
          onClose={() => setSettingsOf(null)} />
      ) : null}
    </>
  );
}

// Settings → Account & sync → Publishing: the workspaces that publish pages to Gamma
// Cloud, each with its state, the count of pages, Conflicts when any wait,
// and a "more" menu of Sync now and Stop publishing all.
export function PublishingSection({ mirrors, refresh, currentId, closeSettings, confirm, setStatus }) {
  const [busy, setBusy, syncNow] = useSyncNow(refresh, setStatus);
  const [conflictsOf, setConflictsOf] = React.useState(null);
  const nameOf = (m) => m.name || t("this workspace");

  // Stop publishing every page of a publication: one DELETE per page, in
  // that workspace (?ws=), each stopping the share there and deleting the copy.
  function stopPublishing(m) {
    const pages = m.page_filter || [];
    confirm({
      title: T("Stop publishing all"),
      message: t("The {pages} of “{name}” leave Gamma Cloud: their cloud links stop working and the copies there are deleted. The pages here stay.", { pages: n(pages.length, "published page"), name: nameOf(m) }),
      confirmLabel: t("Stop publishing"), danger: true,
      onConfirm: async () => {
        setBusy(true);
        const failed = [];
        for (const id of pages) {
          try {
            await apiJson(`${API}/pages/${encodeURIComponent(id)}/publish?ws=${encodeURIComponent(m.workspace_id)}`, { method: "DELETE" });
          } catch (err) {
            failed.push(err.message);
          }
        }
        setBusy(false);
        setStatus?.(failed.length ? t("Could not stop {page}: {failed}", { page: n(failed.length, "page"), failed: failed[0] }) : t("Stopped publishing."));
        refresh();
        window.dispatchEvent(new CustomEvent("gamma:mirror"));
      },
    });
  }

  if (conflictsOf) {
    return <MirrorConflicts mirror={conflictsOf} setStatus={setStatus} closeSettings={closeSettings} onClose={() => { setConflictsOf(null); refresh(); }} />;
  }

  // A publication's row: its state, the pages it publishes, Sync now and
  // Stop publishing all.
  function publicationRow(m) {
    const current = m.workspace_id === currentId;
    const s = m.status || {};
    const st = mirrorState(m);
    const pages = m.page_filter || [];
    const more = [
      { icon: RefreshIcon, label: T("Sync now"), disabled: s.running || m.detached || m.mode === "off",
        title: T("Send local edits and bring back edits made through the cloud links"), onClick: () => syncNow(m) },
      { icon: CloudOffIcon, label: T("Stop publishing all"), danger: true, disabled: !pages.length,
        title: T("Every page leaves Gamma Cloud; the pages here stay"), onClick: () => stopPublishing(m) },
    ];
    return (
      <div key={m.workspace_id} className="aiProvRow" data-publication={m.workspace_id}>
        <span className={`aiProvAvatar mirrorAvatar ${st.tone} ${current ? "active" : ""}`} title={t(st.title)}>
          <st.Icon size={16} />
        </span>
        <span className="aiProvMeta">
          <span className="aiProvName">
            {nameOf(m)}
            {current ? <span className="uiTag">{t("open")}</span> : null}
            {s.last_error ? <span className="uiTag warn">{versionGap(m) ? t("update needed") : t("problem")}</span> : null}
            {!s.last_error && m.pending_local ? <span className="uiTag pending" title={t("Local edits the next round sends")}>{t("unsynced edits")}</span> : null}
            {m.conflicts_open ? <span className="uiTag warn">{n(m.conflicts_open, "conflict")}</span> : null}
          </span>
          <span className="aiProvDesc" title={m.remote_url}>
            {n(pages.length, "published page")} · {hostOf(m.remote_url)}
          </span>
          <span className="aiProvDesc">{mirrorStatusLine(m)}</span>
        </span>
        <span className="aiProvActions">
          {m.conflicts_open ? (
            <button className="uiBtn sm primary" disabled={busy} onClick={() => setConflictsOf({ ...m, name: nameOf(m) })}
              title={t("Blocks edited both here and through a cloud link wait for you to resolve")}>
              <AlertCircleIcon size={14} /> {t("Conflicts ({n})", { n: m.conflicts_open })}
            </button>
          ) : null}
          <ActionMenu label={t("More")} icon={MoreIcon} iconOnly disabled={busy} items={more} />
        </span>
      </div>
    );
  }

  // a publication with nothing published (its last page unpublished) is not shown
  const publications = (mirrors || []).filter((m) => isPublication(m) && (m.page_filter || []).length);
  return (
    <Section title={t("Publishing")}>
      {mirrors === null ? <Empty icon={CloudIcon} loading>{t("Loading…")}</Empty>
        : publications.length ? publications.map(publicationRow)
        : <Empty icon={CloudIcon}>
            <span>{t("No published pages yet.")}</span>
            <span className="settingDesc">{t("Publish a page from its Share button to give it a Gamma Cloud link; its edits sync both ways.")}</span>
          </Empty>}
    </Section>
  );
}
