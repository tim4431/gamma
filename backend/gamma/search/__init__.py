"""The document text index: the text of the workspace's PDFs and text files,
searchable beside the notes (docs/dev/api.md "Search").

    index    the FTS5 tables in data.db (text_fts*): schema, writes, queries
    indexer  a document's text extracted into it, and the indexing job
    hits     the one builder of the hits the routes and the agent list

The notes index is pages.db's (gamma/block_index.py); the extraction is
gamma/documents/text.py's; the routes are gamma/routers/search.py."""
