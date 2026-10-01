import { T } from "../../shared/i18n/i18n.js";
// Offered the first time the account starts a job on the server (an export,
// a backup, a restore, a library import). What is worth teaching is not the
// tray but the fact behind it: the work is the server's, so it outlives the
// dialog and the tab — which is what the drawing shows (guide/media.js,
// docs/dev/tasks.md). On a phone the tray is a row of the More sheet with no
// anchor of its own, so the tour neither lists nor offers itself there.
export default {
  id: "tasks",
  version: 1,
  title: T("Background tasks"),
  requires: { phone: false },
  trigger: { event: "job.started" },
  offerAnchor: "header.tasks",
  offerPlacement: "bottom",
  offer: { title: T("That work is running in the background"), line: T("Where to watch it, stop it, or pick up the file.") },
  steps: [
    { id: "task-tray", anchor: "header.tasks", placement: "bottom", media: "tasks-keep-going",
      title: T("Long work carries on without you"),
      body: T("It runs on the server, so closing the dialog — or this tab — does not stop it, and your other devices see it too.") },
    { id: "task-row", anchor: "tasks.row", placement: "left", optional: true,
      title: T("Every job keeps its own row"),
      body: T("Stop it, download what it made, or start it again. Clicking an export or import row opens its dialog on that job.") },
    { id: "task-clear", anchor: "tasks.clear", placement: "bottom", optional: true,
      title: T("Clear finished tidies the list and the files with it"),
      body: T("A finished file is kept for a day anyway, then swept."),
      next: T("Done") },
  ],
};
