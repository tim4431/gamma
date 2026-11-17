# Architecture

How Gamma is put together: one server and the apps around it, where your
data is kept, what Gamma Cloud adds, and what an upgrade promises. The
README's [install section](../../README.md#install) covers setting it up.

## One server, many windows

```text
  web browser    desktop app    iPad app    Android app    Gamma Connector    assistants (MCP)
       │             │              │            │               │                  │
       │       its own server  its own server  its own server    │                  │
       │        + any remote    (on device)    (on device)       │                  │
       └─────────────┴──────────────┴────────────┴───────────────┴──────────────────┘
                                         │
                                   a Gamma server
                          the web app · the API · one data directory
```

Gamma is a self-hosted app. One server, the same one in a Docker image, on
a NAS or inside an app, serves both the web app and its API. Every other
part is a window onto that server, or a small client of its API:

- **The desktop app** starts a server of its own on this computer, with its
  own data directory, and can open other servers by address: a NAS, a
  hosted Gamma. It adds what only a desktop can do: synced folders, files
  opened from disk, and agents running on this computer.
- **The iPad and Android apps** carry the same server, running on the
  device, so they work with no connection. The native side is only a
  window, a pen layer for the stylus, sign-in and file hand-off; it holds
  no page or stroke of its own. Each web feature therefore reaches the
  tablets once, rather than being built twice.
- **Gamma Connector**, the browser extension, finds the paper on a page
  and hands it to your server, which does the work of adding it. Where the
  server cannot fetch the PDF itself, the extension downloads it with your
  browser's sign-in and uploads it.
- **Assistants** such as Codex and Claude Code connect over MCP with a
  token ([AI](ai.md#assistants-over-mcp)).

Servers are independent: nothing passes between two of them except what
you set up, such as an offline copy ([Sync](sync.md#offline-copies)).
Offline work on another server's library is an offline copy kept by the
app's own server. A tablet syncs its copies only while the app is open,
since iOS and Android suspend apps in the background.

## Where your data is kept

Everything is SQLite databases and files under one data directory:

```text
data/
├── users.db                accounts, sessions, workspaces, members, share links, account settings
└── workspaces/<random id>/
    ├── pages.db            every block, the change log, AI chats, per-person tabs and recents
    ├── data.db             what can be rebuilt: the search index, covers, kept translations
    └── uploads/            files, named by their content, so the same file is stored once
```

- **Accounts and workspaces are named by random ids**, never by a name
  someone can change, so a rename moves no file.
- **A workspace is the unit.** One process owns a workspace's files and
  the people editing it. A single shared database server was declined:
  every deployment would need one, the desktop app would have to embed
  one, and search, upgrades, backups and sync would all have to be ported.
  The accepted limit is that one workspace cannot outgrow one process's
  writes.
- **Paths on your computer stay on your computer.** The desktop app keeps
  its synced folders' paths in a database of its own, outside every
  workspace, so no workspace backup carries one: a backup that carried a
  path could make the server it is restored on read a file it should not.
- A PDF read where it lies on your disk is not copied into the data
  directory, does not count toward a quota and is not in backups.

## Gamma Cloud

Gamma Cloud is optional: a Gamma server runs without it. It adds:

| Part | What it is |
|---|---|
| `account.gammapdf.com` | one account for every Gamma server: sign-in, plans, and your settings profile. It holds no notes and no files |
| `app.gammapdf.com` | a shared Gamma server where online libraries and published pages live |
| `<username>-user.gammapdf.com` | a Gamma server of your own, on the Pro plan |
| `<username>-pages.gammapdf.com` | your published pages |

- **A Gamma Cloud sign-in is a sign-in to your server.** The server checks
  the token itself and makes the same session a password would. It never
  asks Gamma Cloud before answering a request; it checks the sign-in is
  still valid once an hour, and syncs your settings profile when it
  changes.
- **The server's administrator decides who gets in**: only accounts
  already linked, or also new people, or only those invited. An existing
  account is linked to a cloud sign-in of the same username only where the
  administrator chose that; otherwise its owner links it with its
  password, since a name alone would hand the account to whoever holds
  that name in the cloud.
- **One address for everyone.** People are told `app.gammapdf.com`; at
  sign-in a Pro owner is sent on to their own server. Each server keeps a
  host name of its own, which also keeps one customer's pages apart from
  another's in the browser.
- The operator sees counts, sizes and recent log lines of a hosted server,
  never its content.

The plans are on [gammapdf.com/pricing](https://gammapdf.com/pricing).

## Upgrades

- **A snapshot comes first.** Before an upgrade changes stored data, the
  server takes a snapshot of it. An upgrade cut short resumes at the step
  that did not finish.
- **Refuse rather than guess.** Data written by a newer Gamma, data too
  old to upgrade directly, or a failed upgrade is not served. The server
  shows one page instead, saying the data is intact and what to do:
  update, or restore the snapshot taken before.
- Each workspace is upgraded when it is first opened; one that fails is
  refused while the others are served.
- An offline copy and its origin pause while their data formats differ,
  so two versions never write into each other.

## Privacy

There is no telemetry and no analytics. Your data stays in the data
directory you chose. Besides a check for a newer release (which a
self-hosted server can turn off), Gamma makes a network request only for
something you use: fetching a paper, your AI provider, translation,
Zotero, Gamma Cloud sign-in. The [privacy policy](../../PRIVACY.md) lists each one, and what
Gamma Cloud keeps.

## Running your own server

- Gamma runs at the root of its own host name, never under a path.
- **A token goes only to the address it was made for.** A request from
  one Gamma to another refuses a redirect, except a file download, which
  follows one to another address without the token. Nothing a server says
  becomes an address or a trust decision without you.
- **Plain http**: a new link (a synced folder, an offline copy) to a public
  host over plain http is refused. Addresses on your own network, Tailscale
  and `.local` names are allowed after you confirm once.
- A NAS with a certificate it signed itself can be trusted by its
  fingerprint, in the desktop app; a renewed certificate asks again.
- Moving a server to a new address moves the links that point at it (synced
  folders, offline copies) after a dry run and a confirmation.

## Behind this page

- [Developer guide](../dev/README.md) and [Repository](../dev/repository.md).
- [Desktop](../dev/desktop.md), [iPad](../dev/ipad.md), [Android](../dev/android.md),
  [Extension](../dev/extension.md).
- [Storage and accounts](../dev/user_db.md), [Migrations](../dev/migrations.md).
- [Gamma Cloud](../dev/cloud.md), [Cloud accounts](../dev/cloud_accounts.md).
- [HTTPS for a NAS](../dev/https.md), [Network proxy](../dev/network_proxy.md).
- [Scaling on SQLite](../research/scaling.md), [iPad web shell](../research/ipad-web-shell.md).
