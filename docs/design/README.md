# Gamma's design

How Gamma is designed, and why: where your library lives, what a page is,
what wins when two copies disagree, what the AI may change, what the
colours are. The [user guide](../user_guide/user_guide.md) shows how to
use each part; these pages explain the model behind it. The mechanics,
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
- **The AI works for you, within your rights.** It reaches only what you
  could, asks before it changes anything unless you allow it, and never
  deletes a page for good ([AI](ai.md)).
- **One behaviour on every device.** The same controls at the same sizes
  under a mouse, a finger and a pen; each command declared once, behind
  every key, menu and toolbar that offers it ([Interaction](interaction.md)).

## The pages

| Page | What it explains |
|---|---|
| [Architecture](architecture.md) | The server and its apps, where your data is kept, Gamma Cloud, upgrades, privacy |
| [Blocks and pages](blocks.md) | Everything is a block; a page and its document; pages inside pages; notebooks; Markdown notes |
| [The library](library.md) | Folders and labels, files from a synced folder, pins and recents, Recently deleted, who may organize |
| [Reading and marking](reading.md) | Highlights that survive a new version of the PDF, handwriting and the pen, recordings, translation |
| [Comments and the inbox](comments.md) | Threads beside the notes, who hears about what, comments as work for an agent |
| [Workspaces and sharing](workspaces.md) | Accounts and workspaces, roles, invitations, share links, backups |
| [Sync and offline](sync.md) | Editing together, offline copies, folders on disk, the Zotero link, and what wins each clash |
| [AI](ai.md) | Your own model, what the AI sees, reaches and may change, agents on your computer, MCP |
| [Formats and portability](formats.md) | What each export keeps, the annotated PDF, long work as background tasks |
| [Interaction](interaction.md) | Mouse, finger and pen alike; menus and dialogs; one catalog of commands; where a setting lives |
| [Look and feel](look.md) | Themes and how one is made, surfaces, type, icons, buttons, motion, the colour palette, the brand |
