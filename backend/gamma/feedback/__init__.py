"""Feedback on a work folder's files: the open comment threads on them,
read as requests an agent can act on (docs/dev/feedback.md).

A thread opened on a work folder's file records where it was opened
(``anchors``: the passage, area, handwriting, note or whole file, measured
on the file then shown). The reader turns the threads of a work folder, a
directory or a file into entries with their file, place, source line and
comments (``entries``, ``markdown``), with a picture of each region
(``pictures``); SyncTeX maps a place to its source (``synctex``,
``sources``); the version a thread was opened on stays stored while it is
open (``kept``); a person's Send asks an agent to act (``requests``); the
files below a vault's ``.gamma/feedback/`` carry it all to the agent in the
repository (``files``), kept current by the comment and op hooks
(``listeners``); and an MCP connection allowed to answer feedback replies
and resolves (``agent``).
"""
