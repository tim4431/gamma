// The sync half of Settings → Account & sync, under the account's own rows
// (SettingsUsers.jsx, selfOnly): the pages published to Gamma Cloud
// (PublishingSection, docs/dev/mirror.md "Publishing"). Clones of
// workspaces on other Gamma servers are listed in Settings → Workspaces, and
// where the header's sync pill shows is under Appearance. `value` is the
// workspace panes' value, which App.jsx gives only a signed-in account that
// is no guest (a guest publishes nothing, docs/dev/guests.md).
import React from "react";
import { PublishingSection, useMirrors } from "./SettingsMirrors";

export function SyncSettings({ value }) {
  const { workspace, setStatus, confirm, closeSettings } = value;
  const [mirrors, refreshMirrors] = useMirrors();
  return (
    <PublishingSection mirrors={mirrors} refresh={refreshMirrors} currentId={workspace?.id}
      closeSettings={closeSettings} confirm={confirm} setStatus={setStatus} />
  );
}
