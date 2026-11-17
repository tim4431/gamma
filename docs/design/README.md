# Gamma's design

How Gamma is designed, and why: what a page is, how folders and labels
work out, what wins when two copies disagree, what the AI may change, what
the colours are. The [user guide](../user_guide/user_guide.md) shows how
to use each part; these pages explain the model behind it. The mechanics,
down to the code, are in the [developer guide](../dev/README.md), which
each page links at its end.

## The ideas that run through it

- **Your library lives on a server you choose.** One server, the same in a
  Docker image, on a NAS or inside the desktop, iPad and Android apps,
  holds your library in plain SQLite and files. There is no telemetry, and
  Gamma Cloud is optional ([Architecture](architecture.md)).
- **One shape for everything you write.** Every note, highlight, drawing
  and sheet is a block, and a page is the only container. There are no
  page types: a page is described by what it carries
  ([Blocks and pages](blocks.md)).
- **Nothing is lost silently.** A deleted page waits 30 days. An edit
  beats a delete. A clash between two copies keeps the losing text where
  you can take it back. A highlight whose text changed is drawn dashed for
  you to check, not moved quietly. An import lists what it left out
  ([Sync and offline](sync.md), [Reading and marking](reading.md)).
- **Your files stay yours.** Your marks live beside a PDF, never written
  into it. Notes are Markdown. Exports use the other side's own formats
  ([Formats](formats.md)).
- **One behaviour on every device.** The same controls at the same sizes
  under a mouse, a finger and a pen; each command declared once, behind
  every key, menu and toolbar that offers it ([Interaction](interaction.md)).
- **The AI works for you, within your rights.** It reaches only what you
  could, asks before it changes anything unless you allow it, and never
  deletes a page for good ([AI](ai.md)).

## The pages

| Page | What it explains |
|---|---|
| [Look and feel](look.md) | Themes and how one is made, surfaces, type, sizes, icons, buttons, motion, the colour palette, the brand |
| [Interaction](interaction.md) | Mouse, finger and pen alike; menus and dialogs; the keyboard's one command catalog; where a setting lives; languages |
| [Blocks and pages](blocks.md) | Everything is a block; a page and its document; files in notes; pages inside pages; notebooks; Markdown notes |
| [The library](library.md) | Folders and labels, pages inside pages in the library, files from a synced folder, pins and recents, Recently deleted, who may organize |
| [Reading and marking](reading.md) | Highlights that hold on to their text through a new version, handwriting and the pen, text boxes and pictures, recordings, translation, trustworthy records |
| [Comments and the inbox](comments.md) | Threads beside the notes, who may comment, mentions and reminders, the Inbox, comments as work for an agent |
| [Workspaces and sharing](workspaces.md) | Accounts and workspaces, roles, invitations, share links, guests, backups |
| [Sync and offline](sync.md) | Editing together, offline copies, synced folders on disk, the Zotero link, and what wins each kind of clash |
| [AI](ai.md) | Your own model, what the chat sees, what the agent may reach and change, agents on your computer, assistants over MCP |
| [Formats and portability](formats.md) | What each export keeps, the annotated PDF, background tasks |
| [Architecture](architecture.md) | The server and its apps, where data is kept, Gamma Cloud, upgrades, privacy, running your own server |

## Keeping these pages true

These pages describe Gamma as it is. A change to a rule they state
changes the page in the same commit, and a page links the developer
docs that hold the mechanics rather than repeating them. The pictures of
the themes and the palette are drawn from the app's own values: run
`node tools/designSheets.mjs` in `frontend/` after changing a theme or
the default palette, and `npm test` there fails until you do.
