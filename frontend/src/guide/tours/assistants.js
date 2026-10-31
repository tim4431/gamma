import { T } from "../../shared/i18n/i18n.js";
// A setup guide (`setup: true`): connecting Codex, Claude Code or any MCP
// client to Gamma's MCP server (docs/dev/mcp.md). The command runs in a
// terminal and the workspace is allowed in a browser tab, so the step
// waits for the connection to show in Settings → Integrations, which reads
// its list again when the window gets the focus back (`mcp.connected`).
export default {
  id: "assistants",
  version: 1,
  setup: true,
  title: T("Connect an assistant"),
  requires: { guest: false },
  steps: [
    { id: "assistant-setup", anchor: "settings.assistantSetup", placement: "left", media: "assistant-connect",
      title: T("Pick your assistant and run its command"),
      body: T("Then allow this workspace in the browser it opens."),
      advanceOn: { event: "mcp.connected" } },
    { id: "assistant-ask", anchor: null,
      title: T("Ask it with @Gamma"),
      body: T("*@Gamma, which of my papers use attention?* A Gamma link in the question reads that page.") },
    { id: "assistant-access", anchor: "settings.assistantAccess", placement: "left",
      title: T("Each connection is listed here"),
      body: T("It lasts 90 days; disconnect it here any time."), next: T("Done") },
  ],
};
