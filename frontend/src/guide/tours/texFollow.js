import { T } from "../../shared/i18n/i18n.js";
// A setup guide (`setup: true`): a page whose PDF is pushed from the one a
// TeX build writes, a synced vault of one file in the desktop app
// (docs/research/vaults.md). The app's own picker makes the vault and tells
// the page nothing, so that step only explains; the next waits for the
// build's new version to reach the page (`page.fileReplaced`), which may be
// days later: the guide resumes. The title bar's sync panel is the desktop
// shell's, outside the page, so its card is centred. The id and the step
// ids stay the ones progress was stored under when it said "follow".
export default {
  id: "tex-follow",
  version: 1,
  setup: true,
  title: T("Push a LaTeX build to a page"),
  requires: { desktop: true, hasPdf: true, editable: true },
  steps: [
    { id: "follow-file", anchor: "page.pushFile", placement: "left",
      title: T("Push from a file on this computer…"),
      body: T("Pick the PDF your build writes, such as main.pdf.") },
    { id: "follow-build", anchor: "pdf.viewer", placement: "inside", media: "tex-follow",
      title: T("Build again: highlights move with their text"),
      body: T("Each new version becomes this page's PDF; your notes stay."),
      advanceOn: { event: "page.fileReplaced" } },
    { id: "follow-panel", anchor: null,
      title: T("The title bar's sync button lists it"),
      body: T("Pause it, sync it or remove the vault there."), next: T("Done") },
  ],
};
