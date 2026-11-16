# UI review of the agents-as-participants features (November 2026)

What the features built after the 14 November UI check look like against
[ui-design.md](../dev/ui-design.md): synced folders as an explorer and
Detach, agents in Settings and the chat, comments that ask an agent, CLI
sessions in History, the Settings regroup, bucket backups, the network
proxy, PDF page direction and GoodNotes export. Checked on dev at 684c2887,
on 15 November 2026.

## How it was checked

- Four read-only audits of the code, one per area (the explorer and
  Detach; the chat with `desktop/ui/bar.html`; comments, the agent's
  identity and the Inbox; Settings and the smaller features). Each read all
  of ui-design.md first and cites file and line. The high-severity findings
  were checked again by hand.
- Screenshots from an isolated server (scratch data directory, the browser
  suite's fake Codex and Claude Code, the desktop app's user agent).
- The three ratchets. The token scan finds nothing new. The conventions
  scan finds only three 12 px icons in chat chips (`GrantChip`,
  `ReplyPapers`, `RevertChanges`). Neither scan sees what is left:
  composition, wording and what an icon means.

The phone and narrow-width findings are worked out from the CSS sizes, not
measured on a device. Failed, queued and refused agent turns were not
reproduced on screen.

## What was found

The 55 findings fall into eight themes. Each theme has one shared fix
rather than a patch per surface.

1. **An agent's identity.** Each surface names and draws the agent its own
   way:
   - The Inbox draws Codex with `PersonAvatar`, coloured from "Codex on
     TIM-PC".
   - History's agent rows share one bot glyph and no name.
   - The chat's agent picker is labelled "Model, reasoning effort and
     speed".
   - The comment tick says "Send to Codex" with no computer or owner.

   Shared fix: one wording helper (agent · computer · whose) and one
   `ActorAvatar`.
2. **An agent's state.** `StateTag` is used only while a turn goes well:
   - A failed or stopped turn is an ordinary agent comment, worded in
     English by the server (`local_agents/threads.py` `_said`), with an
     Inbox item even for the reader's own Stop.
   - A refusal after sending lives a second in the status pill.
   - A queued turn says "Codex is working…".
   - Computer agents' steps show tool ids ("Read Gamma: list_annotations").

   Shared fix: `StateTag` with an icon and the tones a turn needs (working,
   queued, asking, failed, stopped, not sent), in the thread, the strip,
   the picker and the keeper line.
3. **Asking from a comment.**
   - The send button keeps its arrow and "Comment" name when ticked.
   - A workspace viewer is offered the tick and always refused.
   - Every agent reply carries a text "Open in chat", which wraps the time
     in a 280 px card.
4. **The chat with a computer agent.**
   - A changed file's link outside a synced folder falls back to a raw
     `<a href>` and opens a broken tab.
   - The working directory is picked in the strip, far from Send, and
     locks without saying so.
   - "Chat settings" and "Tools on/off" sit side by side and look like two
     settings buttons.
   - The desktop bar's approval card uses other words and another
     hierarchy than `ApprovalShell`.
5. **Detach.** It is a reversible pause for a clone ("Nothing is lost") but
   permanent for a synced folder, which can send every file to Recently
   deleted. Both share the word, `UnlinkIcon` and 分离. The web app draws
   the synced folder's row as a plain row, while the desktop panel draws
   it as a danger row. Proposed: "Stop syncing with {computer}…" as a
   danger row.
6. **Folder view and Files window.**
   - Inside a synced folder nothing says it is synced or where.
   - The keeper line squeezes the name, against the Interface size rule
     that meta ellipsizes first.
   - The folder view keeps about 323 px of fixed columns on a phone.
   - The pin's hover reveal targets `.fileRow`, so on a `.fileTreeRow` it
     stays invisible yet clickable.
   - Material Icon Theme's root glyph, a hollow ring, reads as a status dot.
7. **Settings.**
   - There is no Agents entry in the rail: agents open the pane titled
     "Connections", which ends with a section also called "Connections".
   - Irreversible deletes are plain buttons, while reversible removals
     are red.
   - Several panes hand-draw their errors instead of using `FormNotice`.
   - Some disabled controls give their reason only in a title.
   - Page direction's row icon follows its value.
   - The GoodNotes and Notability export cards read the same.
8. **Words.** Thread and comment, 讨论 and 评论, vault and synced folder;
   "Go to folder" sits beside "Show in folder"; a dozen Chinese strings
   need fixing.

The parts already done well: one transcript for Gamma AI and computer agents;
one `TreeRow` and `useTree` for both trees; the Detach dialog, the bucket
dialog and Network proxy as their docs describe them; clean ratchets.

## What was decided and built

The owner approved all 55 proposals, and the branch `agent-ui-improve`
builds them, with the dev docs describing each surface as it now is.
Three of them settled a question the review had left open:

- **Agents got a rail row of their own.** It is the first in the AI group,
  and Connections keeps the providers. With the nineteenth row the rail
  still fits an admin's 900 px window: the gap between rows went from 1 px
  to none and the caption gap from 8 px to 6 px. A shorter window scrolls
  only the groups, never the account card or Help.
- **The synced folder's root wears `SyncedFolderIcon`** (lucide's
  folder-sync), not Material's root ring. Its arrows take the sync state's
  tone. The card, the row, the Files window's root and the folder view's
  heading all draw it.
- **No new wording helper was needed.** `chat/agents.js` already had
  `agentChoice`, `madeBy` and `agentWords`, and the Inbox, History, the
  chip, the comment tick and the threads now all call them.

## Still open

- The server's own words for a computer agent's Gamma tool calls (the
  runner's at-work line, the chat export) still read "Reading Gamma:
  read_page". The frontend's tool-words table could move to the server.
- The desktop bar has no translation mechanism, so its approval card is
  English only.
- A failed turn's Inbox item still shows the server's English sentence as
  its snippet.
- The phone layouts (the folder view's second line, the keeper's line)
  were worked out from the CSS and checked in the browser suite, not on a
  device.
