"""Work folders (docs/dev/work_folders.md): a folder kept by a synced vault
of this computer that pushes everything, whose tracked files are each a
page. ``library`` says which root pages the library lists and which are
library documents; ``marks`` reads the folder tree as the work-folder
rules do (which folders carry a vault's mark, and the work folder a folder
is in); ``filing`` is the op rule, who may change a file's filing, name,
document and life, and the folder tree inside a work folder; ``paths``
turns a file page into its path in its work folder and a path back into
its pages, with the one rule for naming a work folder; ``file_search`` is
the rule quick open finds a file by, its name or a fragment of its path."""
