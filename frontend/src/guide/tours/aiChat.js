import { T } from "../../shared/i18n/i18n.js";
// Started from the Tours menu; `show` brings the chat up first. What the
// chat can do depends on where it is, so each place has its own steps
// (step-level `requires`), each with the model chip (optional: there only
// once the models have loaded) and Send — a step the user's own send
// (`chat.sent`) ticks; the tour itself never sends or records. Every place
// ends on New chat, which keeps the conversation in History (Alt+click
// discards it). With no AI connected there is one step: the setup card,
// which the chat shows instead of a usable message box
// (docs/dev/onboarding.md).
const ON = { aiConfigured: true };
// The model, its reasoning effort and speed: one chip, the same in every place.
const model = (place) => ({ id: `chat-model-${place}`, anchor: "chat.model", placement: "top", optional: true,
  requires: { ...ON, view: place }, title: T("Pick the model, effort and speed") });
export default {
  id: "ai-chat",
  version: 2,
  title: T("AI chat"),
  show: "chat",
  steps: [
    // No AI connected: the one thing that makes the chat work. Clicking a
    // tile opens Settings → Connections, which finishes the tour.
    { id: "chat-setup", anchor: "chat.setup", placement: "left", requires: { aiConfigured: false, aiEditable: true },
      title: T("Connect an AI to use chat"),
      body: T("Pick a service: add its key, or sign in with ChatGPT."),
      advanceOn: { event: "settings.opened", match: { pane: "ai" } }, next: T("Done") },

    // On a paper: a question, a figure whose snapshot joins it, Send.
    { id: "chat-question", anchor: "chat.input", placement: "top", requires: { ...ON, view: "pdf" },
      title: T("Ask about this paper"),
      body: T("Type @ to bring in another one."),
      do: [{ type: "chat.input", text: T("What is the main result, and where is it shown?"), preserveDraft: true }, { wait: 1000 }] },
    { id: "chat-figure", anchor: "pdf.viewer", placement: "inside", requires: { ...ON, pdfChatVisible: true },
      title: T("{key:pdf.areaDrag}+drag a box around a figure"),
      body: T("The snapshot goes with your question."),
      do: [{ previewArea: true, context: true }, { point: "chat.imageContext", wait: 1400 }] },
    model("pdf"),
    { id: "chat-send-paper", anchor: "chat.composer", placement: "top", requires: { ...ON, view: "pdf" },
      title: T("Send it"),
      body: T("Answers link to the passages they quote."),
      advanceOn: { event: "chat.sent" } },

    // On a page of notes: a question (or a rewrite of part of a note), a
    // note attached with a Ctrl+click, Send.
    { id: "chat-question-notes", anchor: "chat.input", placement: "top", requires: { ...ON, view: "page" },
      title: T("Ask about this page"),
      body: T("Or select part of a note to have it rewritten."),
      do: [{ type: "chat.input", text: T("Turn these notes into a short summary"), preserveDraft: true }, { wait: 1000 }] },
    { id: "chat-attach-note", anchor: "dock.notes", placement: "left", requires: { ...ON, view: "page" },
      title: T("{key:Mod}+click a note to attach it"),
      scene: [{ click: "dock.notes", key: "Mod", at: [0.3, 0.2] }] },
    model("page"),
    { id: "chat-send-notes", anchor: "chat.composer", placement: "top", requires: { ...ON, view: "page" },
      title: T("Send it"),
      advanceOn: { event: "chat.sent" } },

    // On the library: the agent searches, reads and files pages.
    { id: "chat-question-library", anchor: "chat.input", placement: "top", requires: { ...ON, view: "home" },
      title: T("Ask across your library"),
      body: T("It can search, read and file your pages."),
      do: [{ type: "chat.input", text: T("Which of these papers use attention? File them into ML/attention"), preserveDraft: true }, { wait: 1000 }] },
    { id: "chat-tools", anchor: "chat.tools", placement: "bottom", requires: { ...ON, view: "home" }, optional: true,
      title: T("Choose the tools it may use"),
      body: T("Each action shows as a chip you can open.") },
    model("home"),
    { id: "chat-send-library", anchor: "chat.composer", placement: "top", requires: { ...ON, view: "home" },
      title: T("Send it"),
      advanceOn: { event: "chat.sent" } },

    // Everywhere: New chat, and where the old conversation went. Only where
    // conversations are kept (a viewer's chat stays in the tab).
    { id: "chat-new", anchor: "chat.new", placement: "bottom", requires: { ...ON, editable: true },
      title: T("**+** starts a new chat"),
      body: T("The old one waits in **History**; {key:Alt}+click discards it."),
      scene: [{ click: "chat.new" }, { point: "chat.history" }, { wait: 300 }, { click: "chat.new", key: "Alt" }] },
  ],
};
