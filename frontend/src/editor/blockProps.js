// The Properties popover's model (editor/BlockProperties.jsx): what a block
// is and every key it stores, as sections of rows the popover renders
// without working anything out itself. The keys are the catalog of
// docs/research/block-properties.md "The catalog" (CATALOG mirrors it, one
// entry per key, plus the fields of pdf_position, text_box, picture, sheet
// and recording the rows name); a key the catalog does not list goes under
// Other keys, rendered by its value's shape.
//
// A row is a descriptor {id, path, field, key, keyPath, sqlPath, label,
// hint, type, value, copy, copyJson, display, nav?, step?, tags?}: `path`
// the parts under `properties` (or the block dict's field, `field` true),
// `value` the stored value (undefined for a row worked out from defaults),
// `copy` what its copy button copies, `display` what `type` draws (see
// rowDisplay below). The tables (KINDS, CATALOG, FIELDS) hold T() keys;
// everything a function returns is already translated.
// Pure: tests/blockProps.test.mjs.
import { T, fmtDate, t, tn } from "../shared/i18n/i18n.js";
import { blockPosition, closestPlace, getParentInfo, isHighlightBlock, isInkGroup, isLinkBlock, isNum, isRecord, placedToCheck,
  roughMatchPercent, sameJson } from "../shared/model/blockModel.js";
import { DEFAULT_HIGHLIGHT, LINK_COLOR, colorName, hexColor, normalizeColor, parseColor } from "../shared/model/palette.js";
import { isTextBox, normalizeTextBox } from "../markup/textBox.js";
import { isPicture, normalizePicture } from "../markup/picture.js";
import { isLandscape, isSheet, normalizePaper, paperSizeKey, sheetOfBlock, sheetsOf } from "../notebook/notebook.js";
import { heldElsewhere, isRecordingBlock, normalizeRecording } from "../recording/recording.js";
import { citationKeyOf, filingChips, folderPath, formatRelativeTime, parseStamp } from "../library/libraryUtils.js";
import { metaSourceInfo } from "../shared/lib/metaSource.js";
import { itemTypeFields, itemTypeLabel, itemTypeOf } from "../shared/lib/itemTypes.js";
import { fmtClock } from "../replay/clock.js";
import { fmtBytes } from "../shared/lib/format.js";

// A table's own entry for a key: a stored key named like an Object
// member ("constructor", "toString") is no entry.
const ownEntry = (table, key) => (Object.hasOwn(table, key) ? table[key] : null);
// A value as JSON has it: a key holding undefined (App sets some that way)
// is no key.
const asJson = (v) => (v === undefined ? v : JSON.parse(JSON.stringify(v)));
// A link key, a pin or an attachment that is "" or null is unset (older
// tabs and peers still write ""): its row is hidden.
const unset = (v) => v === undefined || v === null || v === "";
// Points as the rows show them: a tenth at most.
const pt = (v) => String(Math.round((Number(v) || 0) * 10) / 10);
const pct = (v) => Math.round(v * 100);

// --- kinds ---------------------------------------------------------------------

// What a block is, as the server's `kind` names it, with its word
// (BlockProperties.jsx's KIND_ICONS draws the kind's tile by the same key).
export const KINDS = {
  highlight: { label: T("Highlight") },
  link: { label: T("Link region") },
  ink: { label: T("Handwriting") },
  text_box: { label: T("Text box") },
  picture: { label: T("Picture") },
  sheet: { label: T("Paper sheet") },
  recording: { label: T("Recording") },
  page: { label: T("Page") },
  folder: { label: T("Folder") },
  label: { label: T("Label") },
  note: { label: T("Note") },
};

// The block's kind by gamma/blocks_store.py block_kind's rule, in its order:
// a page (under root or the trash), a folder or a label (in their trees),
// from the flags or the dict's parent_id / page_id; then ink (an ink_url
// key), text_box, picture, sheet, recording (an object there), link (a
// link_url or link_page_id that is set), highlight (a pdf_position object),
// note. The per-kind predicates each test one step; chained, they are the
// server's rule (tests/shared/blockkind.json pins both).
export function blockKind(b, { isPage = false, isFolder = false, isLabel = false } = {}) {
  if (isPage || b?.parent_id === "root" || b?.parent_id === "trash") return "page";
  if (isFolder || b?.page_id === "folders") return "folder";
  if (isLabel || b?.page_id === "labels") return "label";
  if (isInkGroup(b)) return "ink";
  if (isTextBox(b)) return "text_box";
  if (isPicture(b)) return "picture";
  if (isSheet(b)) return "sheet";
  if (isRecordingBlock(b)) return "recording";
  if (isLinkBlock(b)) return "link";
  // Ink, text box, picture and sheet are ruled out above, so this is the
  // pdf_position object test alone.
  if (isHighlightBlock(b)) return "highlight";
  return "note";
}

// The kind the popover shows: the server's while its properties are this
// tab's, else the one this tab's properties make.
export function effectiveKind(live, server) {
  if (server?.kind && (!live || sameJson(asJson(server.properties), asJson(live.properties)))) return server.kind;
  return blockKind(live || server);
}

// A note attached to a highlight (attach mode stores linked_highlight_id
// and its page as pdf_position {pageNumber}): kind highlight on the server,
// laid out as a note.
const onHighlight = (b, kind) => !unset(b?.properties?.linked_highlight_id) && (kind === "highlight" || kind === "note");

// The kind's word, refined: a note on a highlight, an area highlight.
export function kindLabel(b, kind = blockKind(b)) {
  if (onHighlight(b, kind)) return t("Note on a highlight");
  if (kind === "highlight" && b?.properties?.pdf_position?.area) return t("Area highlight");
  return t(ownEntry(KINDS, kind)?.label || KINDS.note.label);
}

// --- the catalog ---------------------------------------------------------------

// Every key a block may carry (docs/research/block-properties.md "The
// catalog"): the section it shows in, its label, a sentence on what it is,
// the row type its value draws as, `legacy` for a key no writer produces
// any more. Section "kind" is the kind's own section; a kind key on a
// block of another kind (a quote on a note) shows under Technical. Fields
// of an object are listed as "key.field".
export const CATALOG = {
  // highlights and link regions
  color: { section: "kind", type: "color", label: T("Colour"), hint: T("The highlight's colour as stored, #rrggbbaa.") },
  quote: { section: "kind", type: "quote", label: T("Quote"), hint: T("The passage this highlight marks.") },
  pdf_position: { section: "kind", type: "place", label: T("Place"), hint: T("Where this sits on the PDF page, in the frame its page width and height give.") },
  "pdf_position.pageNumber": { section: "kind", type: "number", label: T("Page"), hint: T("The PDF page, counted from 1.") },
  "pdf_position.width": { section: "kind", type: "number", label: T("Page width"), hint: T("The width of the page the place was measured on: the viewer's page at its zoom then (points for handwriting and imported annotations).") },
  "pdf_position.height": { section: "kind", type: "number", label: T("Page height"), hint: T("The height of the page the place was measured on, in the same frame as its width.") },
  "pdf_position.boundingRect": { section: "kind", type: "json", label: T("Frame"), hint: T("The box around every line, from the page's top-left corner, in the frame of its page width and height.") },
  "pdf_position.rects": { section: "kind", type: "json", label: T("Lines"), hint: T("One box per line of the passage, in the frame of its page width and height.") },
  "pdf_position.area": { section: "kind", type: "flag", label: T("Area"), hint: T("An area dragged out on the page rather than selected text.") },
  "pdf_position.doc_id": { section: "kind", type: "mono", label: T("Measured on"), hint: T("The stored PDF the place was measured on, when that is no longer the page's PDF.") },
  "pdf_position.lost": { section: "kind", type: "flag", label: T("Lost"), hint: T("Its text was not found in the replacement PDF.") },
  "pdf_position.approx": { section: "kind", type: "flag", label: T("To check"), hint: T("Placed roughly after the PDF was replaced; it waits to be accepted or moved.") },
  "pdf_position.match": { section: "kind", type: "number", label: T("Match"), hint: T("How alike its text and the text found for it read, 0 to 1.") },
  "pdf_position.prev": { section: "kind", type: "json", label: T("Before replacement"), hint: T("Where it was before the PDF was replaced.") },
  "pdf_position.closest": { section: "kind", type: "json", label: T("Closest match"), hint: T("The best place found for a lost passage, too unlike it to apply.") },
  link_url: { section: "kind", type: "url", label: T("Links to"), hint: T("The web or document address this region opens.") },
  link_page_id: { section: "kind", type: "page", label: T("Links to page"), hint: T("The Gamma page this region opens.") },
  link_block_id: { section: "kind", type: "block", label: T("Lands on"), hint: T("The highlight on the linked page this region scrolls to.") },
  // handwriting
  ink_url: { section: "kind", type: "file", label: T("Stroke file"), hint: T("The uploaded file that holds this group's strokes.") },
  ink_strokes: { section: "kind", type: "number", label: T("Strokes"), hint: T("How many strokes the file holds, saved with the block for quick reads.") },
  // text boxes
  text_box: { section: "kind", type: "text", label: T("Frame"), hint: T("Where the box sits on its page and how large it is, in points.") },
  "text_box.size": { section: "kind", type: "number", label: T("Text size"), hint: T("The font size, in points.") },
  "text_box.color": { section: "kind", type: "color", label: T("Text colour"), hint: T("The colour of the text.") },
  "text_box.bg": { section: "kind", type: "color", label: T("Fill"), hint: T("The box's background colour.") },
  "text_box.auto": { section: "kind", type: "text", label: T("Box width"), hint: T("Auto: the width follows the text. Fixed: the text wraps inside it.") },
  "text_box.zotero": { section: "kind", type: "flag", label: T("Zotero"), hint: T("A Zotero text annotation, laid out as Zotero's reader lays it out.") },
  // pictures
  picture: { section: "kind", type: "text", label: T("Frame"), hint: T("Where the picture sits on its page and how large it is, in points.") },
  "picture.url": { section: "kind", type: "file", label: T("File"), hint: T("The uploaded image.") },
  "picture.angle": { section: "kind", type: "text", label: T("Rotation"), hint: T("The turn about the frame's centre, clockwise.") },
  "picture.crop": { section: "kind", type: "text", label: T("Crop"), hint: T("The part of the image shown, as a share of its width and height.") },
  "picture.flip_h": { section: "kind", type: "text", label: T("Flip"), hint: T("Whether the image is mirrored.") },
  "picture.opacity": { section: "kind", type: "text", label: T("Opacity"), hint: T("How strongly the image shows over what lies under it.") },
  "picture.z": { section: "kind", type: "number", label: T("Layer"), hint: T("Its place in the stack of pictures on its page.") },
  "picture.locked": { section: "kind", type: "flag", label: T("Locked"), hint: T("The page neither moves nor edits it.") },
  pdf_page: { section: "timing", type: "number", label: T("On page"), hint: T("The PDF page a text box or picture sits on.") },
  // sheets
  sheet: { section: "kind", type: "text", label: T("Size"), hint: T("The paper's size, in points.") },
  "sheet.color": { section: "kind", type: "color", label: T("Paper colour"), hint: T("The colour of the paper.") },
  "sheet.pattern": { section: "kind", type: "text", label: T("Pattern"), hint: T("The lines printed on the paper, and how far apart.") },
  "sheet.line": { section: "kind", type: "color", label: T("Line colour"), hint: T("The colour of the printed lines.") },
  // recordings
  recording: { section: "kind", type: "json", label: T("Recording"), hint: T("The audio: when it began, its parts, and which tab holds the microphone.") },
  "recording.state": { section: "kind", type: "flag", label: T("State"), hint: T("Recording, paused, or done.") },
  "recording.started": { section: "kind", type: "time", label: T("Started"), hint: T("When the recording began.") },
  "recording.segments": { section: "kind", type: "text", label: T("Length"), hint: T("The parts' lengths added up.") },
  "recording.dur": { section: "technical", type: "number", label: T("Stored length"), hint: T("The parts' total as last saved, in milliseconds; the app adds the parts up itself.") },
  "recording.by": { section: "technical", type: "mono", label: T("Held by tab"), hint: T("The tab recording it now; a tab, not a person.") },
  "recording.at": { section: "technical", type: "time", label: T("Lease renewed"), hint: T("When that tab last wrote the recording; a lease gone stale frees it.") },
  // timing and display
  t0: { section: "timing", type: "time", label: T("Written"), hint: T("When this was written; replay and recordings line notes up by it.") },
  transcript: { section: "timing", type: "flag", label: T("Transcript"), hint: T("Written by a recording's transcription.") },
  collapsed: { section: "timing", type: "flag", label: T("Folded"), hint: T("Whether the page opens with this block folded; your own folding may differ.") },
  linked_highlight_id: { section: "timing", type: "block", label: T("Attached to"), hint: T("The highlight on the PDF this note is attached to.") },
  zotero_readonly: { section: "timing", type: "flag", label: T("Zotero"), hint: T("A Zotero link does not write this block back.") },
  zotero_author: { section: "timing", type: "text", label: T("Zotero author"), hint: T("The group member whose Zotero annotation this is.") },
  // the attachment
  original_filename: { section: "document", type: "text", label: T("File name"), hint: T("The PDF's name when it was added.") },
  doc_id: { section: "document", type: "mono", label: T("Stored copy"), hint: T("The stored PDF's content hash.") },
  source_url: { section: "document", type: "url", label: T("Source address"), hint: T("Where the PDF is read from, when that is not the stored copy.") },
  // the paper record
  meta: { section: "paper", type: "json", label: T("Paper record"), hint: T("The bibliographic record: title, authors, venue, identifiers.") },
  "meta.title": { section: "paper", type: "text", label: T("Title"), hint: T("The work's title.") },
  "meta.authors": { section: "paper", type: "text", label: T("Authors"), hint: T("The work's authors, in order.") },
  "meta.kind": { section: "paper", type: "text", label: T("Type"), hint: T("The kind of work, under Zotero's names.") },
  "meta.doi": { section: "paper", type: "url", label: T("DOI"), hint: T("The work's DOI.") },
  "meta.arxiv_id": { section: "paper", type: "url", label: T("arXiv"), hint: T("The work's arXiv id.") },
  "meta.source": { section: "paper", type: "text", label: T("Source"), hint: T("Where the record came from and what tied it to this PDF.") },
  "meta.at": { section: "paper", type: "time", label: T("Fetched"), hint: T("When the record was fetched or last edited.") },
  "meta.abstract": { section: "paper", type: "text", label: T("Abstract"), hint: T("The work's abstract.") },
  cite_key: { section: "paper", type: "mono", label: T("Cite key"), hint: T("A cite key pinned by hand; without one the BibTeX entry's own key is used.") },
  bibtex: { section: "paper", type: "code", label: T("BibTeX"), hint: T("The BibTeX entry of record, kept as written, not rebuilt from the fields.") },
  ppt_cite: { section: "paper", type: "code", label: T("Slide citation"), hint: T("The short citation for slides.") },
  meta_error: { section: "technical", type: "json", label: T("Last lookup"), hint: T("Why the last metadata lookup failed, and when.") },
  auto_title: { section: "technical", type: "text", label: T("Automatic title"), hint: T("The title the page had when metadata set it; the title follows new metadata while it still reads this.") },
  // filing
  folders: { section: "filing", type: "folders", label: T("Folders"), hint: T("The folders this page is filed in.") },
  labels: { section: "filing", type: "labels", label: T("Labels"), hint: T("The labels this page carries.") },
  parent_page: { section: "filing", type: "page", label: T("Inside page"), hint: T("The page this page sits inside; it is listed there, not in the library.") },
  pinned: { section: "filing", type: "time", label: T("Pinned"), hint: T("When it was pinned to the top of the library.") },
  // in Recently deleted
  deleted_at: { section: "deleted", type: "time", label: T("Deleted"), hint: T("When it was moved to Recently deleted.") },
  deleted_by: { section: "deleted", type: "mono", label: T("By"), hint: T("The account that deleted it; the app cannot name an account from its id.") },
  deleted_with: { section: "deleted", type: "page", label: T("Went with"), hint: T("It was deleted with this page; restoring that page brings it back.") },
  // where it came from
  imported_annot: { section: "imported", type: "mono", label: T("PDF annotation"), hint: T("The PDF's own annotation this came from.") },
  annot_stripped: { section: "imported", type: "flag", label: T("Annotation removed"), hint: T("The PDF it came from was replaced, or its annotation stripped.") },
  web_url: { section: "imported", type: "url", label: T("Clipped from"), hint: T("The web page this was saved from.") },
  web_meta: { section: "imported", type: "json", label: T("Page's citation tags"), hint: T("The citation tags the saved web page carried.") },
  web_clips: { section: "imported", type: "flag", label: T("Web clips page"), hint: T("The page web clips are saved to.") },
  markdown_import: { section: "imported", type: "mono", label: T("Markdown file"), hint: T("The imported Markdown file's hash, so a re-import finds this page.") },
  notion_id: { section: "imported", type: "mono", label: T("Notion page"), hint: T("The Notion page this was imported from.") },
  aliases: { section: "imported", type: "text", label: T("Aliases"), hint: T("Other names from the imported file's front matter.") },
  notability_import: { section: "imported", type: "mono", label: T("Notability import"), hint: T("The key a Notability re-import finds this page by.") },
  goodnotes_import: { section: "imported", type: "mono", label: T("GoodNotes import"), hint: T("The key a GoodNotes re-import finds this page by.") },
  seeded: { section: "imported", type: "text", label: T("Starter page"), hint: T("A page every new account starts with.") },
  // Zotero
  zotero_key: { section: "zotero", type: "url", label: T("Zotero item"), hint: T("The Zotero item this page is linked to.") },
  zotero_note: { section: "zotero", type: "mono", label: T("Zotero note"), hint: T("The Zotero note this block or page is linked to.") },
  zotero_pdf: { section: "zotero", type: "json", label: T("PDF in Zotero"), hint: T("The item's PDF in Zotero and whether it is here yet.") },
  zotero_annotation: { section: "zotero", type: "mono", label: T("Zotero annotation"), hint: T("The Zotero annotation this block is linked to.") },
  zotero_attachments: { section: "zotero", type: "flag", label: T("Attachments heading"), hint: T("The heading Zotero's attachments are listed under.") },
  zotero_attachment: { section: "zotero", type: "mono", label: T("Zotero attachment"), hint: T("The Zotero attachment this block is linked to.") },
  zotero_rdf: { section: "zotero", type: "json", label: T("Zotero fields"), hint: T("Zotero fields Gamma has no place for, kept for export.") },
  // old keys a row may still carry: only the agent's page header reads summary, nothing reads guide_demo
  summary: { section: "technical", legacy: true, label: T("Summary"), hint: T("A page summary typed before July 2026; only the AI's page header reads it.") },
  guide_demo: { section: "technical", legacy: true, label: T("Tour marker"), hint: T("The first-run tour's old marker; nothing reads it.") },
};

// The block dict's own fields (GET /api/blocks/{id}), and the rows the
// tree adds: their labels and what they are.
export const FIELDS = {
  kind: { label: T("Kind"), hint: T("What the block is, worked out from which keys it carries.") },
  id: { label: T("ID"), hint: T("The block's permanent id; links and [[refs]] use it.") },
  page_id: { label: T("Page"), hint: T("The page this block belongs to.") },
  parent_id: { label: T("Parent"), hint: T("What this block is nested under.") },
  order: { label: T("Order"), hint: T("Its place among its siblings, and how deep it is nested.") },
  created_at: { label: T("Created"), hint: T("When the server first saved it.") },
  updated_at: { label: T("Last edited"), hint: T("When the server last saved a change to it.") },
  content: { label: T("Content"), hint: T("The block's text, in Markdown.") },
  position: { label: T("Order key"), hint: T("The key that sorts it among its siblings.") },
};
// The block table's columns the dict fields are (pages.db unified_blocks):
// every field but `order`, which the tree adds.
const COLUMNS = new Set(Object.keys(FIELDS).filter((k) => k !== "order"));

// --- paths ----------------------------------------------------------------------

const IDENT = /^[A-Za-z_$][\w$]*$/;

// A path under `properties` as JavaScript spells it: properties.a.b[0],
// properties["odd key"]. With root "" the path alone (a.b[0]).
export function keyPath(parts, root = "properties") {
  let out = root;
  for (const p of parts) {
    if (typeof p === "number") out += `[${p}]`;
    else if (IDENT.test(p)) out += `${out ? "." : ""}${p}`;
    else out += `[${JSON.stringify(p)}]`;
  }
  return out;
}

// The same path as SQLite reads it from pages.db: json_extract(properties,
// '$.a.b[0]'), an odd key quoted ($."odd key"), the quote of the SQL string
// doubled.
export function sqlPath(parts) {
  let path = "$";
  for (const p of parts) {
    if (typeof p === "number") path += `[${p}]`;
    else path += IDENT.test(p) ? `.${p}` : `."${p}"`;
  }
  return `json_extract(properties, '${path.replace(/'/g, "''")}')`; // i18n-ignore: SQL
}

// A long hash shortened for a row (a1b2c3…f9e8); shorter values as they are.
export function shortHash(s) {
  const v = String(s ?? "");
  return v.length > 16 && !/\s/.test(v) ? `${v.slice(0, 6)}…${v.slice(-4)}` : v;
}

// A value as the JSON view and the copy buttons write it.
export function jsonText(v) {
  return v === undefined ? "" : JSON.stringify(v, null, 2);
}

// The block dict's fields in the API's order, children left out.
export function wholeBlock(dict) {
  const d = dict || {};
  return { id: d.id ?? null, kind: d.kind ?? null, parent_id: d.parent_id ?? null, page_id: d.page_id ?? null,
    position: d.position ?? null, created_at: d.created_at ?? null, updated_at: d.updated_at ?? null,
    content: d.content ?? null, properties: d.properties ?? null };
}

// The JSON view's sources: this tab's properties, the whole block as this
// tab has it (the server's dict with this tab's text and properties), and
// the server's dict when it differs from that (else null), all as JSON has
// them.
export function jsonSources(live, server) {
  const merged = server ? { ...server, content: live?.content ?? server.content, properties: live?.properties ?? server.properties }
    : live || {};
  const block = asJson(wholeBlock(merged));
  const theirs = server ? asJson(wholeBlock(server)) : null;
  return { properties: block.properties ?? {}, block, server: theirs && !sameJson(theirs, block) ? theirs : null };
}

// The keys whose values differ between two blocks: their properties' keys,
// and "content" when their text does; [] when either is missing.
export function changedKeys(a, b) {
  if (!a || !b) return [];
  const pa = isRecord(a.properties) ? a.properties : {}, pb = isRecord(b.properties) ? b.properties : {};
  const keys = [...new Set([...Object.keys(pa), ...Object.keys(pb)])].filter((k) => !sameJson(pa[k], pb[k])).sort();
  if ((a.content ?? "") !== (b.content ?? "")) keys.push("content");
  return keys;
}

// --- values -----------------------------------------------------------------------

// A moment as a row shows it: {iso, date, relative, text, title}, from
// milliseconds (t0, a recording's moments) or a stored stamp (a bare one
// is UTC); null for what is no moment.
export function timeDisplay(v, now = Date.now()) {
  const d = isNum(v) ? new Date(v) : typeof v === "string" && v.trim() ? parseStamp(v.trim()) : null;
  if (!d || Number.isNaN(d.getTime())) return null;
  const iso = d.toISOString();
  const date = fmtDate(d, { dateStyle: "medium", timeStyle: "short" });
  const relative = formatRelativeTime(iso, now);
  return { iso, date, relative, text: `${date} · ${relative}`, title: iso };
}

const DOI = /^10\.\d{4,9}\/\S+$/;
const ARXIV = /^(?:arxiv:)?(\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+(?:\.[a-z]{2})?\/\d{7}(?:v\d+)?)$/i;

// An address as a row shows it: {url, host, path}; a DOI or an arXiv id
// becomes its address. A value that is no web address keeps no host.
export function urlDisplay(v) {
  let url = String(v ?? "").trim();
  if (DOI.test(url)) url = `https://doi.org/${url}`;
  else if (ARXIV.test(url)) url = `https://arxiv.org/abs/${ARXIV.exec(url)[1]}`;
  if (/^https?:\/\//i.test(url)) {
    try {
      const u = new URL(url);
      return { url, host: u.host, path: `${u.pathname === "/" ? "" : u.pathname}${u.search}${u.hash}`, text: url };
    } catch { /* not an address: shown as it is */ }
  }
  return { url, host: "", path: url, text: url };
}

// A colour as a row shows it: {css, hex, alpha, name, text}. A missing or
// unreadable one reads `none` (by default the default highlight, drawn in
// its colour); LINK_COLOR reads "Link colour".
export function colorSummary(c, { none = null } = {}) {
  const stored = normalizeColor(c);
  if (!stored) {
    return none !== null ? { css: null, hex: null, alpha: null, name: null, text: none, muted: true }
      : { css: DEFAULT_HIGHLIGHT, hex: hexColor(DEFAULT_HIGHLIGHT), alpha: parseColor(DEFAULT_HIGHLIGHT)[3], name: t("Yellow"),
        text: t("Default (yellow)"), muted: true };
  }
  const hex = hexColor(stored), alpha = parseColor(stored)[3], name = t(colorName(stored));
  if (stored === LINK_COLOR) return { css: stored, hex, alpha, name, text: t("Link colour") };
  return { css: stored, hex, alpha, name, text: [name, hex, ...(alpha < 1 ? [`${pct(alpha)}%`] : [])].join(" · ") };
}

const placedRect = (r) => isRecord(r) && ["x1", "y1", "x2", "y2"].every((k) => isNum(r[k]));
const boxText = (x, y, w, h) => t("{w}×{h} pt at ({x}, {y})", { w: pt(w), h: pt(h), x: pt(x), y: pt(y) });
// A highlight's or a link region's box is in the frame of the page as the
// viewer drew it when the place was taken (its zoom then), not in points:
// it reads as a share of the page, or bare numbers with no frame.
const share = (v, of) => String(Math.round((v / of) * 1000) / 10);
function placeBoxText(b, frame) {
  const w = b.x2 - b.x1, h = b.y2 - b.y1;
  if (!frame) return t("{w}×{h} at ({x}, {y})", { w: pt(w), h: pt(h), x: pt(b.x1), y: pt(b.y1) });
  return t("{w}%×{h}% of the page at ({x}%, {y}%)", { w: share(w, frame.width), h: share(h, frame.height),
    x: share(b.x1, frame.width), y: share(b.y1, frame.height) });
}

// A highlight's place as its row shows it: {page, text (the summary), box,
// frame {width, height}, rects (for the mini-map), area, dashed, hollow,
// chips [{id, text, tone}], prev, closest}. `currentDocId` is the page's
// PDF (attachmentFile of its attachment), to tell a place measured on an
// older one; undefined when the block is not on the open page.
export function placeSummary(pos, { currentDocId } = {}) {
  const p = isRecord(pos) ? pos : {};
  const page = p.pageNumber ?? null;
  const rects = Array.isArray(p.rects) ? p.rects.filter(placedRect) : [];
  const b = placedRect(p.boundingRect) ? p.boundingRect : null;
  const text = !b ? t("Page {n} only (no exact place)", { n: page ?? "?" })
    : p.area ? t("Page {n} · area", { n: page ?? "?" })
      : `${t("Page {n}", { n: page ?? "?" })} · ${tn("{n} line", "{n} lines", Math.max(1, rects.length))}`;
  const chips = [];
  if (placedToCheck(p)) {
    const match = roughMatchPercent(p);
    chips.push({ id: "check", tone: "warn", text: match === null ? t("To check") : t("To check · {pct}% match", { pct: match }) });
  }
  if (p.lost) chips.push({ id: "lost", tone: "failed", text: t("Lost") });
  if (currentDocId !== undefined && p.doc_id && p.doc_id !== currentDocId) {
    chips.push({ id: "older", tone: "warn", text: t("Measured on an older PDF") });
  }
  const close = closestPlace(p);
  const frame = isNum(p.width) && isNum(p.height) && p.width > 0 && p.height > 0 ? { width: p.width, height: p.height } : null;
  return {
    page, text, area: Boolean(p.area),
    box: b ? placeBoxText(b, frame) : null,
    frame,
    rects: rects.map(({ x1, y1, x2, y2 }) => ({ x1, y1, x2, y2 })),
    dashed: placedToCheck(p), hollow: Boolean(p.lost), chips,
    prev: isRecord(p.prev) && p.prev.pageNumber != null ? t("Before the PDF was replaced: page {n}", { n: p.prev.pageNumber }) : null,
    closest: close ? t("Closest match: page {n}, {pct}%", { n: close.position.pageNumber, pct: pct(close.match) }) : null,
  };
}

// A block's text as one line: its first line that is not blank.
export const firstLine = (s) => String(s ?? "").split("\n").find((line) => line.trim())?.trim() || "";

// What the head shows: the kind's word, a swatch for a highlight or a link
// region (its colour), and the preview line.
export function headInfo(block, kind = blockKind(block)) {
  const p = isRecord(block?.properties) ? block.properties : {};
  const marked = (kind === "highlight" || kind === "link") && !onHighlight(block, kind);
  const preview = kind === "page" || kind === "folder" || kind === "label" ? (block?.content || "").trim()
    : marked ? p.quote || firstLine(block?.content) : firstLine(block?.content) || p.quote || "";
  return { title: kindLabel(block, kind), swatch: marked ? colorSummary(p.color).css : null,
    preview: preview || t("(empty)"), empty: !preview };
}

// --- rows ----------------------------------------------------------------------------

// The catalog entry of a path: "recording.segments" for
// ["recording", "segments", 2].
const entryOf = (parts) => ownEntry(CATALOG, parts.filter((p) => typeof p === "string").join("."))
  || ownEntry(CATALOG, parts[0]);

// A row descriptor. `display.text` is what a row with no richer drawing
// shows, and what its copy button copies when the row has no stored value.
//
// Each `type` and its `display` (every display may also carry muted, warn,
// icon, detail and title):
//   text    {text}                 clamped to three lines
//   quote   {text}
//   mono    {text, short}          short: the hash shortened
//   flag    {text, tone}           a tag; tone null, "pending", "warn", "failed"
//   number  {text}                 the number with its unit
//   time    {text, date, relative, iso, title}
//   color   {text, css, hex, alpha, name}   css null: no swatch
//   page    {id, text, unresolved} text: the page's title
//   block   {id, text, page, pageId, state, unresolved}   state "ok", "deleted", "missing", "unresolved"
//   folders / labels  {chips: [{id, name}], missing: [{id, text}]}
//   url     {url, host, path, text}
//   file    {url, name, thumb, text}   url "" (not uploaded): text alone
//   place   placeSummary's fields + {color}
//   json    {preview, text}        text: jsonText of the value
//   code    {text}                 a BibTeX entry, a slide citation
// `nav` is the row's way somewhere, {action, label, id?, pageId?, url?}:
// action "openPage", "openBlock", "jumpHighlight", "showOnPage", "openUrl",
// "openFolder", "openLabel", "openPdf" or "copyLink"; `step` the id whose
// properties ⟨{}⟩ steps into; `tags` [{text, tone}].
function makeRow(section, path, { field = false, label, hint, type, value, display, nav, step, tags } = {}) {
  const entry = field ? ownEntry(FIELDS, path[0]) : entryOf(path);
  const key = field ? path[0] : keyPath(path, "");
  const text = display?.text ?? "";
  const copy = value === undefined ? String(text) : typeof value === "string" ? value : jsonText(value);
  const leaf = [...path].reverse().find((p) => typeof p === "string") || key;
  return {
    id: `${section}:${key}`, path, field, key,
    keyPath: field ? key : keyPath(path),
    sqlPath: field ? (COLUMNS.has(key) ? key : null) : sqlPath(path),
    label: label ?? t(entry?.label || key), hint: hint ?? (entry?.hint ? t(entry.hint) : ""),
    type, value, copy, copyJson: jsonText({ [leaf]: value === undefined ? text : value }),
    display: display || { text: "" },
    ...(nav ? { nav } : {}), ...(step ? { step } : {}), ...(tags?.length ? { tags } : {}),
  };
}

const plain = (text, extra) => ({ text, ...extra });
const muted = (text) => ({ text, muted: true });

const jsonPreview = (v) => (Array.isArray(v) ? `[${tn("{n} item", "{n} items", v.length)}]`
  : `{${tn("{n} key", "{n} keys", Object.keys(v).length)}}`);

const ISO_DATE = /^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/;

// A value drawn by its shape, for a key the catalog does not list or whose
// value is not the shape the catalog names: a web address, a date, text
// (mono when it is one long word), a number, yes or no, none, empty, JSON.
function inferred(value, ctx) {
  if (value === null) return { type: "text", display: muted(t("None")) };
  if (value === "") return { type: "text", display: muted(t("(empty)")) };
  if (typeof value === "boolean") return { type: "flag", display: { text: value ? t("Yes") : t("No"), tone: null } };
  if (isNum(value)) return { type: "number", display: plain(String(value)) };
  if (typeof value === "string") {
    if (/^https?:\/\/\S+$/i.test(value)) return { type: "url", display: urlDisplay(value) };
    if (ISO_DATE.test(value)) {
      const time = timeDisplay(value, ctx.now);
      if (time) return { type: "time", display: time };
    }
    if (!/\s/.test(value) && value.length >= 20) return { type: "mono", display: { text: value, short: shortHash(value) } };
    return { type: "text", display: plain(value) };
  }
  return { type: "json", display: { preview: jsonPreview(value), text: jsonText(value) } };
}

// A page by id: its title from the library's pages or the fetched refs;
// unresolved until onFetchRefs answers, a tag when it is gone.
function pageRef(id, ctx) {
  const page = (ctx.pages || []).find((p) => p.id === id);
  const ref = ctx.refCache?.[id];
  if (page || (ref && !ref.trashed && !ref.missing)) {
    return { display: { id, text: (page ? page.content : ref.content) || t("Untitled") } };
  }
  if (ref) return { display: { id, text: id, mono: true }, tags: [{ text: ref.trashed ? t("In Recently deleted") : t("Missing page"), tone: "warn" }] };
  return { display: { id, text: id, mono: true, unresolved: true } };
}

// A block by id: one of the open page's (lookupBlock), else a fetched ref.
function blockRef(id, ctx) {
  const node = ctx.lookupBlock?.(id);
  if (node) {
    const quote = node.properties?.quote || "";
    const text = (isHighlightBlock(node) ? quote || firstLine(node.content) : firstLine(node.content) || quote) || t("(empty)");
    return { id, text, page: ctx.pageTitle || "", pageId: ctx.pageId || "", state: "ok" };
  }
  const ref = ctx.refCache?.[id];
  if (ref?.trashed) return { id, text: t("Deleted block"), state: "deleted", muted: true };
  if (ref?.missing) return { id, text: t("Missing block"), state: "missing", muted: true };
  if (ref) return { id, text: firstLine(ref.content) || t("(empty)"), page: ref.page_title || "", pageId: ref.page_root_id || "", state: "ok" };
  return { id, text: id, state: "unresolved", unresolved: true, muted: true };
}

// The ids the rows could not resolve: what onFetchRefs is asked for.
export function refsToFetch(sections) {
  const ids = new Set();
  for (const s of sections) for (const r of s.rows) if (r.display?.unresolved && r.display.id) ids.add(r.display.id);
  return [...ids];
}

// A row of `type` for the value at `path`, drawn by the catalog's type, or
// by its shape when it is not that type's.
function valueRow(section, path, value, ctx, type = entryOf(path)?.type, extra = {}) {
  const out = (t2, display, more = {}) => makeRow(section, path, { type: t2, value, display, ...more, ...extra });
  switch (type) {
    case "text":
      if (typeof value === "string" && value) return out("text", plain(value));
      if (Array.isArray(value) && value.every((v) => typeof v === "string")) return out("text", plain(value.join(", ")));
      break;
    case "quote":
      if (typeof value === "string" && value) return out("quote", plain(value));
      break;
    case "code":
      if (typeof value === "string" && value) return out("code", plain(value));
      break;
    case "mono":
      if ((typeof value === "string" && value) || isNum(value)) return out("mono", { text: String(value), short: shortHash(value) });
      break;
    case "url":
      if (typeof value === "string" && value) {
        const display = urlDisplay(value);
        return out("url", display, { nav: { action: "openUrl", url: display.url, label: t("Open link") } });
      }
      break;
    case "time": {
      const time = timeDisplay(value, ctx.now);
      if (time) return out("time", time);
      break;
    }
    case "number":
      if (isNum(value)) return out("number", plain(String(value)));
      break;
    case "flag":
      // a flag some writers store as 1 or 0 (web_clips)
      if (value === true || value === 1) return out("flag", { text: t("Yes"), tone: null });
      if (value === false || value === 0) return out("flag", { text: t("No"), tone: null });
      if (typeof value === "string" && value) return out("flag", { text: value, tone: null });
      if (isNum(value)) return out("flag", { text: String(value), tone: null });
      break;
    case "page":
      if (typeof value === "string" && value) {
        const ref = pageRef(value, ctx);
        return out("page", ref.display, { tags: ref.tags, step: value,
          nav: { action: "openPage", id: value, label: t("Open page") } });
      }
      break;
    case "block":
      if (typeof value === "string" && value) {
        const display = blockRef(value, ctx);
        const nav = path[0] === "linked_highlight_id" ? { action: "jumpHighlight", id: value, label: t("Jump to highlight") }
          : { action: "openBlock", id: value, pageId: display.pageId || "", label: t("Go to block") };
        return out("block", display, { nav, step: display.state === "ok" ? value : undefined });
      }
      break;
    case "json":
      if (value && typeof value === "object") return out("json", { preview: jsonPreview(value), text: jsonText(value) });
      break;
    default:
      break;
  }
  const guess = inferred(value, ctx);
  return out(guess.type, guess.display);
}

// --- the kind's own section -----------------------------------------------------------

// The way to a block's place: shown on the PDF when it is one of the open
// page's blocks, else its page opened on it (the server's dict names the
// page), else none.
function placeNav(block, ctx, server) {
  if (ctx.lookupBlock?.(block.id)) return { action: "showOnPage", id: block.id, label: t("Show on page") };
  const pageId = server?.page_id;
  return pageId ? { action: "openBlock", id: block.id, pageId, label: t("Go to block") } : undefined;
}

function placeRow(section, block, pos, ctx, css, server) {
  // only the open page's blocks are measured against the open page's PDF
  const currentDocId = ctx.lookupBlock?.(block.id) ? ctx.pageAttachId : undefined;
  const summary = placeSummary(pos, { currentDocId });
  return makeRow(section, ["pdf_position"], { type: "place", value: pos, display: { ...summary, color: css },
    nav: placeNav(block, ctx, server) });
}

// A text box's or a picture's page: the sheet it is under, else its
// pdf_page.
function onPageRow(section, block, p, ctx) {
  const sheet = ctx.tree ? sheetOfBlock(ctx.tree, block.id) : null;
  if (sheet) {
    const n = sheetsOf(ctx.tree).findIndex((s) => s.id === sheet) + 1;
    return makeRow(section, ["pdf_page"], { type: "number", value: p.pdf_page, display: plain(t("On sheet {n}", { n })) });
  }
  if (!isNum(p.pdf_page)) return null;
  return makeRow(section, ["pdf_page"], { type: "number", value: p.pdf_page, display: plain(t("Page {n}", { n: p.pdf_page })) });
}

const fileName = (url) => {
  const tail = String(url || "").split("/").pop() || "";
  const dot = tail.lastIndexOf(".");
  return dot > 0 ? `${shortHash(tail.slice(0, dot))}${tail.slice(dot)}` : shortHash(tail);
};

// The rows of the kind's section, the keys they stand for, and the rows
// they add to Technical and Parts. `server`: the server's dict, or null.
function kindRows(kind, block, p, ctx, server) {
  const s = kind, rows = [], technical = [], parts = [];
  const used = new Set();
  const take = (...keys) => keys.forEach((k) => used.add(k));
  if (kind === "highlight" || kind === "link") {
    take("color", "quote", "pdf_position", "link_url", "link_page_id", "link_block_id");
    const color = colorSummary(p.color);
    rows.push(makeRow(s, ["color"], { type: "color", value: p.color, display: color }));
    if (!unset(p.quote)) rows.push(valueRow(s, ["quote"], p.quote, ctx));
    if (isRecord(p.pdf_position)) rows.push(placeRow(s, block, p.pdf_position, ctx, color.css, server));
    if (!unset(p.link_url)) rows.push(valueRow(s, ["link_url"], p.link_url, ctx));
    if (!unset(p.link_page_id)) rows.push(valueRow(s, ["link_page_id"], p.link_page_id, ctx));
    if (!unset(p.link_block_id)) {
      const row = valueRow(s, ["link_block_id"], p.link_block_id, ctx);
      if (row.nav?.action === "openBlock" && !row.nav.pageId && typeof p.link_page_id === "string") row.nav.pageId = p.link_page_id;
      rows.push(row);
    }
  } else if (kind === "ink") {
    take("ink_url", "ink_strokes", "pdf_position");
    rows.push(p.ink_url ? makeRow(s, ["ink_url"], { type: "file", value: p.ink_url,
      display: { url: p.ink_url, name: fileName(p.ink_url), text: fileName(p.ink_url) } })
      : makeRow(s, ["ink_url"], { type: "file", value: p.ink_url, display: { url: "", text: t("Not uploaded yet"), muted: true } }));
    const here = ctx.inkStrokes?.(block);
    const stored = isNum(p.ink_strokes) ? p.ink_strokes : null;
    if (stored !== null || isNum(here)) {
      const n = stored ?? here;
      const text = tn("{n} stroke", "{n} strokes", n) + (isNum(here) && here !== n ? ` ${t("(this tab: {n})", { n: here })}` : "");
      rows.push(makeRow(s, ["ink_strokes"], { type: "number", value: p.ink_strokes, display: plain(text) }));
    }
    const page = p.pdf_position?.pageNumber;
    if (page != null) {
      rows.push(makeRow(s, ["pdf_position", "pageNumber"], { type: "number", value: page, label: t("Area on PDF"),
        display: plain(t("Page {n}", { n: page })), nav: placeNav(block, ctx, server) }));
    }
    if (p.ink_strokes !== undefined) technical.push(valueRow("technical", ["ink_strokes"], p.ink_strokes, ctx));
    if (p.pdf_position !== undefined) technical.push(valueRow("technical", ["pdf_position"], p.pdf_position, ctx, "json"));
  } else if (kind === "text_box") {
    take("text_box", "pdf_page");
    const raw = p.text_box, box = normalizeTextBox(raw);
    rows.push(makeRow(s, ["text_box", "size"], { type: "number", value: raw.size, display: plain(t("{n} pt", { n: pt(box.size) })) }));
    rows.push(makeRow(s, ["text_box", "color"], { type: "color", value: raw.color, display: colorSummary(box.color) }));
    rows.push(makeRow(s, ["text_box", "bg"], { type: "color", value: raw.bg,
      display: box.bg ? colorSummary(box.bg) : colorSummary(null, { none: t("No fill") }) }));
    rows.push(makeRow(s, ["text_box", "auto"], { type: "text", value: raw.auto, display: plain(box.auto ? t("Auto") : t("Fixed")) }));
    rows.push(makeRow(s, ["text_box"], { type: "text", value: raw, display: plain(boxText(box.x, box.y, box.w, box.h)) }));
    if (box.zotero) rows.push(makeRow(s, ["text_box", "zotero"], { type: "flag", value: raw.zotero, display: { text: t("Zotero text annotation"), tone: null } }));
    const on = onPageRow(s, block, p, ctx);
    if (on) rows.push(on);
  } else if (kind === "picture") {
    take("picture", "pdf_page");
    const raw = p.picture, pic = normalizePicture(raw);
    if (!pic) {
      rows.push(makeRow(s, ["picture", "url"], { type: "flag", value: raw.url, display: { text: t("File missing"), tone: "failed" } }));
    } else {
      rows.push(makeRow(s, ["picture", "url"], { type: "file", value: raw.url,
        display: { url: pic.url, name: fileName(pic.url), text: fileName(pic.url), thumb: true } }));
      rows.push(makeRow(s, ["picture"], { type: "text", value: raw, display: plain(boxText(pic.x, pic.y, pic.w, pic.h)) }));
      if (pic.angle) rows.push(makeRow(s, ["picture", "angle"], { type: "text", value: raw.angle, display: plain(`${pic.angle}°`) }));
      const c = pic.crop;
      if (c.x || c.y || c.w < 1 || c.h < 1) {
        rows.push(makeRow(s, ["picture", "crop"], { type: "text", value: raw.crop, display: plain(`${pct(c.w)}% × ${pct(c.h)}%`) }));
      }
      if (pic.flip_h || pic.flip_v) {
        const text = pic.flip_h && pic.flip_v ? t("Horizontally and vertically") : pic.flip_h ? t("Horizontally") : t("Vertically");
        rows.push(makeRow(s, ["picture", "flip_h"], { type: "text", value: raw.flip_h, display: plain(text) }));
      }
      if (pic.opacity !== undefined) rows.push(makeRow(s, ["picture", "opacity"], { type: "text", value: raw.opacity, display: plain(`${pct(pic.opacity)}%`) }));
      if (pic.z) rows.push(makeRow(s, ["picture", "z"], { type: "number", value: raw.z, display: plain(String(pic.z)) }));
      if (pic.locked) rows.push(makeRow(s, ["picture", "locked"], { type: "flag", value: raw.locked, display: { text: t("Locked"), tone: null } }));
    }
    const on = onPageRow(s, block, p, ctx);
    if (on) rows.push(on);
  } else if (kind === "sheet") {
    take("sheet");
    const raw = p.sheet, paper = normalizePaper(raw);
    const size = { a4: "A4", letter: t("Letter"), a5: "A5" }[paperSizeKey(paper)] || t("Custom");
    const text = [size, ...(isLandscape(paper) ? [t("Landscape")] : []), t("{w}×{h} pt", { w: pt(paper.width), h: pt(paper.height) })];
    rows.push(makeRow(s, ["sheet"], { type: "text", value: raw, display: plain(text.join(" · ")) }));
    rows.push(makeRow(s, ["sheet", "color"], { type: "color", value: raw.color, display: colorSummary(paper.color) }));
    const pattern = { blank: t("Blank"), ruled: t("Ruled"), grid: t("Grid"), dots: t("Dots") }[paper.pattern];
    rows.push(makeRow(s, ["sheet", "pattern"], { type: "text", value: raw.pattern,
      display: plain(paper.pattern === "blank" ? pattern : `${pattern} · ${t("{n} pt", { n: pt(paper.spacing) })}`) }));
    if (paper.pattern !== "blank") rows.push(makeRow(s, ["sheet", "line"], { type: "color", value: raw.line, display: colorSummary(paper.line) }));
  } else if (kind === "recording") {
    take("recording");
    const raw = p.recording, rec = normalizeRecording(raw);
    // A recording not done is live while this tab or another holds its
    // lease; a lease another tab let go stale is a tab that went away, and
    // the recording was interrupted (as its card says).
    const held = ctx.tabId !== undefined && heldElsewhere(rec, ctx.tabId, ctx.now ?? Date.now());
    const live = rec.state !== "done" && (held || ctx.tabId === undefined || rec.by === ctx.tabId);
    const state = rec.state !== "done" && !live ? t("Interrupted")
      : { recording: t("Recording now"), paused: t("Paused"), done: t("Done") }[rec.state];
    rows.push(makeRow(s, ["recording", "state"], { type: "flag", value: raw.state,
      display: { text: held ? `${state} · ${t("held by another tab")}` : state, tone: live && rec.state === "recording" ? "pending" : null } }));
    if (rec.started !== null) rows.push(valueRow(s, ["recording", "started"], rec.started, ctx, "time", { value: raw.started }));
    rows.push(makeRow(s, ["recording", "segments"], { type: "text", value: raw.segments, display: plain(fmtClock(rec.dur)) }));
    // Each part as normalizeRecording reads it, in start order, with the
    // index it is stored at, so its value and paths are that entry's.
    const stored = isRecord(raw) && Array.isArray(raw.segments) ? raw.segments : [];
    stored.map((entry, i) => ({ i, seg: normalizeRecording({ segments: [entry] }).segments[0] }))
      .filter((x) => x.seg).sort((a, b) => a.seg.started - b.seg.started)
      .forEach(({ i, seg }, k) => {
        const name = [fmtDate(new Date(seg.started), { timeStyle: "medium" }), fmtClock(seg.dur), fmtBytes(seg.bytes), seg.mime]
          .filter(Boolean).join(" · ");
        parts.push(makeRow("parts", ["recording", "segments", i], { type: "file", value: stored[i], label: `#${k + 1}`,
          hint: "", display: { url: seg.url, name, text: name } }));
      });
    for (const k of ["dur", "by", "at"]) {
      if (isRecord(raw) && raw[k] !== undefined) technical.push(valueRow("technical", ["recording", k], raw[k], ctx));
    }
  }
  return { rows, technical, parts, used };
}

// --- the page record ---------------------------------------------------------------

function paperRows(p, ctx) {
  const rows = [], meta = isRecord(p.meta) ? p.meta : null;
  if (meta) {
    const type = itemTypeOf(meta);
    if (meta.title) rows.push(valueRow("paper", ["meta", "title"], meta.title, ctx));
    if (meta.authors?.length) rows.push(valueRow("paper", ["meta", "authors"], meta.authors, ctx));
    for (const [label, field] of itemTypeFields(type, meta)) {
      if (String(meta[field] ?? "").trim()) rows.push(valueRow("paper", ["meta", field], meta[field], ctx, "text", { label: t(label), hint: "" }));
    }
    rows.push(makeRow("paper", ["meta", "kind"], { type: "text", value: meta.kind, display: plain(t(itemTypeLabel(type))) }));
    if (meta.doi) rows.push(valueRow("paper", ["meta", "doi"], meta.doi, ctx));
    if (meta.arxiv_id) rows.push(valueRow("paper", ["meta", "arxiv_id"], meta.arxiv_id, ctx));
    const source = metaSourceInfo(meta);
    if (source) {
      rows.push(makeRow("paper", ["meta", "source"], { type: "text", value: meta.source,
        display: { text: source.label, warn: source.warn, title: t(source.hint) } }));
    }
    // a record edited by hand was edited then, as paper details says
    if (meta.at) rows.push(valueRow("paper", ["meta", "at"], meta.at, ctx, "time", { label: meta.source === "manual" ? t("Edited") : t("Fetched") }));
    if (meta.abstract) rows.push(valueRow("paper", ["meta", "abstract"], meta.abstract, ctx));
  } else if (p.meta !== undefined) {
    rows.push(valueRow("paper", ["meta"], p.meta, ctx));
  }
  if (!unset(p.cite_key)) {
    rows.push(makeRow("paper", ["cite_key"], { type: "mono", value: p.cite_key,
      display: { text: String(p.cite_key), short: shortHash(p.cite_key), icon: "pin" } }));
  } else if (citationKeyOf(p.bibtex)) {
    rows.push(makeRow("paper", ["cite_key"], { type: "mono", value: undefined,
      display: { text: t("Automatic: {key}", { key: citationKeyOf(p.bibtex) }), muted: true } }));
  }
  if (!unset(p.bibtex)) rows.push(valueRow("paper", ["bibtex"], p.bibtex, ctx));
  if (!unset(p.ppt_cite)) rows.push(valueRow("paper", ["ppt_cite"], p.ppt_cite, ctx));
  return rows;
}

// A catalog key's row outside the kind's section, with what its key adds
// (a file's icon, the PDF's link, a filing's chips).
function keyRow(section, key, value, ctx) {
  if (key === "folders" || key === "labels") {
    const ids = Array.isArray(value) ? value.filter((id) => typeof id === "string") : [];
    if (!ids.length || !ctx.libTree) return valueRow(section, [key], value, ctx, "json");
    const chips = filingChips(ctx.libTree, { [key]: ids })[key];
    const found = new Set(chips.map((c) => c.id));
    // An id the tree does not hold is missing only where the tree is the
    // whole library: a share lists part of it or none, and an empty tree
    // is a listing that never loaded. There it is out of sight, not gone.
    const whole = !ctx.shareMode && Boolean(ctx.libTree.folders?.size || ctx.libTree.labels?.size);
    const gone = !whole ? t("Not visible here") : key === "folders" ? t("Missing folder") : t("Missing label");
    return makeRow(section, [key], { type: key, value,
      display: { chips, missing: ids.filter((id) => !found.has(id)).map((id) => ({ id, text: gone, ...(whole ? { warn: true } : {}) })),
        text: chips.map((c) => c.name).join(", ") } });
  }
  if (key === "pinned") {
    const time = timeDisplay(value, ctx.now);
    if (time) return makeRow(section, [key], { type: "time", value, display: { ...time, text: t("Pinned {when}", { when: time.relative }), title: time.text } });
  }
  if (key === "original_filename" && typeof value === "string") {
    return makeRow(section, [key], { type: "text", value, display: { text: value, icon: "paperclip" } });
  }
  if (key === "doc_id" && typeof value === "string") {
    return makeRow(section, [key], { type: "mono", value, display: { text: value, short: shortHash(value) },
      nav: { action: "openPdf", url: `/api/uploads/${value}.pdf`, label: t("Open PDF") } });
  }
  if (key === "collapsed" && typeof value === "boolean") {
    return makeRow(section, [key], { type: "flag", value, display: { text: value ? t("Folded") : t("Unfolded"), tone: null } });
  }
  if (key === "transcript" && value === true) {
    return makeRow(section, [key], { type: "flag", value, display: { text: t("Transcript"), tone: null } });
  }
  if (key === "zotero_readonly" && typeof value === "string") {
    const text = value === "locked" ? t("Another member's annotation") : t("Kept in Gamma");
    return makeRow(section, [key], { type: "flag", value, display: { text, tone: null } });
  }
  if (key === "pdf_position" && isRecord(value) && Object.keys(value).every((k) => k === "pageNumber")) {
    return makeRow(section, ["pdf_position", "pageNumber"], { type: "number", value: value.pageNumber,
      display: plain(t("Page {n}", { n: value.pageNumber })) });
  }
  if (key === "pdf_page" && isNum(value)) {
    return makeRow(section, [key], { type: "number", value, display: plain(t("Page {n}", { n: value })) });
  }
  return valueRow(section, [key], value, ctx);
}

// --- the block section ----------------------------------------------------------------

function blockRows(block, server, kind, ctx) {
  const rows = [], s = "block";
  const local = blockKind(block);
  const word = (k) => t(ownEntry(KINDS, k)?.label || KINDS.note.label);
  const kindText = server?.kind && server.kind !== local && !(onHighlight(block, local)) ? `${word(server.kind)} → ${word(local)}`
    : kindLabel(block, kind);
  rows.push(makeRow(s, ["kind"], { field: true, type: "text", value: kind, display: plain(kindText) }));
  rows.push(makeRow(s, ["id"], { field: true, type: "mono", value: block.id, display: { text: block.id, short: shortHash(block.id) },
    nav: { action: "copyLink", id: block.id, label: t("Copy link to block") } }));
  const row = !["page", "folder", "label"].includes(kind);
  const pageId = server?.page_id ?? ctx.pageId;
  if (row && pageId) {
    const ref = pageRef(pageId, ctx);
    if (pageId === ctx.pageId && ctx.pageTitle) ref.display.text = ctx.pageTitle;
    rows.push(makeRow(s, ["page_id"], { field: true, type: "page", value: pageId, display: ref.display, tags: ref.tags, step: pageId,
      nav: { action: "openPage", id: pageId, label: t("Open page") } }));
  }
  const node = ctx.lookupBlock?.(block.id);
  const parent = server ? server.parent_id : node ? node.parentId ?? ctx.pageId : block.parent_id;
  if (parent) {
    let display, nav, step;
    if (parent === "root" || ((kind === "folder" || kind === "label") && parent === server?.page_id)) display = plain(t("Library"));
    else if (parent === "trash") display = plain(t("Recently deleted"));
    else if (parent === pageId) display = plain(t("Top level of the page"));
    else if (ctx.libTree?.folders?.has(parent)) {
      display = plain(folderPath(ctx.libTree, parent));
      nav = { action: "openFolder", id: parent, label: t("Open folder") };
    } else {
      display = blockRef(parent, ctx);
      nav = { action: "openBlock", id: parent, pageId: display.pageId || pageId || "", label: t("Go to block") };
      step = display.state === "ok" ? parent : undefined;
    }
    rows.push(makeRow(s, ["parent_id"], { field: true, type: display.state ? "block" : "text", value: parent, display, nav, step }));
  }
  if (row && ctx.tree) {
    const info = getParentInfo(ctx.tree, block.id);
    if (info) {
      const text = t("{i} of {n} · level {d}", { i: info.index + 1, n: info.siblings.length, d: (node?.depth ?? 0) + 1 });
      rows.push(makeRow(s, ["order"], { field: true, type: "text", display: plain(text) }));
    }
  }
  const saved = !changedKeys(block, server).length;
  for (const key of ["created_at", "updated_at"]) {
    if (!server) {
      rows.push(makeRow(s, [key], { field: true, type: "time", display: { text: "…", loading: true } }));
      continue;
    }
    const time = timeDisplay(server[key], ctx.now);
    if (!time) continue;
    const display = key === "updated_at" ? { ...time, text: `${time.relative} · ${saved ? t("saved") : t("changed in this tab")}`, title: time.text }
      : time;
    rows.push(makeRow(s, [key], { field: true, type: "time", value: server[key], display }));
  }
  if (row) {
    const content = block.content ?? "";
    rows.push(makeRow(s, ["content"], { field: true, type: "text", value: content,
      display: content ? { text: firstLine(content), detail: tn("{n} character", "{n} characters", content.length) } : muted(t("(empty)")) }));
  }
  return rows;
}

// --- sections -------------------------------------------------------------------------

const SECTIONS = ["kind", "parts", "document", "paper", "filing", "deleted", "timing", "block", "imported", "zotero", "other", "technical"];
const SECTION_TITLES = {
  parts: T("Parts"), document: T("Document"), paper: T("Paper record"), filing: T("Filing"), deleted: T("Recently deleted"),
  timing: T("Timing and display"), block: T("Block"), imported: T("Imported from"), zotero: T("Zotero"), other: T("Other keys"),
  technical: T("Technical"),
};
// Sections behind a disclosure, and those it starts closed.
const DISCLOSED = new Set(["imported", "zotero", "other", "technical"]);
const CLOSED = new Set(["imported", "zotero", "technical"]);

// The popover's Details: [{id, title, rows, disclose, collapsed, dim,
// action?}] in display order, a section with no rows left out. The kind's
// own section's id is the kind ("highlight"). `block` is the block as this
// tab has it (the server's dict when there is no live one); `server` the
// server's dict once fetched; `kind` the kind to show (effectiveKind).
// ctx: {pages, libTree, lookupBlock, refCache, pageAttachId, pageId,
// pageTitle, tree, now, isOpenPage, inkStrokes(block), tabId}.
export function propertySections(block, { kind, server = null, ctx = {} } = {}) {
  const c = { now: Date.now(), ...ctx };
  const shownKind = kind || effectiveKind(block, server);
  const p = isRecord(block?.properties) ? block.properties : {};
  const layout = onHighlight(block, shownKind) ? "note" : shownKind;
  const out = Object.fromEntries(SECTIONS.map((id) => [id, []]));
  const own = kindRows(layout, block, p, c, server);
  out.kind = own.rows;
  out.parts = own.parts;
  out.technical.push(...own.technical);
  out.paper = paperRows(p, c);
  const paperKeys = new Set(["meta", "cite_key", "bibtex", "ppt_cite"]);
  const oldPage = (layout === "highlight" || layout === "link") && p.pdf_page !== undefined;
  const legacy = [];
  for (const [key, entry] of Object.entries(CATALOG)) {
    if (key.includes(".") || !(key in p) || own.used.has(key) || paperKeys.has(key)) continue;
    const value = p[key];
    if (entry.legacy || (key === "pdf_page" && oldPage)) {
      legacy.push({ ...keyRow("technical", key, value, c), tags: [{ text: t("Old key"), tone: null }] });
      continue;
    }
    if (unset(value) && key !== "ink_url") continue;
    const section = entry.section === "kind" ? (key === "pdf_position" && layout === "note" ? "timing" : "technical") : entry.section;
    out[section].push(keyRow(section, key, value, c));
  }
  for (const key of Object.keys(p)) {
    if (Object.hasOwn(CATALOG, key)) continue;
    out.other.push({ ...valueRow("other", [key], p[key], c, null), tags: [{ text: t("Not in the catalog"), tone: null }] });
  }
  out.block = block ? blockRows(block, server, shownKind, c) : [];
  const position = server?.position ?? block?.position;
  if (typeof position === "string" && position) {
    out.technical.unshift(makeRow("technical", ["position"], { field: true, type: "mono", value: position, display: { text: position, short: position } }));
  }
  out.technical.push(...legacy);
  return SECTIONS.filter((id) => out[id].length).map((id) => ({
    id: id === "kind" ? layout : id,
    title: id === "kind" ? kindLabel(block, shownKind) : t(SECTION_TITLES[id]),
    rows: out[id],
    disclose: DISCLOSED.has(id), collapsed: CLOSED.has(id), dim: id === "technical",
    ...(id === "paper" && c.isOpenPage ? { action: { action: "editMeta", label: t("Edit in paper details…") } } : {}),
  }));
}

// --- status ---------------------------------------------------------------------------

// How long this tab may differ from the server before a read still finding
// it so says it differs.
export const DIFFER_MS = 4000;

// The head's status chips, [{id, text, tone, action?}], tone null,
// "pending", "warn" or "failed", action "refetch" or "retry". `live` is the
// block as this tab has it, `server` the fetched dict, `err` the fetch's
// error (status, data). ctx: {pageAttachId (undefined for a block not on
// the open page), differSince (when this tab's block started to differ from
// the server's, or the server's copy last moved while it did), readAt (when
// the last read that answered was asked for), gone (the live block was
// deleted while the popover was open)}. The difference reads as pending
// until a read asked DIFFER_MS after it began still finds it.
export function statusChips(live, server, err, ctx = {}) {
  const chips = [];
  const block = live || server;
  const p = isRecord(block?.properties) ? block.properties : {};
  if (ctx.gone) chips.push({ id: "gone", tone: "failed", text: t("This block was deleted.") });
  const changed = live && server && !ctx.gone ? changedKeys(live, server).length : 0;
  if (changed) {
    chips.push(ctx.differSince != null && ctx.readAt != null && ctx.readAt - ctx.differSince >= DIFFER_MS
      ? { id: "differs", tone: "warn", action: "refetch", text: tn("This tab differs · {n} key", "This tab differs · {n} keys", changed) }
      : { id: "pending", tone: "pending", text: t("Changes not saved yet") });
  }
  const missing = err?.status === 404;
  // not saved yet: a block of this tab's the server has never had
  if (missing && live && !ctx.gone && !server && !err.data?.trashed) chips.push({ id: "unsaved", tone: "pending", text: t("Not saved yet") });
  if (!unset(p.deleted_at) || (missing && err.data?.trashed)) chips.push({ id: "deleted", tone: "failed", text: t("In Recently deleted") });
  if (isHighlightBlock(block) && isRecord(p.pdf_position)) {
    chips.push(...placeSummary(p.pdf_position, { currentDocId: ctx.pageAttachId }).chips);
  }
  if (!unset(p.zotero_readonly)) chips.push({ id: "zotero", tone: null, text: t("Not written back to Zotero") });
  if (p.transcript) chips.push({ id: "transcript", tone: null, text: t("Transcript") });
  if (err && !missing) chips.push({ id: "failed", tone: "failed", action: "retry", text: t("Couldn't load from the server") });
  return chips;
}
