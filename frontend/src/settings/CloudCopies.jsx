// Settings → Backups › Cloud copies (admins, once a backup destination is
// set up): what the stores hold of the backup tasks' snapshots, and the
// ways back. Per store, how many copies of how many workspaces and their
// size; the workspaces only the cloud has (this server lacks them: a lost
// disk, or deleted here), each restorable as a new workspace, and all of
// them at once with Restore all… (one job, POST
// /api/jobs/bucket-restore-many, from each one's newest copy); Browse…, a
// dialog over every copy by workspace, filtered by store and found by name,
// each with Download, Restore and Delete. The copies are SettingsBackups.jsx's
// reads (each store's listing, BackupDestinations.jsx `useStores`), and its
// `actions` start the jobs; `CopyRow` is also the row a workspace's section
// there gives a copy only the cloud holds. docs/dev/settings.md.
import React from "react";
import { fmtBytes } from "../shared/lib/utils";
import { ActionMenu } from "../shared/ui/Menus";
import { DialogButtons, Empty, FormNotice, Row, Section, Segmented, SubDialog } from "./SettingsKit";
import { STORE, STORES, storeText } from "./backupStores.js";
import { CloudDownloadIcon, CloudIcon, CopyIcon, DownloadIcon, ImportIcon, PlusIcon, RefreshIcon, Trash2Icon } from "../shared/ui/Icons";
import { TaskBlock } from "../tasks/TasksTray";
import { isActive } from "../tasks/taskModel.js";
import { MorphText } from "../shared/ui/MotionWidgets";
import { T, t, tn } from "../shared/i18n/i18n.js";

const sizeOf = (copies) => copies.reduce((n, c) => n + (c.size_bytes || 0), 0);
const nameOf = (g) => g.name || t("Unnamed workspace");

// One copy in a store. `here`: the server has its workspace, which it
// restores into (or beside, as a new one); else it comes back as a new
// workspace. `showStore` names the store (the browser, where every store is
// listed); a workspace's section says "only in …" instead.
export function CopyRow({ ws, label, copy: c, here, busy, when, actions, showStore = false }) {
  return (
    <div className="aiProvRow">
      <span className="aiProvAvatar"><CloudIcon size={16} /></span>
      <span className="aiProvMeta">
        <span className="aiProvName">
          {when(c)}
          <span className="uiTag">{c.task ? t("Automatic") : t("backup")}</span>
          {showStore ? <span className="uiTag">{storeText.held(c.store)}</span> : null}
        </span>
        <span className="aiProvDesc">{showStore ? fmtBytes(c.size_bytes) : [fmtBytes(c.size_bytes), storeText.only(c.store)].join(" · ")}</span>
      </span>
      <span className="aiProvActions">
        <button className="uiBtn sm iconSq" title={t("Download as a zip")} aria-label={t("Download")} onClick={() => actions.fetch(ws, c)}>
          <DownloadIcon size={16} />
        </button>
        {here ? (
          <ActionMenu
            label={t("Restore")} icon={ImportIcon} disabled={busy}
            items={[
              { icon: ImportIcon, label: T("Replace…"), title: T("Put the workspace back exactly as it was in this snapshot"), danger: true, onClick: () => actions.restore(ws, label, c, "replace") },
              { icon: PlusIcon, label: T("Merge…"), title: T("Add what the snapshot has and the workspace lacks"), onClick: () => actions.restore(ws, label, c, "merge") },
              { icon: CopyIcon, label: T("As a new workspace…"), title: T("Make a workspace of this copy beside the one here, with you as its owner"), onClick: () => actions.restore(ws, label, c, "new") },
            ]}
          />
        ) : (
          <button className="uiBtn sm" disabled={busy} title={t("Make a workspace of this copy, with you as its owner")}
            onClick={() => actions.restore(ws, label, c, "new")}><ImportIcon size={14} /> {t("Restore as new…")}</button>
        )}
        <button className="uiBtn sm iconSq danger" title={storeText.deleteButton(c.store)} aria-label={storeText.deleteLabel(c.store, when(c))}
          onClick={() => actions.remove(ws, label, c)}>
          <Trash2Icon size={16} />
        </button>
      </span>
    </div>
  );
}

// Every copy, by workspace: one store or all, a workspace found by name,
// one workspace alone when the section's Copies… opened it.
function CopiesBrowser({ stores, groups, mine, initial, when, actions, runningFor, onReload, onClose }) {
  const [store, setStore] = React.useState(initial.store || "all");
  const [query, setQuery] = React.useState("");
  const [only, setOnly] = React.useState(initial.workspace || "");
  const owned = new Set(mine.map((w) => w.id));
  const needle = query.trim().toLowerCase();
  const shown = groups
    .map((g) => ({ ...g, snapshots: g.snapshots.filter((c) => store === "all" || c.store === store) }))
    .filter((g) => g.snapshots.length && (!only || g.workspace === only) && (!needle || nameOf(g).toLowerCase().includes(needle)));
  const copies = shown.flatMap((g) => g.snapshots);
  const where = (g) => (!g.here ? t("not on this server") : owned.has(g.workspace) ? t("on this server") : t("another account's"));
  return (
    <SubDialog title={t("Cloud copies")} onClose={onClose} className="cloudCopies">
      <div className="cloudCopiesBar">
        {stores.length > 1 ? (
          <Segmented value={store} onChange={setStore}
            options={[["all", t("All")], ...stores.map((s) => [s, STORE[s].title()])]} />
        ) : null}
        <input className="aiKeyInput" type="search" aria-label={t("Find a workspace")} placeholder={t("Find a workspace")}
          value={query} onChange={(event) => setQuery(event.target.value)} />
        <button className="uiBtn sm iconSq" title={t("Read the stores again")} aria-label={t("Refresh")} onClick={onReload}>
          <RefreshIcon size={16} />
        </button>
      </div>
      {only ? (
        <FormNotice tone="info">
          {t("The copies of one workspace.")}{" "}
          <button type="button" className="uiBtn sm" onClick={() => setOnly("")}>{t("Show every workspace")}</button>
        </FormNotice>
      ) : null}
      <div className="cloudCopiesList">
        {shown.length ? shown.map((g) => (
          <div key={g.workspace} className="cloudCopiesGroup" data-workspace={g.workspace}>
            <div className="cloudCopiesHead">
              <span className="settingLabel">{nameOf(g)}</span>
              <span className="uiTag">{where(g)}</span>
            </div>
            {g.snapshots.map((c) => (
              <CopyRow key={`${c.store}-${c.name}`} ws={g.workspace} label={nameOf(g)} copy={c} here={g.here}
                busy={Boolean(runningFor(g.workspace))} when={when} actions={actions} showStore />
            ))}
          </div>
        )) : <Empty icon={CloudIcon}>{needle ? t("No workspace by that name has a copy.") : t("No copies yet.")}</Empty>}
      </div>
      <p className="settingsPaneHint">
        {tn("{n} copy", "{n} copies", copies.length)} · {tn("{n} workspace", "{n} workspaces", shown.length)} · {fmtBytes(sizeOf(copies))}
      </p>
      <DialogButtons>
        <button type="button" className="uiBtn" onClick={onClose}>{t("Close")}</button>
      </DialogButtons>
    </SubDialog>
  );
}

// The workspaces only the cloud has, each from its newest copy, picked
// before they come back.
function RestoreAllDialog({ missing, when, onStart, onClose }) {
  const [picked, setPicked] = React.useState(() => new Set(missing.map((g) => g.workspace)));
  const [busy, setBusy] = React.useState(false);
  const chosen = missing.filter((g) => picked.has(g.workspace));
  const toggle = (id) => setPicked((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  async function start() {
    setBusy(true);
    if (!(await onStart(chosen))) setBusy(false);
  }
  return (
    <SubDialog title={t("Restore workspaces from the cloud")} onClose={onClose} className="cloudCopies">
      <p className="settingsPaneHint">
        {t("Each comes back from its newest copy as a new workspace you own, one after another in Background tasks. Invite their members again afterwards.")}
      </p>
      <div className="cloudCopiesList">
        {missing.map((g) => {
          const c = g.snapshots[0];
          return (
            <label key={g.workspace} className="aiProvRow cloudCopyPick">
              <input type="checkbox" checked={picked.has(g.workspace)} onChange={() => toggle(g.workspace)}
                aria-label={nameOf(g)} />
              <span className="aiProvMeta">
                <span className="aiProvName">{nameOf(g)}</span>
                <span className="aiProvDesc">{[when(c), fmtBytes(c.size_bytes), storeText.held(c.store)].join(" · ")}</span>
              </span>
            </label>
          );
        })}
      </div>
      <DialogButtons>
        <button type="button" className="uiBtn" onClick={onClose}>{t("Cancel")}</button>
        <button type="button" className="uiBtn primary" disabled={busy || !chosen.length} onClick={start}>
          <MorphText>{tn("Restore {n} workspace", "Restore {n} workspaces", chosen.length)}</MorphText>
        </button>
      </DialogButtons>
    </SubDialog>
  );
}

// `at`: each store's place ("" while it is not set up); `held`: each
// store's listing read; `groups`: the copies of all, by workspace, newest
// first (mergeCopies); `actions`: {fetch, restore, remove, restoreMany}.
export function CloudCopies({ at, held, groups, mine, when, actions, runningFor, tasks, onReload }) {
  const [browsing, setBrowsing] = React.useState(null); // null | {store, workspace}
  const [choosing, setChoosing] = React.useState(false);
  const stores = STORES.filter((store) => at[store]);
  if (!stores.length) return null;
  const missing = groups.filter((g) => !g.here);
  const restoring = tasks.jobs.find((job) => job.kind === "copies-restore" && isActive(job)) || null;
  function summary(store) {
    const read = held[store];
    if (read.error) return storeText.unreadable(store, read.error);
    if (!read.data) return t("Reading {where}…", { where: at[store] });
    const copies = (read.data.groups || []).flatMap((g) => g.snapshots);
    if (!copies.length) return t("{where} · no copies yet", { where: at[store] });
    return [at[store], tn("{n} copy", "{n} copies", copies.length),
      tn("{n} workspace", "{n} workspaces", read.data.groups.length), fmtBytes(sizeOf(copies))].join(" · ");
  }
  return <>
    <Section title={t("Cloud copies")} tag={<span className="uiTag admin">{t("admin")}</span>}
      action={<button className="uiBtn sm" onClick={() => setBrowsing({})}>{t("Browse…")}</button>}>
      {stores.map((store) => (
        <Row key={store} icon={CloudIcon} label={STORE[store].title()} hint={summary(store)}>
          {held[store].error
            ? <button className="uiBtn sm" onClick={onReload}>{t("Try again")}</button>
            : <button className="uiBtn sm" onClick={() => setBrowsing({ store })}>{t("Browse…")}</button>}
        </Row>
      ))}
      {restoring ? <TaskBlock task={restoring} tasks={tasks} /> : null}
      {missing.length ? <>
        <Row icon={CloudDownloadIcon} label={tn("{n} workspace is only in the cloud", "{n} workspaces are only in the cloud", missing.length)}
          hint={t("This server doesn't have them: a lost disk, or deleted here")}>
          <button className="uiBtn sm primary" disabled={Boolean(restoring)} onClick={() => setChoosing(true)}>{t("Restore all…")}</button>
        </Row>
        <div className="setSubRows">
          {missing.map((g) => {
            const c = g.snapshots[0];
            const job = runningFor(g.workspace);
            return (
              <Row key={g.workspace} icon={CloudIcon} label={nameOf(g)}
                hint={t("Newest copy {date} · {size} · {where}", { date: when(c), size: fmtBytes(c.size_bytes), where: storeText.held(c.store) })}>
                <span className="setRowControls">
                  {g.snapshots.length > 1 ? (
                    <button className="uiBtn sm" onClick={() => setBrowsing({ workspace: g.workspace })}>
                      {tn("{n} copy…", "{n} copies…", g.snapshots.length)}
                    </button>
                  ) : null}
                  <button className="uiBtn sm" disabled={Boolean(job || restoring)} title={t("Make a workspace of its newest copy, with you as its owner")}
                    onClick={() => actions.restore(g.workspace, nameOf(g), c, "new")}>{t("Restore…")}</button>
                </span>
              </Row>
            );
          })}
        </div>
      </> : null}
    </Section>
    {browsing ? (
      <CopiesBrowser stores={stores} groups={groups} mine={mine} initial={browsing} when={when} actions={actions}
        runningFor={runningFor} onReload={onReload} onClose={() => setBrowsing(null)} />
    ) : null}
    {choosing ? (
      <RestoreAllDialog missing={missing} when={when} onClose={() => setChoosing(false)}
        onStart={async (chosen) => {
          const ok = await actions.restoreMany(chosen.map((g) => ({
            ws: g.workspace, name: g.snapshots[0].name, store: g.snapshots[0].store, label: nameOf(g),
          })));
          if (ok) setChoosing(false);
          return ok;
        }} />
    ) : null}
  </>;
}
