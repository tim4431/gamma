// The files the iPad app is given by the system (the share sheet's Gamma,
// Files' Open in, AirDrop), as the page takes them (docs/dev/ipad.md
// "Opened files"). The shell holds each until the page is done with it:
// `files` lists what it holds, `fileData` reads one in base64 chunks,
// `fileDone` lets it go, and the shell's `filesOpened` says one came. A file
// waits in the shell until a page signed in to a workspace asks, so one
// opened before a server is chosen, at the sign-in page or across a reload
// is not lost. What a file becomes is openedKind's answer: a page, as a
// picked file is one, or an import's review.
import { nativeHost, onHostMessage, postToHost } from "../shared/lib/host.js";
import { t } from "../shared/i18n/i18n.js";

// The host holds opened files for the page (HostInfo.openFiles).
export const hostOpensFiles = () => Boolean(nativeHost()?.openFiles);

// Listen for the shell's word that a file came; returns the unsubscribe.
export const onFilesOpened = (fn) => onHostMessage("filesOpened", fn);

// What the shell holds, in the order it came: [{id, name, size, type}],
// [] when it holds nothing or cannot say.
export async function listOpenedFiles() {
  let reply;
  try { reply = await postToHost({ type: "files" }); } catch { return []; }
  const files = Array.isArray(reply?.files) ? reply.files : [];
  return files.filter((f) => f && typeof f.id === "string" && typeof f.name === "string" && Number.isFinite(f.size) && f.size >= 0)
    .map((f) => ({ id: f.id, name: f.name, size: f.size, type: typeof f.type === "string" ? f.type : "" }));
}

function fromBase64(data) {
  if (typeof Uint8Array.fromBase64 === "function") return Uint8Array.fromBase64(data);
  const bytes = atob(data);
  const out = new Uint8Array(bytes.length);
  for (let i = 0; i < bytes.length; i++) out[i] = bytes.charCodeAt(i);
  return out;
}

// One held file as a File, read chunk after chunk until its size is in or
// a chunk comes back empty. Throws when the shell no longer holds it.
export async function readOpenedFile(entry) {
  const parts = [];
  let offset = 0;
  while (offset < entry.size) {
    const reply = await postToHost({ type: "fileData", id: entry.id, offset });
    if (typeof reply?.data !== "string") throw new Error(t("the app no longer holds it"));
    if (!reply.data) break;
    const part = fromBase64(reply.data);
    parts.push(part);
    offset += part.length;
  }
  return new File(parts, entry.name, { type: entry.type || "" });
}

// The page is done with a held file (taken, refused or set aside): the shell
// drops its copy. Never throws: a shell gone or a file already let go is the
// same outcome.
export function doneWithOpenedFile(id) {
  return postToHost({ type: "fileDone", id }).then(() => undefined, () => undefined);
}

// What an opened file becomes: "page" (a PDF or a Markdown note, as the
// Add menu's upload makes one), "notability" (a .note or .ntb, to the
// Notability import's review), "archive" (a .zip, whose kind the import
// dialog asks), or "" (nothing Gamma opens).
export function openedKind(file) {
  const name = file?.name || "";
  const type = file?.type || "";
  if (/\.pdf$/i.test(name) || type === "application/pdf") return "page";
  if (/\.(?:md|markdown)$/i.test(name) || /^text\/(?:x-)?markdown$/i.test(type)) return "page";
  if (/\.(?:note|ntb)$/i.test(name)) return "notability";
  if (/\.zip$/i.test(name) || /^application\/(?:x-)?zip(?:-compressed)?$/i.test(type)) return "archive";
  return "";
}

// Who else sees what lands in workspace `w` (a /api/session entry), as the
// desktop's openFiles.js asks before it adds a file there: "clone" (it
// reaches the workspace the clone follows), "shared" (other members, or every
// account of a public workspace), "" (only you).
export function audienceOf(w) {
  if (!w) return "";
  if (w.mirror_of) return "clone";
  return w.access === "public" || w.members > 1 ? "shared" : "";
}

const WRITERS = new Set(["owner", "editor"]);

// Where opened files go: the workspace shown when the account can write
// there, else its personal one (desktop/lib/openFiles.js placeFor).
// {ws, audience, mine, switchTo}: `mine` is the personal workspace to offer
// instead when others see `ws`; `switchTo` the personal workspace to go to
// first, the one shown being read-only. null when there is nowhere to write.
export function placeOpenedFiles(shown, workspaces) {
  const list = workspaces || [];
  const writable = (w) => Boolean(w) && WRITERS.has(w.role);
  const mine = list.find((w) => w.personal && !w.mirror_of && writable(w)) || null;
  if (writable(shown)) {
    const audience = audienceOf(shown);
    return { ws: shown, audience, mine: audience && mine && mine.id !== shown.id ? mine : null, switchTo: null };
  }
  return mine ? { ws: mine, audience: "", mine: null, switchTo: mine } : null;
}
