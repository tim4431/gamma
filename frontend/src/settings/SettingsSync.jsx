// The sync half of Settings → Account & sync, under the account's own rows
// (SettingsUsers.jsx, selfOnly): what travels between this server and
// others — the pages published to Gamma Cloud (PublishingSection,
// docs/dev/mirror.md "Publishing"), the clones of workspaces on other Gamma
// servers (MirrorsSection) and where the header's sync pill shows.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { Section, Row, Segmented } from "./SettingsKit";
import { SECTION_PREFS } from "./sectionPrefs.js";
import { MirrorsSection, PublishingSection, useMirrors } from "./SettingsMirrors";
import { CloudIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";

export function SyncSettings({ value, papers }) {
  const { workspace, me, switchWorkspace, setStatus, confirm, closeSettings } = value;
  const signedIn = !!me && me !== "guest";
  const [mirrors, refreshMirrors] = useMirrors(signedIn);
  // The clones' names and the "Into" choices of a new clone come from the
  // workspace list, which a new clone also adds to.
  const [workspaces, setWorkspaces] = React.useState([]);
  const loadWorkspaces = React.useCallback(() => {
    if (signedIn) apiJson(`${API}/workspaces/mine`).then((d) => setWorkspaces(d.workspaces || [])).catch(() => {});
  }, [signedIn]);
  React.useEffect(() => { loadWorkspaces(); }, [loadWorkspaces]);
  const refresh = React.useCallback(() => { refreshMirrors(); loadWorkspaces(); }, [refreshMirrors, loadWorkspaces]);
  return (
    <>
      {signedIn ? (
        <>
          <PublishingSection mirrors={mirrors} refresh={refreshMirrors} currentId={workspace?.id}
            closeSettings={closeSettings} confirm={confirm} setStatus={setStatus} />
          <MirrorsSection mirrors={mirrors} refresh={refresh} workspaces={workspaces} currentId={workspace?.id}
            switchWorkspace={switchWorkspace} closeSettings={closeSettings} confirm={confirm} setStatus={setStatus} />
        </>
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
