import { T } from "../../shared/i18n/i18n.js";
// Offered after the account's first comment. A comment is a conversation
// beside a note, a passage or the page, never a change to them
// (docs/dev/comments.md): on a paper the passage's own comment comes first,
// then Resolve on the thread, the Inbox where replies arrive, and the
// Comments window. The thread and the window may be off screen, so their
// steps are `optional`; a guest has no Inbox and no tour.
export default {
  id: "comments",
  version: 1,
  title: T("Comments"),
  requires: { guest: false },
  trigger: { event: "comment.created" },
  offerAnchor: "comments.thread",
  offerPlacement: "left",
  offer: { title: T("You started a conversation"), line: T("Comment on a passage, resolve threads, follow replies.") },
  steps: [
    { id: "comment-passage", anchor: "pdf.viewer", placement: "inside", requires: { hasPdf: true }, media: "comment-passage",
      title: T("Select a passage, then the speech bubble"),
      body: T("Its thread sits in the margin, level with the passage.") },
    { id: "comment-resolve", anchor: "comments.thread", placement: "left", optional: true,
      title: T("**Resolve** a thread once it's settled"),
      body: T("Everyone who can open the page can read and reply.") },
    { id: "comment-inbox", anchor: "header.inbox", placement: "bottom",
      title: T("Replies and mentions reach your Inbox"),
      body: T("Click one to go to its thread.") },
    { id: "comment-window", anchor: "comments.grip", placement: "left", optional: true,
      title: T("**View → Comments** lists every thread"), next: T("Done") },
  ],
};
