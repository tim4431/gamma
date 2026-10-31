import { T } from "../../shared/i18n/i18n.js";
// A setup guide (`setup: true`, guide/triggers.js): linking a Zotero
// library, which starts in Settings, may leave for zotero.org to approve
// the key, and ends in the linked workspace, which the app opens with a
// full reload (SettingsMirrors' submitLink). Its steps tick from their
// events wherever the user is, so the run picks up after the reload at the
// sync pill, from the corner card that resumes it. The link dialog asks
// for the account itself when none is connected, so the connect step is
// `optional`: passed over when the dialog opens connected (which ticks it).
// Offered after a Zotero library was imported: a copy, where a link keeps
// the two in step (docs/user_guide/user_guide.md, "Zotero link").
export default {
  id: "zotero-link",
  version: 1,
  setup: true,
  title: T("Zotero link"),
  requires: { guest: false },
  trigger: { event: "import.finished", match: { source: "zotero" } },
  offerAnchor: null,
  offer: { title: T("You imported a Zotero library"), line: T("A Zotero link keeps the two in step instead, both ways.") },
  steps: [
    { id: "zotero-open", anchor: "settings.zoteroLink", placement: "bottom",
      title: T("**Link a Zotero library…**"),
      body: T("In Settings → Workspaces."),
      advanceOn: { event: "dialog.opened", match: { name: "zotero-link" } } },
    { id: "zotero-connect", anchor: "zotero.connect", placement: "right", optional: true,
      title: T("Connect your Zotero account"),
      body: T("Approve it at zotero.org, or paste an API key."),
      advanceOn: { event: "zotero.connected" } },
    { id: "zotero-go", anchor: "zotero.link", placement: "right", media: "zotero-link",
      title: T("**Link**, and the workspace fills as you watch"),
      body: T("Its counts say what the first sync will bring."),
      advanceOn: { event: "zotero.linked" } },
    { id: "zotero-pill", anchor: "sync.pill", placement: "bottom", optional: true,
      title: T("The sync pill says when Zotero needs you"),
      body: T("Its log lists each round; its gear holds the link's settings."), next: T("Done") },
  ],
};
