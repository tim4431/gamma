// What the tracking dialog (explorer/TrackDialog.jsx) says of a work folder
// of another server (docs/dev/work_folders.md, docs/dev/synced_vaults.md "A
// vault on another server"): every file ticked is uploaded to that server
// and stored there, where every member of its workspace reads it, so the
// summary says so with how many files and bytes leave this computer, and
// names each ticked file that may hold secrets (gamma/documents/text.py is_secret).
// Both from the server's preview: `uploads` {files, bytes}, the large files
// already counted out, and `secrets`, the ticked ones' paths. Pure:
// tests/trackRemote.test.mjs.
import { tn } from "../shared/i18n/i18n.js";
import { fmtBytes } from "../shared/lib/format.js";

// The server a link's `remote_url` names, as people read it: its host.
export function serverOf(url) {
  try {
    return new URL(url).host;
  } catch {
    return String(url || "");
  }
}

// The line saying what the choice uploads to the link's server and that
// every member of its workspace there can read it, "" for a vault of this
// server or a preview that does not say.
export function uploadsText(link, preview) {
  const up = preview?.uploads;
  if (!link?.remote_url || !up) return "";
  return tn("{n} file, {size}, is uploaded to {server} and stored there, where every member of the workspace can read it",
    "{n} files, {size}, are uploaded to {server} and stored there, where every member of the workspace can read them",
    up.files || 0, { size: fmtBytes(up.bytes || 0), server: serverOf(link.remote_url) });
}

// The warning over the secrets files ticked, {text, files}, or null for
// none and for a vault of this server.
export function secretsWarning(link, preview) {
  const files = Array.isArray(preview?.secrets) ? preview.secrets.filter((p) => typeof p === "string" && p) : [];
  if (!link?.remote_url || !files.length) return null;
  return {
    text: tn("{n} ticked file may hold secrets, and every member of the workspace on {server} can read it:",
      "{n} ticked files may hold secrets, and every member of the workspace on {server} can read them:",
      files.length, { server: serverOf(link.remote_url) }),
    files,
  };
}

// Whether the dialog hands the window back to the desktop app's shell when
// it closes (the page ask `tracked`): a vault of another server, whose
// dialog the shell opened in its own server's window.
export const handsBack = (link, desktop) => !!(desktop && link?.remote_url);
