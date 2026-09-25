// Settings → Sync: what travels between this server and others — the pages
// published to Gamma Cloud (PublishingSection, docs/dev/mirror.md
// "Publishing") and where the header's sync pill shows. Clones stay with
// the workspaces in Settings → Workspaces: each is a workspace of its own.
import React from "react";
import { PaneHead, Section, Row, Segmented } from "./SettingsKit";
import { SECTION_PREFS } from "./sectionPrefs.js";
import { PublishingSection, useMirrors } from "./SettingsMirrors";
import { CloudIcon, RefreshIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";

export function SyncSettings({ value, papers }) {
  const { workspace, me, setStatus, confirm, closeSettings } = value;
  const signedIn = !!me && me !== "guest";
  const [mirrors, refresh] = useMirrors(signedIn);
  return (
    <>
      <PaneHead icon={RefreshIcon} title={t("Sync")} />
      {signedIn ? (
        <PublishingSection mirrors={mirrors} refresh={refresh} currentId={workspace?.id}
          closeSettings={closeSettings} confirm={confirm} setStatus={setStatus} />
      ) : null}
      <Section title={t("Sync status")} scope="account" prefs={SECTION_PREFS.sync["Sync status"]}>
        <Row icon={CloudIcon} label={t("Sync pill")} hint={t("Where the header shows a page's sync with Gamma Cloud.")}
          title={t("Synced pages: the pill appears only on pages that sync with Gamma Cloud. Every page: it stays in the header on every page of a workspace that syncs some. A clone of another server shows it on every page either way.")}>
          <Segmented value={papers.syncPillScope} onChange={papers.setSyncPillScope}
            options={[["synced", t("Synced pages")], ["all", t("Every page")]]} />
        </Row>
      </Section>
    </>
  );
}
