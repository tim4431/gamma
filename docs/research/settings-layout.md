# The Settings dialog's layout

How the Settings dialog's panes were arranged in November 2026, what read
badly once every pane was looked at in turn, what was regrouped, and what
was seen but left. The dialog as built: [dev/settings.md](../dev/settings.md)
"The Settings dialog".

## What was looked at

Every pane, opened as an admin on a fresh server (one account, no AI, no
shared workspaces) at 1280 × 860, once as the dialog shows it and once
unrolled to its full height. The unrolled heights, the dialog's 65 px head
included, before the change:

| Pane | Height | Sections |
|---|---|---|
| Appearance | 2617 px | Theme, PDF pages, Colors, Library, Interface, Top bar, Viewer tools, Text highlighters, Tours |
| Reading & editing | 1578 px | PDFs, Replacing a PDF, Notes, Handwriting, This device, Recordings, Search opens as |
| Server | 1947 px | Dashboard, Assistant connections, Sign-in, Reports, Storage defaults, Guests, Shared AI provider, Shared workspaces, Databases, Server backups, Log |
| Tool usage | 2056 px | Tools (the permission table), Online search, Tool limits |
| Keyboard | 4270 px | Shortcuts, Built in |
| Language and Translation | 1110 px | Language, Viewer & selection, Service, Glossary, Speed |
| every other pane | under 900 px | |

## What read badly

- **Panes named for one subject held several.** Appearance held what the
  bars offer and the text highlighters (not how things look) and the tours
  (help). Reading & editing held the pen: the account's pen habits, this
  device's input and, in the apps, the pen's gestures, half the pane. Server
  held who may come in (sign-in, guests, the storage every account starts
  with, the reports people make), next to the machine itself (its build,
  backups, databases and log).
- **Two long panes were long for that reason**, not for one big table:
  Appearance and Server. Keyboard and Tool usage are long because each is
  one list or table, which a filter (Keyboard) or presets (Tool usage)
  already make usable.
- **Entry points led to the wrong pane's name.** The strip's
  *Handwriting settings…* landed in "Reading & editing", the bars'
  *Customize…* in "Appearance", and an admin's sign-in notices in "Server".
- **"Language and Translation"** wrapped to two lines in the rail, the one
  label that did, and read unlike its neighbours ("Reading & editing",
  "Account & sync").
- **Lists added rows three ways.** Most sections put their *Add* or *New*
  on the section's rule (Workspaces, Backups, Glossary); Users put
  *+ Add user* and *Invite by e-mail…* as plain buttons under its rows with
  no section; Connections put a primary *+ Add provider* under its list and
  Server an outlined one on the rule, each spelling the plus as text.

## What changed

The rule now written in the dev doc: a pane holds one subject, and a
setting goes into the pane whose subject it is.

- **Toolbars**, a new pane: Top bar, Viewer tools and Text highlighters,
  out of Appearance. The `topbar`, `viewer-tools` and `highlighters` entry
  points open it.
- **Handwriting**, a new pane: Pens & eraser (the account's section,
  renamed from "Handwriting" inside a pane of that name), This device and
  the Apple Pencil or Stylus section, out of Reading & editing. The strip's
  *Handwriting settings…* names it.
- **Users** gained Storage defaults, Sign-in, Guests and Reports under the
  accounts, which are a section ("Accounts") with *Add user* and *Invite by
  e-mail…* on its rule. The `cloud-setup`, `join-requests` and `reports`
  notices point at it, and the Gamma Cloud connect round trip reopens it.
- **Help & diagnostics** gained Tours and now leads with Help.
- **Language & translation**, renamed, on one line once the rail is 224 px.
- *Add provider* in both AI lists and *Add user* use the plus icon, on the
  section's rule once a list exists.

After, unrolled: Appearance 1546 px, Toolbars 1161, Reading & editing 956,
Handwriting 800, Users 957, Server 1332, Help & diagnostics 780.

The two new panes make an admin's rail eighteen rows, which overflowed the
760 px dialog by 80 px. The rows went from 29.5 to 27.5 px, the group
captions' gap from 10 to 8 px, and the dialog's cap from 760 to 800 px, so
the rail fills the dialog exactly; a shorter window scrolls it, as it could
before. A nineteenth pane needs a decision: a rail that scrolls at full
height, or two panes made one.

The nineteenth came with the November 2026 UI review
([ui-review-2026-11.md](ui-review-2026-11.md)): **Agents**, first in the AI
group, out of the pane titled "Connections" (whose own first section, named
like the pane, became "AI services"). To keep an admin's rail at the
dialog's full height, the 1 px gap between the rows went (about 21 px over
the groups) and the captions' gap from 8 to 6 px (8 px over four), about
the 28.5 px a row and its gap take; the figures are worked out from the
sizes above, not measured again. On a shorter window, or if a face draws
the rows taller, only the groups scroll, between the account card and Help
(`.settingsNavList`, a thin scrollbar so "Language & translation" keeps its
line).

## Weighed and left

- **Network proxy into Server.** It would have saved a rail row, and a
  proxy is the server's. It stayed a pane: it is a subject of its own, with
  its own Save and Test, and Server would have grown back by most of what
  it lost.
- **Storage shows four times**, all the same `/api/quota` number: the
  account card, Account & sync's own row, Workspaces › Storage, and
  Maintenance › Storage. Workspaces' row explains that shared workspaces
  carry their own quota, and Maintenance shows it beside the indexes;
  neither was taken out.
- **Shared workspaces** are listed under Server for an admin and under
  Workspaces › Shared for their members, whose empty state offers an admin
  *New shared workspace* as a jump to Server. One place for both would
  need the admin list's management inside the Workspaces pane.
- **Backups in two panes**: a workspace's snapshots and tasks in Backups,
  the whole data directory's under Server › Server backups. Different
  subjects, named alike.
- **Pane subtitles.** The rule says a pane carries none, yet Integrations,
  Backups and Network proxy do; each explains a pane whose name says
  little. Left as they are, and the rule now names them as its exceptions
  ([dev/settings.md](../dev/settings.md) "The Settings dialog").
- **Appearance** draws its Interface rows boxed and its rows without the
  hover fill, unlike every other pane; it is consistent within the pane.
