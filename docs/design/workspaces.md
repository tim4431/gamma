# Workspaces and sharing

Who owns what, who may see and change it, and how a page or a whole
library is shared. The user guide's [Workspaces](../user_guide/user_guide.md#workspaces)
and [Share a page](../user_guide/user_guide.md#share-a-page) show how to
use them.

## Accounts and workspaces

An **account** is a person. A **workspace** is a library: its pages,
files, folders, labels and AI chats. They are kept apart, so one person
can have several libraries and one library several people.

```text
alice ─┬─ Personal              (alice alone)
       ├─ Reading group  ◄────┐ (shared: alice owner, bob editor, carol viewer)
bob ───┴─ Personal        ────┘
```

- **Personal workspaces** have exactly one member, who cannot invite
  anyone into them. Every account has at least one; the last cannot be
  deleted without deleting the account.
- **Shared workspaces** are made by an administrator, who picks the owner.
  Only administrators make them, so a personal storage quota cannot be
  stretched by opening shared libraries.
- **One tab, one workspace.** Switching workspace reloads the app, so
  nothing from one library leaks into the other.

Where a piece of state lives follows from who may see it:

| Belongs to | What |
|---|---|
| the workspace, seen by its members | pages, files, folders, labels, pins, AI chats, kept translations, the glossary |
| you, in every workspace | your AI connections, your preferences and theme, your Inbox |
| you, in one workspace | your open tabs, recently viewed pages, reading positions |

Storage is named by random ids, never by a name someone can change, so a
rename moves no file.

## Roles

| | Viewer | Editor | Owner |
|---|---|---|---|
| Read, search, export, comment | yes | yes | yes |
| Edit pages, organize the library, make share links | | yes | yes |
| Merge an import or a snapshot into the workspace | | yes | yes |
| Manage members, rename, delete, replace from a backup, manage snapshots | | | yes |

A shared workspace always keeps at least one owner.

**Public access** is an administrator's choice, not an owner's: it opens a
shared workspace to every signed-in account on the server as viewers or
as editors. An explicit role wins: a member invited as a viewer stays a
viewer even where public access allows editing. Public access makes no
one a member and is never anonymous.

## Invitations

People with an account on the server are added directly. On a server that
signs in with Gamma Cloud, an owner or an administrator can also invite:

- **by e-mail**, where the server has a Gamma Cloud sign-in of its own
  (not the desktop app's) and admits new people: Gamma Cloud mails the link (the Gamma server itself never
  sends mail). Only signing in through that link admits the person, and it
  works once. It expires after 14 days, and the answer to an invitation is
  the same for every address, so it reveals nothing about who has an
  account.
- **by Gamma Cloud username**: the invitation waits for the person's first
  sign-in. It is kept by the account's id, so a rename on either side
  never sends it to someone else.

An invitation grants viewing or editing, never ownership.

## Sharing a page or a folder

Each page and each folder has one share link, set in its Share popover.
Access adds up in layers:

```text
1. members of the workspace      keep their own role
2. people invited to the share   view or edit, as invited
3. everyone else                 general access:
                                 anyone with the link · any signed-in account · nobody
```

- **A page's link** also opens the pages inside it. **A folder's link**
  opens the pages filed in the folder and below, with the pages inside
  them. A folder's link names the folder by id, so renaming or moving the
  folder keeps it; deleting the folder ends it.
- **An edit link changes notes, not places.** Someone editing through a
  link cannot make library pages, move or delete the shared page itself,
  or change a page's own properties, so a folder's edit link can never
  refile pages into or out of the folder. They can make pages inside the
  shared page.
- **"Anyone with the link can edit"** makes the link itself the key.
  People without an account get a name for their browser ("Curious
  Otter"), which is a label, not an identity; their writes are rate
  limited, and what they upload counts against the workspace.
- **Taking access back is immediate.** Turning a link to view, or stopping
  it, ends its writes at once, open pages included.
- A shared page in Recently deleted cannot be opened until it is restored.

**Publishing** keeps a page reachable while your computer is off. With a
Gamma Cloud account, it is a two-way copy of that page (and the pages inside it) on Gamma Cloud's share
server, at an address of your own. Edits made there through an edit link
come back. Comments stay on each side.

## Guests and the demo

Where a server allows it, *Continue as guest* makes a throwaway account
with one personal workspace, deleted a day after it was made (the
administrator sets how long) or when the guest logs out. A guest cannot
make or join workspaces, use public access or link Gamma Cloud, and has a
small storage quota. The public demo at
[demo.gammapdf.com](https://demo.gammapdf.com) is a server whose sign-in
page offers exactly that.

## Backups

There are three kinds, each for a different loss:

| Kind | Holds | For |
|---|---|---|
| A workspace export | one workspace as a zip | moving a library, or keeping a copy elsewhere |
| A workspace snapshot | one workspace, kept on the server; by hand or on a schedule | going back after a mistake |
| A server snapshot | the whole data directory | recovering the server |

- **Replacing** a workspace from a backup first keeps an automatic
  snapshot of it, and is refused if it cannot.
- **Merging** a backup adds only the pages the workspace lacks, whole.
- A backup never holds passwords, sessions or AI keys, nor anyone's tabs,
  recents or reading positions; a restore keeps the live ones.
- **Deleting a workspace** first writes a final copy, kept for 90 days. If
  the copy cannot be written, nothing is deleted.

## Behind this page

- [Workspaces](../dev/workspaces.md): roles, defaults, data ownership and
  backups as built.
- [API](../dev/api.md): the share permissions and link visitors.
- [Guests](../dev/guests.md), [Storage and accounts](../dev/user_db.md).
- [Workspaces research](../research/workspaces.md): why the workspace is
  the unit people share.
