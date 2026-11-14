// Detach, the one way a synced folder leaves Gamma (docs/dev/work_folders.md
// "Detaching a work folder"): the words of its menu row, its dialog
// (DetachDialog.jsx) and the status after it, from what GET
// /api/folders/{id}/detach answers (`plan`: {may, files, in_library,
// too_large, bytes, computer: {name, online} | null}) and what POST answers
// ({ok, kept, trashed}). The interface says "synced folder"; the code and
// the API say work folder. Pure: tests/detach.test.mjs.
import { fmtBytes } from "../shared/lib/format.js";
import { t, tn } from "../shared/i18n/i18n.js";

// The computer that keeps the folder, as the plan names it ("" for none
// named: none keeps it, or it is another account's).
const computerOf = (plan) => (typeof plan?.computer?.name === "string" ? plan.computer.name : "");

// The menu row: "Detach from TIM-PC…", or "Detach…" with no computer named.
export const detachLabel = (plan) => (computerOf(plan)
  ? t("Detach from {computer}…", { computer: computerOf(plan) }) : t("Detach…"));

// The dialog's words for the folder `name`: {title, counts, keep, remove,
// disk, offline}, `counts` its files and those in the library, `keep` and
// `remove` what each choice does, `disk` that the directory stays as it
// is, `offline` ("" while online) that a computer away forgets it later.
export function detachText(plan, name) {
  const computer = computerOf(plan);
  const files = plan?.files || 0;
  const inLibrary = plan?.in_library || 0;
  const tooLarge = plan?.too_large || 0;
  const leaving = Math.max(files - inLibrary, 0);
  return {
    title: computer ? t("Detach “{name}” from {computer}?", { name, computer }) : t("Detach the synced folder “{name}”?", { name }),
    counts: [
      files ? tn("{n} file syncs with it.", "{n} files sync with it.", files) : t("No file syncs with it yet."),
      inLibrary ? tn("{n} of them is in your library and stays there either way.",
        "{n} of them are in your library and stay there either way.", inLibrary) : "",
    ].filter(Boolean).join(" "),
    keep: [
      t("Each file becomes an ordinary page, filed where it is."),
      plan?.bytes ? t("Their bytes are stored in Gamma: {size}.", { size: fmtBytes(plan.bytes) }) : "",
      tooLarge ? tn("{n} file too large to keep stays listed by name, without its bytes.",
        "{n} files too large to keep stay listed by name, without their bytes.", tooLarge) : "",
    ].filter(Boolean).join(" "),
    remove: leaving ? tn("{n} file goes to Recently deleted.", "{n} files go to Recently deleted.", leaving)
      : t("No file goes to Recently deleted: every one is in your library."),
    disk: computer ? t("The directory on {computer} stays as it is: only Gamma's own .gamma folder goes.", { computer })
      : t("The directory on the computer stays as it is: only Gamma's own .gamma folder goes."),
    offline: computer && plan.computer.online === false
      ? t("{computer} is offline: it stops syncing the folder when it is next online.", { computer }) : "",
  };
}

// What the status says once `name` was detached with `pages` ("keep" or
// "remove"), `answer` the POST's.
export function detachSaid(name, pages, answer) {
  if (pages === "keep") return t("Detached “{name}”: its files stay in Gamma as pages.", { name });
  const trashed = answer?.trashed || 0;
  return trashed ? tn("Detached “{name}”: {n} file went to Recently deleted.", "Detached “{name}”: {n} files went to Recently deleted.",
    trashed, { name }) : t("Detached “{name}”: no file went to Recently deleted.", { name });
}
