// A work folder's directory read from GET /api/folders/{id}/pages, as pure
// rules: the rows an answer gives, how many a read again asks for and in
// how many requests, and, for the Files window (explorer/
// useDirectoryRows.js holds them in React), which of its open directories
// to read now (docs/dev/work_folders.md "The Files window"). The home list
// view's one directory (useFolderRows.js) reads by the same rows rules.
// Pure: tests/directoryRows.test.mjs.

// Rows one read asks for, and the most one request does (the server's
// MAX_FOLDER_PAGES), which is also as far as the reveal pages: a directory
// of 10,000 files is read a page at a time.
export const PAGE_ROWS = 200;
export const MAX_ROWS = 500;

// A directory never read: `loaded` false until its first answer, `count`
// the listing's count of its files the rows were read at, `seq` the change
// log's seq they were read at (the answer's).
export const NONE = Object.freeze({ rows: [], total: 0, next: null, loading: false, failed: false, loaded: false, count: -1, seq: -1 });

// An answer's rows as the listing's: a file is inside no page.
export const listed = (pages) => (pages || []).map((r) => ({ ...r, _parentPage: r.parent || "" }));

// `rows`, then the rows of `more` not among them.
export function joined(rows, more) {
  const ids = new Set(rows.map((r) => r.id));
  return [...rows, ...more.filter((r) => !ids.has(r.id))];
}

// A directory's entry after an answer (`answer`: {pages, total, next,
// count, seq}): its rows in the server's order, the answer's in place of
// the held ones (`replace`, a read from the top, whose `seq` it takes) or
// after them (a later page, which keeps the seq the first rows were read
// at), a row held already kept once.
export function merged(held, answer, replace) {
  const base = held || NONE;
  const rows = listed(answer.pages);
  const kept = replace ? rows : joined(base.rows, rows);
  return { rows: kept, total: answer.total ?? kept.length, next: answer.next ?? null, loading: false, failed: false, loaded: true,
    count: answer.count ?? base.count, seq: replace ? answer.seq ?? base.seq : base.seq };
}

// How many rows a read from the top asks for: as many as are held, one
// page at least, so the list stays where it was.
export const readLimit = (held) => Math.max(PAGE_ROWS, held?.rows.length || 0);

// `limit` rows from `offset`, at most MAX_ROWS a request: `page(offset,
// n)` asks for one and answers it, or null once the read is no longer
// wanted. The answers as one ({pages, total, next} of the last, `seq` of
// the first), null for a read dropped.
export async function readRows(page, offset, limit) {
  let pages = [];
  let got = 0;
  let first = null;
  let last = null;
  do {
    last = await page(offset + got, Math.min(MAX_ROWS, limit - got));
    if (!last) return null;
    first = first || last;
    got += last.pages?.length || 0;
    pages = joined(pages, last.pages || []);
  } while (last.next != null && got < limit && last.pages?.length);
  return { ...last, pages, seq: first.seq };
}

// Whether the directory `id`, its entry `entry`, was written since its
// rows were read, by the listing's `work_changes` (`changes`: {seq, since,
// dirs}): a write to one of its files the log names after the read, or a
// read older than the log still answers for. A file renamed on disk keeps
// the count; this sees it.
export function changedSince(entry, id, changes) {
  if (!changes) return false;
  return entry.seq < (changes.since || 0) || (changes.dirs?.[id] ?? -1) > entry.seq;
}

// The directories of `wanted` (the open ones) to read now, from `held`
// (id → entry): none while `enabled` is false (the window is not shown);
// one never read; one whose count in `counts` (the listing's files right in
// each directory, libTree.workCounts) differs from the count it was read
// at, or that was written since (`changes`, libTree.workChanges:
// changedSince). A listing read again with nothing changed reads nothing,
// and one in flight is not asked again.
export function wantedReads(wanted, held, counts, changes, enabled) {
  if (!enabled) return [];
  return wanted.filter((id) => {
    const entry = held.get(id);
    if (!entry) return true;
    if (entry.loading) return false;
    return (counts?.[id] || 0) !== entry.count || changedSince(entry, id, changes);
  });
}

// The offset to read next while looking for `pageId` among a directory's
// rows (the reveal), null once it is held, the rows end, or MAX_ROWS are
// held.
export function findStep(held, pageId) {
  if (!held?.loaded || held.loading || held.failed) return null;
  if (held.rows.some((r) => r.id === pageId)) return null;
  if (held.next == null || held.rows.length >= MAX_ROWS) return null;
  return held.next;
}
