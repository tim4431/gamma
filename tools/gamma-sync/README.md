# gamma-sync

Keep a folder of your Gamma library as a folder on your disk: each paper's
PDF beside a Markdown file of its notes, the subfolders as subdirectories,
kept up to date from Gamma. One way, Gamma to disk: what you change on
disk is never sent back, and never overwritten either.

`gamma_sync.py` is one file and needs only Python 3.10 or newer. Copy it
anywhere; nothing else of Gamma has to be installed on the computer.

## Setup

1. In Gamma, open **Settings → Integrations** and create a token. Read
   access is enough.
2. See the folders:

   ```sh
   python gamma_sync.py folders --server https://gamma.example.com --token gamma_…
   ```

3. Link a directory to a folder (made if missing), then sync:

   ```sh
   python gamma_sync.py init ~/Papers/Quantum --server https://gamma.example.com \
                             --folder "Papers / Quantum" --token gamma_… --save-token
   python gamma_sync.py sync ~/Papers/Quantum
   ```

   `--folder root` takes the whole library. `--no-notes` writes the PDFs
   only. Without `--save-token`, pass `--token` each time or set
   `GAMMA_TOKEN`; a saved token is readable by anyone with the directory.

4. Keep it running, or put the plain `sync` in a scheduler:

   ```sh
   python gamma_sync.py sync ~/Papers/Quantum --watch 60
   ```

## What you get

```text
~/Papers/Quantum/
  .gamma-sync.json          the link and what was written
  Sub/                      a folder below the linked one
    A paper.pdf             the paper, the file Gamma stores
    A paper.md              its notes and highlights
  A note.md                 a page without a PDF
  attachments/<hash>.png    the pictures the notes show
```

The notes are Obsidian's dialect, so the directory opens as a vault:
`[[Title]]` links between the folder's pages, highlights as quote callouts
that link the PDF's page, labels as tags, and `gamma_id` in the front
matter naming the page. Renaming a page or a folder in Gamma renames the
files on disk; deleting a page removes them.

## Rules

- The client only writes, renames or removes files it wrote itself. A
  file it finds in the way is left alone and reported.
- A file you changed on disk is kept and reported until you run with
  `--force`. `--full` writes every file again. `--dry-run` only tells.
- Several links to several directories work side by side; one directory
  holds one link.

Details for developers: [docs/dev/folder_sync.md](../../docs/dev/folder_sync.md).
