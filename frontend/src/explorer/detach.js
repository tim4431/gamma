// Stop syncing, the one way a synced folder leaves Gamma (docs/dev/
// work_folders.md "Detaching a work folder"): the words of its menu row,
// its dialog (DetachDialog.jsx) and the status after it, from what GET
// /api/folders/{id}/detach answers (`plan`: {may, files, in_library,
// too_large, bytes, computer: {name, online} | null}, or {may: false, why})
// and what POST answers ({ok, kept, trashed}). The interface says "synced
// folder" and "Stop syncing"; the code and the API say work folder and
// detach (a clone's or a Zotero link's Detach is a pause, this is not).
// Pure: tests/detach.test.mjs.
import { fmtBytes } from "../shared/lib/format.js";
import { t, tn } from "../shared/i18n/i18n.js";

// The computer that keeps the folder, as the plan names it ("" for none
// named: none keeps it, or it is another account's).
const computerOf = (plan) => (typeof plan?.computer?.name === "string" ? plan.computer.name : "");

// The menu row: "Stop syncing with TIM-PC…", or "Stop syncing…" with no
// computer named (and while the plan is being read).
export const detachLabel = (plan) => (computerOf(plan)
  ? t("Stop syncing with {computer}…", { computer: computerOf(plan) }) : t("Stop syncing…"));

// Why the account may not stop syncing it (a plan of {may: false}), the
// row's disabled title, in the rule's words (gamma/work_folders/detach.py
// plan's `why`): a workspace kept in step with another carries copies of
// its marks; a vault of this server lets its maker alone; else the
// workspace's owner or the account whose computer keeps it.
export function detachRefusal(plan) {
  if (plan?.why === "elsewhere") {
    return t("This workspace is kept in step with another: stop syncing the folder in the workspace it comes from.");
  }
  if (plan?.why === "maker") return t("Only the person whose computer keeps it can stop syncing it.");
  return t("Only the person whose computer keeps it, or the workspace's owner, can stop syncing it.");
}

// The dialog's words for the folder `name`: {title, counts, keep, remove,
// disk, offline}, `counts` what it does and its files and those in the
// library, `keep` and `remove` what each choice does, `disk` that the
// directory stays as it is, `offline` ("" while online) that a computer
// away forgets it later.
export function detachText(plan, name) {
  const computer = computerOf(plan);
  const files = plan?.files || 0;
  const inLibrary = plan?.in_library || 0;
  const tooLarge = plan?.too_large || 0;
  const leaving = Math.max(files - inLibrary, 0);
  return {
    title: computer ? t("Stop syncing “{name}” with {computer}?", { name, computer }) : t("Stop syncing “{name}”?", { name }),
    counts: [
      computer ? t("Gamma stops syncing it with {computer}.", { computer }) : t("Gamma stops syncing it."),
      files ? tn("It has {n} file.", "It has {n} files.", files) : t("It has no files yet."),
      inLibrary ? tn("{n} of them is in your library and stays there either way.",
        "{n} of them are in your library and stay there either way.", inLibrary) : "",
    ].filter(Boolean).join(" "),
    keep: [
      t("Each file becomes an ordinary page, filed where it is."),
      plan?.bytes ? t("A copy of each file is stored in Gamma ({size}).", { size: fmtBytes(plan.bytes) }) : "",
      tooLarge ? tn("{n} file is too large to store: it stays listed by name only.",
        "{n} files are too large to store: they stay listed by name only.", tooLarge) : "",
    ].filter(Boolean).join(" "),
    remove: leaving ? tn("{n} file goes to Recently deleted.", "{n} files go to Recently deleted.", leaving)
      : t("No file goes to Recently deleted: every one is in your library."),
    disk: computer ? t("The directory on {computer} stays as it is: only Gamma's own .gamma folder goes.", { computer })
      : t("The directory on the computer stays as it is: only Gamma's own .gamma folder goes."),
    offline: computer && plan.computer.online === false
      ? t("{computer} is offline: it stops syncing the folder when it is next online.", { computer }) : "",
  };
}

// What the status says once `name` stopped syncing with `pages` ("keep" or
// "remove"), `answer` the POST's.
export function detachSaid(name, pages, answer) {
  if (pages === "keep") return t("Stopped syncing “{name}”: its files stay in Gamma as pages.", { name });
  const trashed = answer?.trashed || 0;
  return trashed ? tn("Stopped syncing “{name}”: {n} file went to Recently deleted.",
    "Stopped syncing “{name}”: {n} files went to Recently deleted.", trashed, { name })
    : t("Stopped syncing “{name}”: no file went to Recently deleted.", { name });
}
