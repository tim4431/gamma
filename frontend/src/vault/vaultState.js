// How the open page stands with this computer, read from the desktop app's
// own server (GET /api/folder-links/page/{id}, routers/folder_links.py
// `page_state`; docs/research/vaults.md "The words" and "Reading a PDF where
// it is"): `{vault, reader}`. `vault` is the synced vault that keeps the page
// (`{id, name, scope, path, pull, push, problem, conflicts, paused}`), `reader`
// the PDF the page reads where it lies (`{path, missing, mine,
// relocation_error?}`, `mine` whether this account opened the file).
// What the page shows of it: the chip in the page header and its card
// (VaultChip.jsx), the banner over the page (its VaultBanner), whether the
// notes are locked, and the conflict route the merge chips read. A path is
// "" for a member who cannot act on the vault: where its files lie is the
// reading of whoever made it. Pure: tests/vaultState.test.mjs.
import { t, tn } from "../shared/i18n/i18n.js";
import { inPlace } from "../documents/pageDocument.js";

// The answer, kept only when it says something: null for a page of no vault
// that reads no file here.
export function readVault(answer) {
  const vault = answer?.vault && typeof answer.vault === "object" ? answer.vault : null;
  const reader = answer?.reader && typeof answer.reader === "object" ? answer.reader : null;
  return vault || reader ? { vault, reader } : null;
}

// The last name of a path, with either separator.
export function baseName(path) {
  return String(path || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "";
}

// A vault's direction in its two switches' words (`push` is the API's
// "off" | "pdfs" | "everything").
export function directionText(vault) {
  const push = vault?.push === "everything" ? t("Push: everything") : vault?.push === "pdfs" ? t("Push: PDFs") : t("Push: off");
  return `${vault?.pull ? t("Pull: on") : t("Pull: off")} · ${push}`;
}

// The chip in the page header: `{kind, label, title}`, or null for a page
// that has nothing to do with this computer.
// `kind` is what the header draws, one icon for how the page stands:
// "sync" a folder vault keeps it in step, "push" its PDF comes from a file
// of its own, "disk" it is a PDF read where it lies, "paused" its vault is
// paused, "alert" the vault could not write or the file is gone. The label
// says the same in words, for the tooltip, the screen reader and the card;
// a folder vault's page says where it is synced, a vault of one file where
// its PDF is pushed from (a file vault only pushes), an opened PDF that it
// is on this computer.
export function vaultChip(state) {
  if (!state) return null;
  const { vault, reader } = state;
  if (vault) {
    const name = baseName(vault.path) || vault.name || "";
    const file = vault.scope === "file";
    const label = file ? t("Pushed from: {name}", { name }) : t("Synced: {name}", { name });
    const where = vault.path || vault.name || "";
    const lines = [file ? t("This page's PDF is pushed from {path}", { path: where }) : t("This page is synced with {path}", { path: where })];
    if (vault.paused) lines.push(t("The vault is paused."));
    if (vault.conflicts) lines.push(tn("{n} conflict", "{n} conflicts", vault.conflicts));
    if (vault.problem) lines.push(t("Not written to disk: {reason}", { reason: vault.problem }));
    const kind = vault.problem ? "alert" : vault.paused ? "paused" : file ? "push" : "sync";
    return { kind, label, title: lines.join("\n") };
  }
  const missing = !!reader.missing;
  return {
    kind: missing ? "alert" : "disk",
    label: missing ? t("File missing") : t("On this computer"),
    title: missing ? t("The file this PDF was read from is gone")
      : reader.path ? t("This PDF is read from {path}, not stored in the library", { path: reader.path })
      : t("This PDF is read from a file on this computer, not stored in the library"),
  };
}

// The banner over the page, in the missing-page notice's slot: a note the
// vault could not write (`problem`, why), or an opened PDF whose file is
// gone (`missing`, with Locate… and Remove). Null for neither.
export function vaultBanner(state) {
  if (state?.vault?.problem) return { kind: "problem", reason: String(state.vault.problem) };
  if (state?.reader?.missing) return { kind: "missing", path: state.reader.path || "" };
  return null;
}

// What a dismissed banner is remembered by: the page and what the banner
// says, so it shows again once it says something else ("" for none).
export function bannerKey(pageId, banner) {
  return banner ? `${pageId}:${banner.kind}:${banner.reason || banner.path}` : "";
}

// Whether the page's notes are locked: a folder vault that does not pull
// writes nothing of Gamma's to its files, so where it pushes everything a
// note edited here would part from the file, which is where the notes are
// written. Highlights and chat stay open (BlockTree's `notesLocked`). One
// that pushes only PDFs never reads a notes file into Gamma, and a vault of
// one file keeps only its PDF on disk: their notes are Gamma's.
export function notesLocked(state) {
  const vault = state?.vault;
  return !!vault && vault.scope !== "file" && !vault.pull && vault.push === "everything";
}

// Whether the page's notes file can be edited from here (FileSourceDialog):
// the vault pushes everything, so the file is what the page becomes, and the
// page has a notes file whose path this account may see.
export function editsFile(state) {
  const vault = state?.vault;
  return !!vault && vault.push === "everything" && /\.(md|markdown)$/i.test(vault.path || "");
}

// Whether Add to library… takes the page in as an opened PDF (a work
// folder's file is fileRules' addToLibrary, explorer/workFolders.js).
export function addsToLibrary(state) {
  return !!state?.reader && !state.vault;
}

// Whether this account opened the page's file, as far as the page knows:
// false only when the server says another account did. Keeping on pushing
// from the file and taking the page to another workspace or server are the
// opener's alone (the file is on that account's computer); the server and the
// desktop app refuse them to anyone else too.
export function openedHere(state) {
  return state?.reader?.mine !== false;
}

// Whether the page's PDF can be carried to another server's library (the
// desktop app's `carry-to-server`): an opened PDF whose file is there to read,
// opened by this account.
export function carriesToServer(state) {
  return addsToLibrary(state) && !state.reader.missing && openedHere(state);
}

// Where the page's vault keeps its conflicts (the merge chips' read and
// resolve), or "" for a page with no vault that has them: under
// /api/folder-links/{id}.
export function vaultConflictsPath(state) {
  const vault = state?.vault;
  return vault?.id && vault.scope !== "file" ? `/folder-links/${encodeURIComponent(vault.id)}` : "";
}

// Whether a library row (App's pageBlocks: `_doc`, `_parentPage`, `_opened`)
// is a PDF opened from this computer, one whose document is read in place,
// inside *Opened files*: the page Move to library and Move into page… take
// to Add to library… instead, since its bytes must be stored before it
// leaves.
export function isOpenedPdf(id, pagesById) {
  const row = pagesById?.get(id);
  return inPlace(row?._doc) && !!row._parentPage && !!pagesById.get(row._parentPage)?._opened;
}

// The workspaces Add to library… offers (`workspaces`: the session's list,
// as the workspace menu reads it): the one open here, then each other this
// account edits on this server, a clone left out (it keeps its origin's
// pages; linked_files.add_to_library refuses it too). `[{id, name}]`.
export function libraryWorkspaces(workspaces, current) {
  const list = workspaces || [];
  const writes = (w) => (w.role === "owner" || w.role === "editor") && !w.mirror_of;
  const here = list.find((w) => w.id === current);
  return [...(here ? [here] : []), ...list.filter((w) => w.id !== current && writes(w))]
    .map((w) => ({ id: w.id, name: w.name || w.id }));
}
