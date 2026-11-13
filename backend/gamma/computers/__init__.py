"""A computer's channel to a Gamma server (docs/research/agent-collab.md
"How the server sees a vault"): the desktop app's own server (the host)
keeps one outbound long-poll open to every other server its vaults name,
with a credential of its own (an integration token of the ``computer``
scope, which opens nothing but the channel), and that server shows the
vault's maker, in any window, that the folder or file is kept on that
computer, online or offline since when, and which assistants are there.
Nothing runs over it yet.

- ``registry``: the server's side, on every Gamma server: what each
  computer announced (users.db ``computers``) and whether it is online now
  (in memory), the long-poll's hold, and the computers of a page.
- ``channel``: the computer's side, on the desktop app's own server only:
  its credential for each server (users.db ``computer_links``) and one
  thread per credential that announces this computer and holds the poll.
"""
