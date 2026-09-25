import { t, T } from "../../shared/i18n/i18n.js";
export default {
  id: "ai-chat",
  version: 2,
  title: T("AI chat"),
  steps: [
    { id: "chat-question", anchor: "chat.input", placement: "top", title: T("Ask about your paper"),
      do: [{ type: "chat.input", text: T("summarize the paper for me"), preserveDraft: true }, { wait: 1000 }] },
    { id: "chat-voice", anchor: "chat.voice", placement: "top", title: T("Or use your voice") },
    { id: "chat-box", anchor: "pdf.viewer", placement: "inside", title: T("Ctrl-drag a box for context"),
      requires: { pdfChatVisible: true }, do: [{ previewArea: true, context: true }] },
    { id: "chat-box-context", anchor: "chat.imageContext", placement: "top", title: T("Your selection is now chat context"),
      requires: { pdfChatVisible: true }, next: T("Done") },
  ],
};
