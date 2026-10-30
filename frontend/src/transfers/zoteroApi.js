// The server's Zotero endpoints the link surfaces call (gamma/routers/zotero.py
// and routers/zotero_sync.py, docs/dev/zotero_sync.md): the account's
// connection Settings › Account & sync shows, the sign-in round trip, the
// libraries a key reaches, and a link's creation and the counts its
// dialog shows, under syncLinks.js's LINK_BASES.zotero. What a link does
// once made (sync, settings, conflicts, a page's PDF first) goes through
// syncLinks.js's paths, as a clone's does.
import { API, apiJson } from "../shared/lib/utils";
import { LINK_BASES, linkPath } from "../collaboration/syncLinks.js";

const post = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// A refusal that Settings › Account mends: no connection, or a key Zotero
// no longer takes (409).
export const needsZoteroConnect = (error) => error?.status === 409;

// {connected, username, user_id, hint, via, connected_at, access, can_edit,
//  oauth: {available, mode}}; {connected: false, can_edit, oauth} when none
// (Settings reads it through useSettingsRead).
export const ZOTERO_ACCOUNT = "/zotero/account";

export const connectZoteroKey = (apiKey) => apiJson(`${API}${ZOTERO_ACCOUNT}`, {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ api_key: apiKey }),
});

export const disconnectZotero = () => apiJson(`${API}${ZOTERO_ACCOUNT}`, { method: "DELETE" });

// [{id: "users/123" | "groups/9", name, kind: "user" | "group", user_id,
//   read, write, files, notes}]: `files`, the stored files may be
// downloaded (and uploaded where `write` holds too); `notes`, notes are
// seen at all.
export const getZoteroLibraries = () => apiJson(`${API}/zotero/libraries`).then((d) => d.libraries || []);

// Why Zotero no longer takes the stored key (revoked on zotero.org, or one
// this server can no longer unseal), "" while it does or cannot be asked
// just now: the account's own answer says "connected" from the stored pref
// alone, the libraries ask Zotero.
export const zoteroKeyRefusal = () => getZoteroLibraries()
  .then(() => "", (err) => (needsZoteroConnect(err) ? err.message : ""));

// {state, url}: the page opens `url` in a new tab and polls the state, which
// works in a browser and in the desktop and iPad shells alike (their
// navigation guards send other origins to the system browser).
export const startZoteroOAuth = () => apiJson(`${API}/zotero/oauth/start`, post({}));

// {state: "pending" | "connected" | "error", error?, account?}
export const zoteroOAuthStatus = (state) => apiJson(`${API}/zotero/oauth/status`, post({ state }));

// Link a library: {library, workspace_id ("" for a new workspace), name,
// mode, download, upload, auto_tags, send_unrecorded, adopt} → the link's
// info ({kind: "zotero", workspace_id, name, library, library_name, mode,
// download, upload, auto_tags, send_unrecorded, poll_s, on_change, status,
// conflicts_open, …}, as Settings' read of LINK_BASES.zotero lists them);
// its first round starts in the background.
export const createZoteroLink = (body) => apiJson(`${API}${LINK_BASES.zotero}`, post(body));

// What the dialog counts before linking (settings/zoteroPreview.js reads
// it): {items, notes, attachments, pdfs, pdf_bytes, linked_files, partial}
// of the library (`partial`: the PDF counts are a lower bound), and, into
// an existing workspace, {matches, to_send, to_send_bytes,
// collections_to_add, tags_to_add, notes_to_send, highlights_to_send (on
// papers whose PDF Zotero has), highlights_new (on papers whose PDF the
// link would upload: they go once it does)}, each null for a library past
// what the server reads. 429 while the account's
// last count runs (or too many were asked), 503 when Zotero kept it waiting.
export const previewZoteroLink = (library, workspaceId = "") => apiJson(
  `${API}${LINK_BASES.zotero}/preview?${new URLSearchParams({ library, workspace_id: workspaceId })}`);

// Bring a page's PDF from Zotero first (the link's queue reorders; a round
// is asked for): {queued: true}.
export const fetchZoteroPdf = (ws, pageId) => apiJson(`${API}${linkPath("zotero", ws, "/fetch")}`, post({ page_id: pageId }));
