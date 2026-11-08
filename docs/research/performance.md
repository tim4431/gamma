# Speed and memory after the PDF-loading work: a survey, and what was built

October 2026. The question: once a cold PDF open paints in half a second
([pdf-loading.md](pdf-loading.md)), where do speed and memory still go?
Two read-only surveys, one over the frontend and one over the backend, each
confirmed in the code afterwards; nothing was profiled in a browser, and
the one measurement run was an in-memory SQLite benchmark. The mechanics
of what was built live in the dev docs named below; this note keeps the
ranking and the reasoning.

## What was already tight

The PDF open path (manifest-first skeleton, range transport above 12 MB
with a quiet IndexedDB backfill, two parsed documents kept, one shared
worker, precompressed hashed assets, lazy chunks for Settings, chat, guide
and pdf.js) and the server's base (per-thread SQLite connection cache, WAL
and pragmas, orjson for the tree, text merges outside the write lock,
bounded in-process caches, `FileResponse` with ranges, streamed part
uploads). None of it was touched.

## Client findings, ranked by what a reader feels

1. **Every keystroke copied the whole block tree and re-rendered every
   row.** `updateBlockTree` mapped every node and JSON-cloned the edited
   block's children; App then ran about eight full-tree walks per key
   (flatten, sheets, highlights twice, two stringify passes, diff,
   classify), and `BlockRow` is not memoized, so every row rendered again.
   Tens of milliseconds per key on a page of a thousand blocks.
2. **A long reading session had no memory bound.** pdf.js's page cleanup
   was never called, so every painted page kept its operator list and
   decoded images in the main thread (a scanned page's bitmap is tens of
   MB); text layers were never torn down; in editable mode every page
   mounted an ink layer and a text-box layer with their own window and
   document listeners (about 1,200 for a 300-page book).
3. **Zoom and scroll re-rendered more than they needed to.** The scale is
   a prop of every page, so a zoom frame re-renders all of them; the scroll
   tracker measured pages in order every frame and set App state at each
   page boundary; after each scroll settle, every cover snapshot was
   serialized to localStorage synchronously.
4. **In-document search re-extracted every page per query**, with no cache
   and no cancellation; on a range-opened book the first search downloads
   the whole file.
5. **The IndexedDB cache was capped by count, not bytes**: thirty entries
   of any size, the backfill fetched whatever the file weighed, and the put
   copied the buffer once more.
6. Smaller: the App chunk was fetched one round trip late (the locale
   catalog loads first, then imports App); typing in a highlight's note
   re-rendered every page with a highlight (the per-page grouping rebuilt
   every array); the task poller set fresh state every 30 s when nothing
   changed; the 12 MB whole-file threshold means a 10 MB paper downloads
   whole before anything paints.

## Server findings, ranked

1. **The AI chat re-extracted the PDF with pdfium on every turn**, under
   the process-wide pdfium lock; a turn with a selection extracted the
   whole document, and each `read_page` window re-extracted from its start
   page. Seconds on a long book, and in the way of the viewer's manifest
   walks and the indexer.
2. **The page subtree query computed `kind` for every row**: a virtual
   column with up to six JSON functions, selected inside the recursive
   CTE, which also copies each row once per level. On an in-memory
   database, 5,000 highlight-like blocks of 1.3 KB each:

   | Query | Time |
   |---|---|
   | The recursive CTE selecting `kind` | 42 ms |
   | The CTE without `kind` | 28 ms |
   | An ids-only CTE, then a join | 16 ms |
   | `WHERE page_id = ?`, no `kind` | 8 ms |

3. **No index on `updated_at`**, yet three queries sort by it descending,
   including the `[[` picker on each keystroke.
4. **JSON encoded on the event loop** for a conversation, the op-log
   catch-up, the covers and the searches (FastAPI's default encoder on
   dict returns).
5. **Whole files in memory**: an upload read whole then hashed; the proxy's
   save path joining its chunks into a second copy with no upstream size
   cap; the annotated-PDF export cloning the document in PyPDF2 inside the
   request. The hosted containers run in 768 MB.
6. **Walks over every workspace**: startup opens every workspace, the trash
   sweep opens every `pages.db` hourly, upload GC scans everything every
   6 h, each mirror round reads every block.
7. **Server page rendering encoded PNG in pure Python under the pdfium
   lock** (Pillow is not a dependency).

## What was built (October 2026)

Client ([dev/pdf_loading.md](../dev/pdf_loading.md),
[dev/tasks.md](../dev/tasks.md)):

- `updateBlockTree` copies only the path to the edited block and hands
  back the same array on a miss (`tests/blockTree.test.mjs`), and the rows
  are memoized on it: App hands them one stable wrapper per callback name
  (`makeStableActions`, `shared/lib/stableActions.js`), the page's blocks through `lookupBlock` / `getTree`
  rather than as props, each row's own `[[ref]]` labels resolved once per
  tree change (`editor/refLabels.js`, `tests/refLabels.test.mjs`), and the
  derived maps every row takes (sheet numbers, highlight colours) kept by
  identity while they read the same. A keystroke renders App and the
  edited row; the derived walks App itself runs per keystroke stay.
- A page outside the 900 px look-ahead calls `page.cleanup()`; a page past
  four viewport heights drops its text layer; the ink and text-box layers
  mount only near the view unless the page carries marks.
- The scroll tracker finds the page by binary search and sets App state
  once the scroll rests; the covers' localStorage copy is written once the
  captures rest.
- Search keeps each page's text per parsed document and answers `stale`
  to a query the panel moved past.
- IndexedDB keeps at most 512 MB, sizes in a store of their own; the
  backfill's buffer is stored without a copy.
- The App chunk is modulepreloaded from the page's head; a highlight
  slice keeps its identity while it reads the same; the poller ignores an
  unchanged listing.

Server ([dev/ai_context.md](../dev/ai_context.md),
[dev/api.md](../dev/api.md), [dev/user_db.md](../dev/user_db.md),
[dev/home_library.md](../dev/home_library.md)):

- The page-text cache in `pdf_text`: per file the pages read so far,
  bounded by files and characters, bypassed by the indexer.
- A page's subtree is read by `page_id`; `kind` is computed in Python.
- `idx_ub_updated`.
- `json_response.OrjsonResponse` for the conversation, the op-log
  catch-up, the covers, both searches and the block search.
- Uploads and the proxy's save spool to disk and hash as they arrive
  (`storage.Spool`, `store_pdf_stream`), with the limits asked every 8 MB.
- The trash sweep remembers each workspace's file stamp and next expiry.
- The page render encodes its image outside the pdfium lock.

## Measured (November 2026)

The timing budgets ([dev/debugging.md](../dev/debugging.md#performance-budgets))
time the pure hot paths on seeded fixtures: a page of 1,000 blocks (400
highlights, 200 notes under them, 100 ink groups, 10 sheets, 30 text boxes,
60 notes with `[[refs]]`), a drawing of 500 strokes, a block of 20,000
characters. The p50s below are from an idle Ryzen 9 9950X3D (Node 22.23,
Python 3.12). Each row is one timed run of the case, so a row of 200 keys
is 200 calls.

| Path | p50 |
|---|--:|
| 200 keys at the last block, every pure walk App runs per key | 295 ms (1.5 ms a key) |
| the highlights memo (`blocksToHighlights` + `JSON.stringify`), x200 | 167 ms |
| `diffTrees`, x200 | 56 ms |
| `refLabelsByBlock`, x200 | 3.5 ms |
| `updateBlockTree`, x200 | 1.3 ms |
| `applyOps` of a one-set batch, x200 | 0.5 ms |
| `applyOps` of 50 top-level inserts, x5 | 9 ms |
| a page open of 500 strokes, every outline built | 51 ms |
| one eraser flush, warm / cold | 0.5 / 6.3 ms |
| `mergeInk` of 500 strokes, x20 | 2 ms |
| 20 PDF pages' search text | 4 ms |
| server: subtree read of the page, in-process / through the API | 3.1 / 7.7 ms |
| server: `commit_ops` of 20 keystrokes / a 50-op batch / a stale set merged | 2.5 / 1.6 / 1.7 ms |
| server: `textmerge.merge`, 50 scattered edits a side / `map_offset` | 8.7 / 3.6 ms |
| server: `ink.merge_ink` / `parse_ink`, 500 strokes | 21 / 20 ms |

What they showed:

- The pure work of a keystroke on that page is about 1.5 ms. The highlights
  memo is half of it, and the largest part of that is `JSON.stringify` of
  the 254 KB highlights list, which only keeps the list's identity.
  `blocksToHighlights` runs twice per key (the memo and `annotatedPage`).
  `diffTrees` is about a fifth.
- The server's `merge_ink` runs inside `_Batch.set`, under the write lock,
  and about two-thirds of its ~20 ms on 500 strokes is `_result`'s pydantic
  `model_dump`/`model_validate`. A text merge is worked out before the lock
  (`_premerge`).
- `inkStore.js` keeps every file it loaded for the life of the tab, each
  with its strokes' outlines: about 37 MB for the 500-stroke drawing. A
  timing case that loaded a fresh file per run ran the heap out at 3.4 GB.
  In the app it is one entry per drawing seen, never let go.
- With every core busy, p95s grew 2 to 3x, so the budgets are about 5x an
  idle p95 and CI only reports them.

## Left open

- **App's own render per keystroke.** The rows no longer re-render, but
  App does, with its derived walks over the tree (flatten, sheets,
  highlights, the diff and the history's classification) and the chat
  dock. Moving that state out of App is the state-owner work of
  [dev/frontend-refactor.md](../dev/frontend-refactor.md).
- **The scale as a CSS variable** on the zoom layer, so a zoom frame
  re-renders no page.
- **The annotated-PDF export as a job** answered with a `FileResponse`,
  instead of PyPDF2's clone and the whole result in the request.
- **The startup walk and the upload GC over every workspace**, and each
  mirror round's read of every block.
- **Pillow in the image**, so a page picture is a JPEG a few times
  smaller than the pure-Python PNG.
- **The whole-file threshold** (12 MB): one run of the e2e timing probe at
  mid sizes would say whether to lower it.
- **Measurements in a browser.** The pure paths are measured (above); a
  browser profile of a 1,000-block page, React's render included, and a
  heap snapshot after reading a long book would put numbers on the two
  client items at the top.
