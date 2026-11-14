// Quick open's Files group, the pure half (useFileSearch.js asks the
// server): the rows GET /api/files answered, as palette rows, and a name
// or a directory cut at the server's spans. The match rule is the server's
// alone (backend/gamma/work_folders/file_search.py): the client draws its
// rows in its order and marks its spans, never ranks.
import { PATH_SEP } from "../library/libraryUtils.js";
import { partsAt } from "../search/snippets.js";

// `hits` (GET /api/files' rows) as palette rows `{key, section: "files",
// file}`, in the server's order, leaving out a file a page row already
// lists (a library file found by its title, a tab's file): one row, the
// page's.
export function fileRows(hits, pageRows) {
  const listed = new Set(pageRows.map((r) => r.page?.id).filter(Boolean));
  return hits.filter((f) => !listed.has(f.id)).map((f) => ({ key: `file:${f.id}`, section: "files", file: f }));
}

// `spans` (half-open [from, to) in code points, sorted and apart, as the
// server counts them) as offsets into the string `text`, those past its
// end cut and the empty dropped.
export function spanRanges(text, spans) {
  const chars = Array.from(text || "");
  const offsets = [0];
  for (const c of chars) offsets.push(offsets.at(-1) + c.length);
  const at = (n) => offsets[Math.max(0, Math.min(chars.length, n))];
  return (spans || []).map(([from, to]) => [at(from), at(to)]).filter(([a, b]) => b > a);
}

// `text` cut at the server's `spans` into the {text, mark} pieces search's
// MarkedText draws (search/snippets.js partsAt).
export const fileParts = (text, spans) => partsAt(text, spanRanges(text, spans));

// A found file's place line, "W / a / b" (explorer/workPaths.js
// filePlace's text), in those pieces: W's name `work`, then its directory
// `dir` ("a/b", the row's) cut at the server's `spans`, each "/" drawn as
// the line's " / ".
export function placeParts(work, dir, spans) {
  const parts = fileParts(dir || "", spans).map((p) => ({ ...p, text: p.text.replaceAll("/", PATH_SEP) }));
  return work ? [{ text: dir ? `${work}${PATH_SEP}` : work, mark: false }, ...parts] : parts;
}
