# AI chat permissions

Research checked September 29, 2026. The implementation reference is
[AI tools](../dev/ai_tools.md).

[Claude Code's permission rules](https://code.claude.com/docs/en/permissions)
distinguish allow, ask and deny. Reading normally proceeds without a prompt;
actions requiring review stop before execution. Denial remains an explicit rule.

[Codex's sandbox documentation](https://learn.chatgpt.com/docs/sandboxing)
separates the enforced access boundary from approval policy. Approval decides
whether an action proceeds inside the permissions the environment can grant.
[Its remote workflow guide](https://developers.openai.com/blog/mastering-codex-remote-for-engineering)
describes approval scopes, including one action and the current chat.

Gamma adopts three permission states in the existing tool matrix and chat
shortcut. Reads default to Allow; edits and connected journal sign-ins default
to Ask. Existing explicit choices remain effective.

The request appears within the assistant's reply, beside the proposed action
and target. Note edits show their proposed content before the user decides.
Allow once is the primary action because it grants the narrowest permission.
Deny skips the action. Always allow remembers the permission for the originating
chat type, matching the Folder, PDF and Notes settings columns. The card spells
out that scope beside the buttons.

An approval cannot expand workspace membership or the chat's page/folder
boundary. The server checks policy before executing a tool, independently of
the model's instructions and the tools advertised to it. Pending requests close
on cancellation, expiry or disconnect. Decisions remain visible in the reply.

The UI uses Gamma's shared menus, buttons, icons and theme tokens. Color draws
attention only to the pending state; explicit words distinguish all three
permission choices in every theme.
