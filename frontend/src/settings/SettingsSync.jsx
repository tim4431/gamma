// The sync half of Settings → Account & sync, under the account's own rows
// (SettingsUsers.jsx, selfOnly): the pages published to Gamma Cloud
// (PublishingSection, docs/dev/mirror.md "Publishing"). Clones of
// workspaces on other Gamma servers are listed in Settings → Workspaces, and
// where the header's sync pill shows is under Appearance.
import React from "react";
import { PublishingSection, useMirrors } from "./SettingsMirrors";

export function SyncSettings({ value }) {
  const { workspace, me, setStatus, confirm, closeSettings } = value;
  const signedIn = !!me && me !== "guest";
  const [mirrors, refreshMirrors] = useMirrors(signedIn);
  if (!signedIn) return null;
  return (
    <PublishingSection mirrors={mirrors} refresh={refreshMirrors} currentId={workspace?.id}
      closeSettings={closeSettings} confirm={confirm} setStatus={setStatus} />
  );
}
