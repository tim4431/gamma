"""Agent tools for the AI chat — a scope-agnostic tool registry
(docs/dev/ai_tools.md).

Every chat has a *scope* deciding what its tools can touch:

- ``{"type": "folder", "folder": id}`` — the home/folder chat; tools reach
  the pages filed in that folder or below it ("" = the whole library).
  Builders that write the prompt add ``folder_path``, the folder's path as
  it reads (``prompt.agent_system``).
- ``{"type": "page", "page_id": id}`` — the per-page chat; tools reach only
  that page, and its reading tools the pages inside it too (``inside``,
  which ``registry.run_agent_tool`` sets for a reading tool).

``context_pages`` extends either scope for reads; mutations keep the base scope.

Reads: list the pages and the folder tree (folder scope only); read a page,
its note outline, the AI chat kept with it, its highlights and comment
threads; look at a PDF page or the user's handwriting as a picture; the
citation records kept with pages; full-text-search the reachable pages;
search the scholarly record and the web, and read a paper or a web page
that is not in the library (read-only, nothing stored); list Recently
deleted. Writes: rename pages, file them into (sub)folders, label them,
move them to Recently deleted and restore deleted ones — each of those
tools takes a list of pages, one card and one step for the lot, and the
user can revert each call; rename folders, make and rename labels, and
delete a folder or a label — never its pages, and the user can revert it
(folder scope only); edit, create, move and delete note blocks (a deletion
the user can revert); comment, resolve and highlight; draw on pages; make,
show and export pages; save a paper to the library. Deliberately NOT
offered under any permission: deleting pages for good or files, moving
folders, deleting a shared folder or the chat's own folder, or touching
pages outside the scope.

Folders and labels are blocks of the ``folders`` and ``labels`` trees
(``blocks_store``; a folder may be empty), and a page is filed by their ids
(``properties.folders`` / ``properties.labels``). The model never sees the
filing ids alone: a folder reads as its path — the names from the top joined
with ``PATH_SEP``, "Physics / QEC" — with its id beside it where it may pass
one back, and a label as its name.

Each family's module holds its tools' executors and their TOOLS entries:

- ``reading``: list_pages, list_folders, read_page, read_block, read_chats.
- ``annotations``: list_annotations, read_annotation.
- ``viewing``: view_pdf_page, view_ink, view_image.
- ``library``: cite, search_library.
- ``web``: search_papers, related_papers, search_web, fetch_paper,
  fetch_url, read_paper, save_paper.
- ``organizing``: rename_page, move_page, list_deleted, restore_page,
  delete_page, and the machinery of a call that names several pages.
- ``folders``: rename_folder, create_label, rename_label, label_page,
  delete_folder, delete_label.
- ``notes``: edit_block, create_block, move_block, delete_block,
  clip_region.
- ``commenting``: comment, resolve, highlight.
- ``drawing``: draw, add_text_box, place_picture, add_sheet.
- ``pages``: show_page, create_page, export, backlinks.

``base`` is what they share (the scope, the limits, links, the read window,
the writer, the diff); ``registry`` joins the entries and dispatches;
``prompt`` is what the model is told. Imports run one way: base, the
families, the registry, the prompt (``web`` reaches the registry inside
the functions that need it).

Each executor returns ``(result_text, action)``: ``result_text`` goes back
to the model; ``action`` is the ``{kind, summary}`` event streamed to the
UI and saved with the chat message, for every successful call (reads
included); errors carry None. A change also names what changed, so the chat
can list it without parsing the summary: rename/move_page carry ``title``
(the page's title before the call) and ``from`` / ``to`` (old and new title,
the old folder paths and the new one as they read — "" is the library
root); the note tools carry ``title`` (their page's title); the folder and
label tools carry ``title`` (the folder's path, the label's name, or the
labelled page's title), ``from`` / ``to`` on a rename, ``added`` /
``removed`` on label_page, and ``count`` (pages refiled), ``chats`` (a
folder's) and ``revert`` on a deletion. A call that names several pages
(rename_page, move_page, label_page, restore_page) carries ``changes`` (the
pages it changed, which the message's change limit counts) and ``items``
(each page as the one-page action names it) instead of one page's fields;
rename_page, move_page and label_page carry ``revert`` (``{pages: [[page id,
field, before, after], …]}``), for one page or many.

Nothing is imported here: callers import what they use from its module.
"""
