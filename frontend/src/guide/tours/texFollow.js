import { T } from "../../shared/i18n/i18n.js";
// A setup guide (`setup: true`): a page that follows the PDF a TeX build
// writes, in the desktop app (docs/dev/folder_sync.md). The app's own
// picker follows the file and tells the page nothing, so that step only
// explains; the next waits for the build's new version to reach the page
// (`page.fileReplaced`), which may be days later: the guide resumes. The
// title bar's sync panel is the desktop shell's, outside the page, so its
// card is centred.
export default {
  id: "tex-follow",
  version: 1,
  setup: true,
  title: T("Follow a LaTeX build"),
  requires: { desktop: true, hasPdf: true, editable: true },
  steps: [
    { id: "follow-file", anchor: "page.follow", placement: "left",
      title: T("Follow a file on this computer…"),
      body: T("Pick the PDF your build writes, such as main.pdf.") },
    { id: "follow-build", anchor: "pdf.viewer", placement: "inside", media: "tex-follow",
      title: T("Build again: highlights move with their text"),
      body: T("Each new version becomes this page's PDF; your notes stay."),
      advanceOn: { event: "page.fileReplaced" } },
    { id: "follow-panel", anchor: null,
      title: T("The title bar's sync button lists it"),
      body: T("Pause, sync or stop following there."), next: T("Done") },
  ],
};
