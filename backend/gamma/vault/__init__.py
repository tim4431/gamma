"""The synced vault engine (docs/dev/synced_vaults.md): a folder of Gamma and
a directory on this computer kept in step, the disk's changes read first
(push), Gamma's written after (pull). ``sync`` runs a round (``sync``,
``sync_page``); ``round`` holds one round's state and what its parts share
(``Round``); ``journal`` settles what a round that died left; ``push`` reads
the disk's changes against the rows and applies them, ``pages`` taking each
changed and new file into its page; ``pull`` writes Gamma's changes onto the
disk; ``remote`` what a round reads and writes Gamma through and what both
remotes share; ``local_remote`` this server's workspace in-process
(``LocalRemote``), ``http_remote`` a folder on another Gamma server
(``HttpRemote``); ``kinds`` the class registry, what a file is to a
vault by its name and what a round does with each class; ``disk`` the
directory as files; ``store`` vaults.db's rows; ``merge`` the three-way merge
of a note; ``links`` links and highlights between the file's text and
Gamma's; ``upgrade`` a folder kept on disk before synced vaults.

Nothing is imported here: gamma/folder_links.py imports the engine inside
its functions to break a cycle, and a package that imported its modules at
load would bring it back.
"""
