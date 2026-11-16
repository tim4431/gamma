import { T } from "../../shared/i18n/i18n.js";
// Offered after the account's first comment sent to an agent (Send to
// Codex ticked, or `@codex` typed, which ticks it) that started its turn
// (docs/dev/local_agents.md "A comment sent to an agent"). The agent
// answers in the thread under its own name; Open in chat shows the turn
// with every step it took; a reply sent to it goes on where it left off.
// The Open in chat step waits on its answer's comment, so it is `optional`.
// Only where a comment can ask an agent (`commentAgents`): the desktop app's
// own server, or a synced folder another computer of the account keeps.
export default {
  id: "agent-comments",
  version: 1,
  title: T("Asking an agent"),
  requires: { guest: false, editable: true, commentAgents: true },
  trigger: { event: "comment.created", match: { agent: true } },
  offerAnchor: "comments.thread",
  offerPlacement: "left",
  offer: { title: T("You asked an agent"), line: T("It works on its own and answers in this thread.") },
  steps: [
    { id: "agent-thread", anchor: "comments.thread", placement: "left",
      title: T("It answers here, under its own name"), body: T("The tag over the reply field says what it's doing.") },
    { id: "agent-chat", anchor: "comments.openChat", placement: "left", optional: true,
      title: T("**Open in chat** shows its work"), body: T("Every step it took, and each change with its own undo.") },
    { id: "agent-reply", anchor: "comments.thread", placement: "left",
      title: T("Reply with **Send to** ticked to go on"), body: T("It picks up where it left off.") },
  ],
};
