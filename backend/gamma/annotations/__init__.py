"""Annotations: the highlights and comment threads of a page, a folder or
the library, read as entries an agent can act on (docs/dev/annotations.md).

A thread opened on a page records where it was opened (``anchors``: the
passage, area, handwriting, note or whole file, measured on the file then
shown). The reader turns the threads of a scope (a work folder, a directory
or a file of one, a library page, a library folder, the library) and its
highlights that have no thread into entries with their file, place, source
line and, for a thread, comments (``entries``, ``markdown``), with a
picture of each region (``pictures``); SyncTeX maps a place to its source
(``synctex``, ``sources``); the version a thread was opened on stays stored
while it is open (``kept``); a person's Send asks an agent to act on a work
folder's threads (``requests``, until step 3.7 of
docs/research/agents-as-participants-plan.md); the files below a work
folder's ``.gamma/annotations/``, and beside a file a vault follows, carry
it all to the agent on the disk (``files``), kept current by the vaults'
rounds and the comment and op hooks (``listeners``); and an agent's hand
writes comments, replies, resolves and highlights as the agent (``hand``)
on the places it names (``places``).
"""
