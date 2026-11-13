"""A block's document: the file a page shows, kept as one property, ``doc``
(docs/dev/documents.md). ``formats`` says what a file is by its name (and,
for a name it does not know, its first bytes) and how a stored one is
served; ``model`` builds, checks and reads a stored ``doc``; ``text``
reads a document's text by its format, for the search index and the chat;
``text_anchors`` places a text file's highlight ranges again when the file
changes (docs/dev/highlight_relocation.md "Text files")."""
