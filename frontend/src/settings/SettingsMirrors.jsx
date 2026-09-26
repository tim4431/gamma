// Settings → Account & sync → Clones: the mirrors of this account — local
// workspaces that follow a workspace on another Gamma server, in git's
// words a clone and its origin (docs/dev/mirror.md, GUI for /api/mirrors*).
// A row per clone, its avatar the clone's state (syncing, up to date, a
// problem, detached): Open, Sync (Reattach when detached), the
// conflicts, and a "more" menu with force pull / force push, detach and
// remove origin. The conflicts view lists the same cards as the row chips,
// each resolved here or opened on its block. "Clone a remote workspace"
// asks for the server address and a write token made there (Settings →
// Integrations on that server), into a new workspace or an existing one.
//
// A publication (a mirror with a page filter, the pages a workspace
// publishes to Gamma Cloud; docs/dev/mirror.md "Publishing") is not a clone:
// PublishingSection lists it in Settings → Account & sync with its state, the count
// of published pages and a "more" menu of Sync now and Stop publishing all
// (DELETE /api/pages/{id}/publish for each page), never the clone actions.
import React from "react";
import { API, apiJson, fmtBytes } from "../shared/lib/utils";
import { Section, SubDialog, Field, IconChoices, Segmented, Empty, WorkspaceFolder } from "./SettingsKit";
import { ActionMenu, MenuSelect } from "../shared/ui/Menus";
import {
  AlertCircleIcon, ArrowDownIcon, ArrowUpDownIcon, CheckIcon, CloudDownloadIcon, CloudIcon, CloudOffIcon, HardDriveIcon, LinkIcon,
  MoreIcon, PlusIcon, RefreshIcon, TrashIcon, UnlinkIcon, UploadIcon,
} from "../shared/ui/Icons";
import { clock, hostOf, isPublication, isPullOnly, mirrorState, n, roundSummary } from "../collaboration/MirrorPopover";
import { ConflictCard, useConflicts } from "../collaboration/MergeResolver";
import { T, t } from "../shared/i18n/i18n.js";

// The account's mirrors (null while loading). `enabled` false (a guest, or
// signed out) reads nothing: the endpoint would refuse.
export function useMirrors(enabled = true) {
  const [mirrors, setMirrors] = React.useState(null);
  const refresh = React.useCallback(() => {
    if (!enabled) { setMirrors([]); return; }
    apiJson(`${API}/mirrors`).then((d) => setMirrors(d.mirrors || [])).catch(() => setMirrors([]));
  }, [enabled]);
  React.useEffect(() => { refresh(); }, [refresh]);
  // while a round runs its row moves ("21 of 79 pages", the file in flight): read again every 2 s
  const running = !!mirrors?.some((m) => m.status?.running);
  React.useEffect(() => {
    if (!running) return undefined;
    const id = setInterval(refresh, 2000);
    return () => clearInterval(id);
  }, [running, refresh]);
  return [mirrors, refresh];
}

// The row's status line: the state in a few words, the file in flight
// while a round runs, the last round's totals when up to date.
export function mirrorStatusLine(m) {
  const s = m.status || {};
  const p = s.progress;
  const st = mirrorState(m);
  if (m.detached || m.mode === "off") return t("detached{detached_at} · reattach to merge what both sides did meanwhile", { detached_at: s.detached_at ? ` ${clock(s.detached_at)}` : "" });
  if (st.tone === "busy") {
    const file = p?.file ? ` · ${p.file.dir === "up" ? t("pushing") : t("pulling")} ${p.file.name} ${fmtBytes(p.file.done)}${p.file.total ? ` / ${fmtBytes(p.file.total)}` : ""}` : "";
    const pages = p?.total
      ? (p.first ? t("cloning {done} of {total} pages…", { done: p.done, total: p.total }) : t("syncing {done} of {total} pages…", { done: p.done, total: p.total }))
      : t("syncing…");
    return `${pages}${file}`;
  }
  if (s.last_error) return t("problem: {error}", { error: s.last_error });
  if (!s.last_sync) return s.interrupted ? t("interrupted · continues at the next round") : t("not cloned yet");
  if (m.pending_local) return t("local edits not pushed yet · up to date {last_sync}", { last_sync: clock(s.last_sync) });
  return t("up to date {last_sync} · {changed}", { last_sync: clock(s.last_sync), changed: roundSummary(s) || t("nothing had changed") });
}

// Busy flag plus "Sync now" for one mirror, waiting for the round and
// reporting its outcome.
function useSyncNow(refresh, setStatus) {
  const [busy, setBusy] = React.useState(false);
  async function syncNow(m) {
    setBusy(true);
    try {
      const d = await apiJson(`${API}/mirrors/${encodeURIComponent(m.workspace_id)}/sync?wait=1`, { method: "POST" });
      const s = d.status || {};
      setStatus?.(s.last_error ? t("Sync problem: {last_error}", { last_error: s.last_error })
        : t("Up to date — {side}.", { side: roundSummary(s) || t("nothing had changed on either side") }));
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
      <Field label={t("Origin server")} hint={t("the other Gamma, e.g. https://nas.local:8000")}>
        <input className="aiKeyInput" value={url} autoFocus placeholder="https://" onChange={(e) => setUrl(e.target.value)} />
      </Field>
      <Field label={t("Token")} hint={t("made on that server: Settings → Integrations → Manual setup, with the “Read and write” scope, for the workspace to clone")}>
        <input className="aiKeyInput" type="password" value={token} placeholder={t("gamma_…")} onChange={(e) => setToken(e.target.value)} />
      </Field>
      {candidates.length ? (
        <Field label={t("Into")} hint={t("a new workspace, or one of yours that already holds a copy (an imported backup, a clone whose origin was removed)")}>
          <MenuSelect block value={into} onChange={setInto}
            options={[["", t("A new workspace")], ...candidates.map((w) => [w.id, w.name])]} />
        </Field>
      ) : null}
      {into ? (
        <Field label={t("If a page differs")} hint={t("both versions are kept; the other waits under Conflicts")}>
          <Segmented value={adopt} onChange={setAdopt} options={[["theirs", t("Take remote's")], ["mine", t("Keep local")]]} />
        </Field>
      ) : (
        <Field label={t("Name here")} hint={t("optional — defaults to the origin workspace's name")}>
          <input className="aiKeyInput" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
      )}
      <Field label={t("Direction")}>
        <IconChoices label={t("Direction")} value={mode} onChange={setMode} options={DIRECTION_TILES} />
      </Field>
      {error ? <div className="settingsPaneHint aiKeysError">{error}</div> : null}
      <div className="reportModalBtns">
        <button className="uiBtn" onClick={onClose} disabled={busy}>{t("Cancel")}</button>
        <button className="uiBtn primary" disabled={!ok || busy}
          onClick={() => onSubmit({ remote_url: url.trim(), token: token.trim(), name: name.trim(), mode, workspace_id: into, adopt })}>
          {busy ? t("Connecting…") : t("Clone")}
        </button>
      </div>
      </div>
    </SubDialog>
  );
}

export function MirrorConflicts({ mirror, onClose, setStatus, closeSettings }) {
  const onError = React.useCallback((message) => setStatus?.(t("Could not resolve: {message}", { message })), [setStatus]);
  const [items, busy, resolve] = useConflicts(mirror.workspace_id, { onError });
  function open(c) {
    closeSettings?.();
    window.dispatchEvent(new CustomEvent("gamma:jump", { detail: { page: c.page_id, block: c.block_id } }));
  }
  return (
    <Section title={t("Conflicts · {name}", { name: mirror.name })} action={<button className="uiBtn sm" onClick={onClose}>{t("Back")}</button>}>
      {items === null ? <Empty icon={HardDriveIcon}>{t("Loading…")}</Empty>
        : items.length ? <div className="mirrorCards">{items.map((c) => <ConflictCard key={c.id} conflict={c} busy={busy} showPage onResolve={resolve} onOpen={open} />)}</div>
        : <Empty icon={CheckIcon}>{t("No conflicts — every change merged cleanly.")}</Empty>}
    </Section>
  );
}

export function MirrorsSection({ mirrors, refresh, workspaces, currentId, switchWorkspace, closeSettings, confirm, setStatus }) {
  const [creating, setCreating] = React.useState(false);
  const [createError, setCreateError] = React.useState("");
  const [busy, setBusy, syncNow] = useSyncNow(refresh, setStatus);
  const [conflictsOf, setConflictsOf] = React.useState(null);
  const byWs = Object.fromEntries((workspaces || []).map((w) => [w.id, w]));

  async function submit(body) {
    setBusy(true);
    setCreateError("");
    try {
      await apiJson(`${API}/mirrors`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      setCreating(false);
      setStatus?.(t("Cloning in the background."));
      refresh();
      setTimeout(refresh, 4000);
    } catch (err) {
      setCreateError(err.message);
    } finally {
      setBusy(false);
    }
  }
  async function call(m, path, init, ok, gone = false) {
    setBusy(true);
    try {
      await apiJson(`${API}/mirrors/${encodeURIComponent(m.workspace_id)}${path}`, init);
      if (ok) setStatus?.(ok);
    } catch (err) {
      setStatus?.(t("Could not do that: {message}", { message: err.message }));
    } finally {
      setBusy(false);
      refresh();
      // the page's pill and merge chips follow; a forgotten link is told apart (nothing to reload)
      if (!gone) window.dispatchEvent(new CustomEvent("gamma:mirror"));
    }
  }
  const json = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  function nameOf(m) {
    return m.name || byWs[m.workspace_id]?.name || t("this clone");
  }
  function force(m, direction) {
    confirm({
      title: direction === "pull" ? t("Force pull") : t("Force push"),
      message: direction === "pull"
        ? t("Make “{m}” identical to {remote_name} on {remote_url}? Only differing pages are written; where texts differ, yours are kept as conflicts.", { m: nameOf(m), remote_name: m.remote_name, remote_url: hostOf(m.remote_url) })
        : t("Make {remote_name} on {remote_url} identical to “{m}”? Only differing pages are written; where texts differ, origin's are kept as conflicts.", { remote_name: m.remote_name, remote_url: hostOf(m.remote_url), m: nameOf(m) }),
      confirmLabel: direction === "pull" ? t("Force pull") : t("Force push"), danger: true,
      onConfirm: () => call(m, "/force", json({ direction }), t("{action} running in the background.", { action: direction === "pull" ? t("Force pull") : t("Force push") })),
    });
  }
  function forget(m) {
    confirm({
      title: T("Remove origin"),
      message: t("“{name}” stays as an ordinary workspace of yours; it never pulls from or pushes to {remote} again.", { name: nameOf(m), remote: m.remote_name }),
      confirmLabel: t("Remove origin"), danger: true,
      onConfirm: async () => {
        if (m.workspace_id === currentId) window.dispatchEvent(new CustomEvent("gamma:mirror-gone"));
        await call(m, "", { method: "DELETE" }, undefined, true);
      },
    });
  }

  if (conflictsOf) {
    return <MirrorConflicts mirror={conflictsOf} setStatus={setStatus} closeSettings={closeSettings} onClose={() => { setConflictsOf(null); refresh(); }} />;
  }

  function row(m) {
    const current = m.workspace_id === currentId;
    const s = m.status || {};
    const detached = m.detached || m.mode === "off";
    const pullOnly = isPullOnly(m);
    const st = mirrorState(m);
    const more = detached ? [
      { icon: LinkIcon, label: T("Reattach"), title: T("Follow origin again; what both sides did meanwhile merges"),
        onClick: () => call(m, "/relink", json({}), t("Reattached — syncing in the background.")) },
    ] : [
      { icon: CloudDownloadIcon, label: T("Force pull"), title: T("Make this clone identical to origin"), onClick: () => force(m, "pull") },
      { icon: UploadIcon, label: T("Force push"), title: pullOnly ? t("A receive-only clone cannot force push") : t("Make the remote identical to this clone"),
        disabled: pullOnly, onClick: () => force(m, "push") },
      { icon: UnlinkIcon, label: T("Detach"), title: T("Stop pulling and pushing for now; origin is kept, so reattaching merges what both sides did meanwhile"),
        onClick: () => call(m, "/detach", { method: "POST" }, t("Detached — reattach whenever you like.")) },
    ];
    more.push({ icon: TrashIcon, label: T("Remove origin"), danger: true, title: T("Remove origin for good; the workspace stays as an ordinary one"), onClick: () => forget(m) });
    return (
      <div key={m.workspace_id} className="aiProvRow">
        <span className={`aiProvAvatar mirrorAvatar ${st.tone} ${current ? "active" : ""}`} title={st.title}>
          <st.Icon size={15} />
        </span>
        <span className="aiProvMeta">
          <span className="aiProvName">
            {nameOf(m)}
            {current ? <span className="uiTag">{t("open")}</span> : null}
            {pullOnly ? <span className="uiTag" title={t("The remote's changes arrive here; yours stay here until you switch to two-way")}>{t("receive only")}</span> : null}
            {detached ? <span className="uiTag">{t("detached")}</span> : null}
            {!detached && s.last_error ? <span className="uiTag warn">{t("problem")}</span> : null}
            {!detached && !s.last_error && m.pending_local ? <span className="uiTag pending" title={t("Local edits the next round pushes")}>{t("unpushed edits")}</span> : null}
            {m.conflicts_open ? <span className="uiTag warn">{n(m.conflicts_open, "conflict")}</span> : null}
          </span>
          <span className="aiProvDesc" title={m.remote_url}>
            {t("clone of")} {m.remote_name} {t("· origin")} {hostOf(m.remote_url)}{" · "}
            <WorkspaceFolder id={m.workspace_id} />
          </span>
          <span className="aiProvDesc">{mirrorStatusLine(m)}</span>
        </span>
        <span className="aiProvActions">
          {!current ? <button className="uiBtn sm" onClick={() => { closeSettings?.(); switchWorkspace(m.workspace_id); }}>{t("Open")}</button> : null}
          {detached ? (
            <button className="uiBtn sm primary" disabled={busy} title={t("Follow origin again; what both sides did meanwhile merges")}
              onClick={() => call(m, "/relink", json({}), t("Reattached — syncing in the background."))}>
              <LinkIcon size={13} /> {t("Reattach")}
            </button>
          ) : (
            <button className="uiBtn sm" disabled={busy || s.running} onClick={() => syncNow(m)}
              title={pullOnly ? t("Receive the remote's changes now") : t("Sync now")}>
              <RefreshIcon size={13} /> {t("Sync")}
            </button>
          )}
          <button className={`uiBtn sm ${m.conflicts_open ? "primary" : ""}`} disabled={busy} onClick={() => setConflictsOf({ ...m, name: nameOf(m) })}
            title={t("Blocks both sides changed: the sync merged them or took one side; they wait here for you to resolve")}>
            <AlertCircleIcon size={13} /> {t("Conflicts")}{m.conflicts_open ? ` (${m.conflicts_open})` : ""}
          </button>
          <ActionMenu label={t("More")} icon={MoreIcon} iconOnly disabled={busy} items={more} />
        </span>
      </div>
    );
  }

  const clones = (mirrors || []).filter((m) => !isPublication(m));

  return (
    <>
      <Section
        title={t("Clones")}
        action={(
          <button className="uiBtn sm" disabled={busy} onClick={() => { setCreateError(""); setCreating(true); }}>
            <PlusIcon size={13} /> {t("Clone a remote workspace")}
          </button>
        )}
      >
        {mirrors === null ? <Empty icon={CloudDownloadIcon}>{t("Loading…")}</Empty>
          : clones.length ? clones.map(row)
          : <Empty icon={CloudDownloadIcon}>
              <span>{t("No clones yet.")}</span>
              <span className="settingDesc">{t("A clone follows a workspace on another Gamma server, pulling and pushing changes, and opens without a connection.")}</span>
            </Empty>}
      </Section>
      {creating ? (
        <MirrorDialog busy={busy} error={createError} onSubmit={submit} onClose={() => setCreating(false)}
          candidates={(workspaces || []).filter((w) => w.personal && !w.mirror_of && !(mirrors || []).some((m) => m.workspace_id === w.id))} />
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
        <span className={`aiProvAvatar mirrorAvatar ${st.tone} ${current ? "active" : ""}`} title={st.title}>
          <st.Icon size={15} />
        </span>
        <span className="aiProvMeta">
          <span className="aiProvName">
            {nameOf(m)}
            {current ? <span className="uiTag">{t("open")}</span> : null}
            {s.last_error ? <span className="uiTag warn">{t("problem")}</span> : null}
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
              <AlertCircleIcon size={13} /> {t("Conflicts ({n})", { n: m.conflicts_open })}
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
      {mirrors === null ? <Empty icon={CloudIcon}>{t("Loading…")}</Empty>
        : publications.length ? publications.map(publicationRow)
        : <Empty icon={CloudIcon}>
            <span>{t("No published pages yet.")}</span>
            <span className="settingDesc">{t("Publish a page from its Share button to give it a Gamma Cloud link; its edits sync both ways.")}</span>
          </Empty>}
    </Section>
  );
}
