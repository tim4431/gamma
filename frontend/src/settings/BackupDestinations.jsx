// Settings → Backups › Backup destinations (admins only): every place off
// this server that backup tasks can copy their snapshots to, one row each
// in one list (backupStores.js STORES): Google Drive and OneDrive
// (BackupCloud.jsx, signed in to) and an S3-compatible bucket
// (BackupBucket.jsx). Each row says whether it is set up and where, with
// its own Connect… / Set up…, Test and Edit…; a task then picks among
// those set up (BackupTasks.jsx, "Copy off this server"), and Cloud copies
// lists what they hold (CloudCopies.jsx). `useStores` is the pane's view of
// them all: where each is and what each holds. docs/dev/settings.md.
import React from "react";
import { Section } from "./SettingsKit";
import { CloudRow } from "./BackupCloud";
import { BucketRow } from "./BackupBucket";
import { STORE, STORES, copiesOf, mergeCopies } from "./backupStores.js";
import { useSettingsReads } from "./settingsReads.js";
import { t } from "../shared/i18n/i18n.js";

const NONE = { data: null, error: "" };

// Every store as the pane needs it (admins; for anyone else nothing is
// read and every store is ""): `at`, where each puts its copies ("" while
// it is not set up, backupStores.js `where`); `held`, each one's listing
// read ({data: {groups}, error}); `groups`, the copies of all, by
// workspace (mergeCopies); `reload`, the listings read again (after a run,
// a restore, a delete). The rows read the same paths, so a store set up
// there is listed here at once.
export function useStores(isAdmin) {
  const reads = useSettingsReads();
  React.useEffect(() => {
    if (isAdmin) for (const store of STORES) reads.read(STORE[store].read).catch(() => {});
  }, [reads, isAdmin]);
  const at = Object.fromEntries(STORES.map((store) => [store, isAdmin ? STORE[store].where(reads.get(STORE[store].read).data) : ""]));
  const places = STORES.map((store) => at[store]).join("\n");
  React.useEffect(() => {
    for (const store of STORES) if (at[store]) reads.read(copiesOf(store)).catch(() => {});
  }, [reads, places]); // eslint-disable-line react-hooks/exhaustive-deps
  const held = Object.fromEntries(STORES.map((store) => {
    const entry = at[store] ? reads.get(copiesOf(store)) : NONE;
    return [store, { data: entry.data ?? null, error: entry.error }];
  }));
  const lists = STORES.map((store) => (at[store] ? held[store].data?.groups : null));
  const groups = React.useMemo(() => mergeCopies(Object.fromEntries(STORES.map((store, i) => [store, lists[i]]))),
    lists); // eslint-disable-line react-hooks/exhaustive-deps
  const reload = React.useCallback(() => {
    for (const store of STORES) if (at[store]) reads.read(copiesOf(store), { force: true }).catch(() => {});
  }, [reads, places]); // eslint-disable-line react-hooks/exhaustive-deps
  return { at, held, groups, reload };
}

const ROWS = { drive: CloudRow, onedrive: CloudRow, bucket: BucketRow };

export function BackupDestinations({ setStatus, confirm }) {
  return (
    <Section title={t("Backup destinations")} tag={<span className="uiTag admin">{t("admin")}</span>}>
      <p className="settingsPaneHint">
        {t("Places off this server that backup tasks can copy their snapshots to. Set up one or more, then choose them in a task.")}
      </p>
      {STORES.map((store) => {
        const StoreRow = ROWS[store];
        return <StoreRow key={store} store={store} setStatus={setStatus} confirm={confirm} />;
      })}
    </Section>
  );
}
