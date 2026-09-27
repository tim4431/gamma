import { T } from "../../shared/i18n/i18n.js";
// Started from the Tours menu; `show` brings the chat up first. What the
// chat can do depends on where it is, so each place has its own steps
// (step-level `requires`), each ending on Send — a step that only waits for
// the user's own send (`chat.sent`); the tour itself never sends or records.
// With no AI connected there is one step: the setup card, which the chat
// shows instead of a usable message box (docs/dev/onboarding.md).
const ON = { aiConfigured: true };
export default {
  id: "ai-chat",
  version: 2,
  title: T("AI chat"),
  show: "chat",
  steps: [
    // No AI connected: the one thing that makes the chat work. Clicking a
    // tile opens Settings → Connections, which finishes the tour.
    { id: "chat-setup", anchor: "chat.setup", placement: "left", requires: { aiConfigured: false, aiEditable: true },
      title: T("Chat needs an AI connection: add a key, or sign in with ChatGPT"),
      body: T("Pick a service; the connect dialog opens on it."),
      advanceOn: { event: "settings.opened", match: { pane: "ai" } }, next: T("Done") },

    // On a paper: a question, a figure, the snapshot it makes, Send.
    { id: "chat-question", anchor: "chat.input", placement: "top", requires: { ...ON, view: "pdf" },
      title: T("Ask about this paper; type @ to bring in another one"),
      do: [{ type: "chat.input", text: T("What is the main result, and where is it shown?"), preserveDraft: true }, { wait: 1000 }] },
    { id: "chat-figure", anchor: "pdf.viewer", placement: "inside", requires: { ...ON, pdfChatVisible: true },
      title: T("Ask about a figure: Ctrl-drag a box around it"), do: [{ previewArea: true, context: true }] },
    { id: "chat-snapshot", anchor: "chat.imageContext", placement: "top", requires: { ...ON, pdfChatVisible: true },
      title: T("The snapshot goes with your question") },
    { id: "chat-send-paper", anchor: "chat.composer", placement: "top", requires: { ...ON, view: "pdf" },
      title: T("Send it: answers link to the exact passages they quote"),
      advanceOn: { event: "chat.sent" }, next: T("Done") },

    // On a page of notes: a question, rewriting part of a note, Send.
    { id: "chat-question-notes", anchor: "chat.input", placement: "top", requires: { ...ON, view: "page" },
      title: T("Ask about this page, or have it rewrite a note"),
      do: [{ type: "chat.input", text: T("Turn these notes into a short summary"), preserveDraft: true }, { wait: 1000 }] },
    { id: "chat-note-selection", anchor: "dock.notes", placement: "left", requires: { ...ON, view: "page" }, optional: true,
      title: T("Drag across a note's text to change just that part") },
    { id: "chat-send-notes", anchor: "chat.composer", placement: "top", requires: { ...ON, view: "page" },
      title: T("Send it, or change the question first"),
      advanceOn: { event: "chat.sent" }, next: T("Done") },

    // On the library: the agent searches, reads and files pages.
    { id: "chat-question-library", anchor: "chat.input", placement: "top", requires: { ...ON, view: "home" },
      title: T("Ask across your library: it can search, read and file your pages"),
      do: [{ type: "chat.input", text: T("Which of these papers use attention? File them into ML/attention"), preserveDraft: true }, { wait: 1000 }] },
    { id: "chat-tools", anchor: "chat.tools", placement: "bottom", requires: { ...ON, view: "home" }, optional: true,
      title: T("Choose which tools it may use; every action shows as a chip you can open") },
    { id: "chat-send-library", anchor: "chat.composer", placement: "top", requires: { ...ON, view: "home" },
      title: T("Send it, or change the question first"),
      advanceOn: { event: "chat.sent" }, next: T("Done") },
  ],
};
