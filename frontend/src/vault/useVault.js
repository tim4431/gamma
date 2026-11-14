// The open page's standing with this computer (vaultState.js), read in the
// desktop app from its own server (GET /api/folder-links/page/{id}) as the
// page opens and again on its socket's `{t: "vault"}`, which a vault's round
// sends after it touched the page, and on each (re)join of the socket, which
// may have missed one (App's usePageCollab `onVault`). Any other
// server answers that route 404: it is not asked again in this window.
// The owner of App's part of the feature (docs/dev/frontend-refactor.md
// "New state"): with the standing it holds the page's banner, dismissed or
// not, which of the feature's dialogs is open, and in the desktop app the
// workspace's folder vaults of this server by folder, which the work
// folders' Choose files… opens the tracking dialog for
// (explorer/TrackDialog.jsx, docs/dev/work_folders.md "The tracking dialog").
import { useCallback, useEffect, useRef, useState } from "react";
import { API, apiJson, getCurrentWorkspace } from "../shared/lib/utils";
import { IS_DESKTOP } from "../shared/lib/connector.js";
import { t } from "../shared/i18n/i18n.js";
import { keptFolders } from "../explorer/workFolderActions.js";
import { bannerKey, readVault, vaultBanner } from "./vaultState.js";

let elsewhere = false;

// `{state, refresh, banner, dismissBanner, adding, setAdding, addingFile,
// setAddingFile, editing, setEditing, links, kept, refreshLinks, tracking, track,
// closeTracking, watchRound}`:
//   state     null for a page of no vault reading no file here, and wherever
//             the route does not answer (outside the desktop app, a share's
//             visitor: `enabled` false). The answer is kept with the page it
//             is of: until the next page's comes, that page has none, so it
//             never shows the last page's chip, banner or notes lock; the same
//             page asked again (a `vault` message) keeps its own meanwhile.
//   refresh   asks again for the page open when it runs, so a handler that
//             awaited something first asks for the page open by then (stable)
//   banner    the page's banner (vaultState.vaultBanner) until it is
//             dismissed; once it says something else it shows again
//   adding    the page ids Add to library… (FolderPickerDialog) is open for,
//             else null
//   editing   the page whose notes file is edited whole (FileSourceDialog),
//             else ""
//   addingFile  {ids, copy} while Add to library… (or, with `copy`, Save a
//             copy to library…) is open for work folders' files, else null
//   links     in the desktop app, this workspace's folder vaults of this
//             server that the account made (`mine`: the tracking dialog's
//             routes are the maker's) by their folder's id (GET
//             /api/folder-links, read once and by `refreshLinks()`);
//             elsewhere empty
//   kept      the folders a vault of this server keeps, whoever made it
//             (every local folder link the account sees, which for a
//             workspace's owner is all of its workspace's), read with
//             `links`: null in the desktop app until they are known, empty
//             on a server that keeps no vault (404) and outside the desktop
//             app (that server's own answer refuses a folder it keeps)
//   tracking  {link, fresh, tick} while the tracking dialog is open for the
//             vault `link` (as GET /api/folder-links/{id} answers it), `fresh`
//             when the vault is still choosing its first files, `tick` the
//             path in its directory of a file to tick ("" for none), else
//             null: `track(linkId, tick)` opens it, as the boot parameters
//             `track` and `tick` (`options.track` and `options.tick`, the
//             shell's hand-over, `tick` when a file of the directory was
//             opened with Gamma) do once the workspace is known, which also
//             leaves the URL; a link that cannot be read says so
//             (`options.setStatus`)
export function useVault(pageId, enabled, { track = "", tick = "", setStatus = null } = {}) {
  const onStatus = useRef(setStatus);
  onStatus.current = setStatus;
  const [held, setHeld] = useState({ pageId: "", state: null });
  const [dismissed, setDismissed] = useState("");
  const [adding, setAdding] = useState(null);
  const [addingFile, setAddingFile] = useState(null);
  const [editing, setEditing] = useState("");
  const [links, setLinks] = useState(() => new Map());
  const [tracking, setTracking] = useState(null);
  const asked = useRef(0);
  const open = useRef({ pageId, enabled });
  open.current = { pageId, enabled };
  const refresh = useCallback(async () => {
    const { pageId, enabled } = open.current;
    const n = ++asked.current;
    if (!IS_DESKTOP || elsewhere || !enabled || !pageId) { setHeld({ pageId, state: null }); return; }
    try {
      const answer = await apiJson(`${API}/folder-links/page/${encodeURIComponent(pageId)}`);
      if (n === asked.current) setHeld({ pageId, state: readVault(answer) });
    } catch (err) {
      if (err?.status === 404) elsewhere = true;
      if (n === asked.current) setHeld({ pageId, state: null });
    }
  }, []);
  useEffect(() => { refresh(); }, [pageId, enabled, refresh]);
  const [kept, setKept] = useState(() => (IS_DESKTOP ? null : new Set()));
  const refreshLinks = useCallback(async () => {
    if (!IS_DESKTOP || !open.current.enabled) return;
    const ws = getCurrentWorkspace();
    try {
      const { links: all = [] } = await apiJson(`${API}/folder-links`);
      setLinks(new Map(all.filter((l) => l.kind === "folder" && !l.remote_url && l.workspace_id === ws && l.mine)
        .map((l) => [l.folder_id, l])));
      setKept(keptFolders(all));
    } catch (err) {
      if (err?.status === 404) setKept(new Set());
    }
  }, []);
  useEffect(() => { refreshLinks(); }, [enabled, refreshLinks]);
  const trackLink = useCallback(async (linkId, file = "") => {
    try {
      const link = await apiJson(`${API}/folder-links/${encodeURIComponent(linkId)}`);
      setTracking({ link, fresh: !!link.choosing, tick: file || "" });
    } catch (err) {
      onStatus.current?.(t("Could not open Choose files…: {message}", { message: err.message }));
    }
  }, []);
  // Once the round of the vault `linkId` started (a Save of its choice) is
  // over — its status no longer running, asked every 1.5 s for up to two
  // minutes — `then()` runs: the folder's list is read again then.
  const watchRound = useCallback(async (linkId, then) => {
    for (let i = 0; i < 80; i++) {
      await new Promise((resolve) => { setTimeout(resolve, 1500); });
      try {
        const link = await apiJson(`${API}/folder-links/${encodeURIComponent(linkId)}`);
        if (!link.status?.running) break;
      } catch { break; }
    }
    then();
  }, []);
  const booted = useRef(false);
  useEffect(() => {
    if (!track || !enabled || booted.current) return;
    booted.current = true;
    const url = new URL(window.location.href);
    url.searchParams.delete("track");
    url.searchParams.delete("tick");
    window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
    trackLink(track, tick);
  }, [track, tick, enabled, trackLink]);
  const state = held.pageId === pageId ? held.state : null;
  const banner = vaultBanner(state);
  const key = bannerKey(pageId, banner);
  const dismissBanner = useCallback(() => setDismissed(key), [key]);
  const closeTracking = useCallback(() => setTracking(null), []);
  return {
    state, refresh, banner: key !== dismissed ? banner : null, dismissBanner,
    adding, setAdding, addingFile, setAddingFile, editing, setEditing,
    links, kept, refreshLinks, tracking, track: trackLink, closeTracking, watchRound,
  };
}

// A message to the desktop app's shell, which its page preload passes on
// (desktop/preload.js): `keep-folder-on-disk {folder}` (sync a folder with a
// directory), `follow-file-on-disk {page}` (push from a file), `reveal-on-disk
// {page}`, `relocate-file {page}`, `carry-to-server {page}` (an opened PDF to
// another server's library), each for the workspace open here. The first two
// keep their names from before synced vaults, the only ones a desktop app
// released before them passes on: it may show a page of a server updated
// since. The shell answers the page nothing: what it does reaches the page
// through the server.
export function askShell(type, fields) {
  window.postMessage({ source: "gamma-app", type, ws: getCurrentWorkspace(), ...fields }, window.location.origin);
}
