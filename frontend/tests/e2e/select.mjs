// Which browser-suite groups a change needs (docs/dev/debugging.md): the
// groups themselves, and RULES mapping every source path to the groups that
// can break when it changes. `npm run e2e -- --changed` runs the groups the
// working tree's changes select; tests/e2eSelect.test.mjs keeps every source
// file matched by a rule other than the fallback, so a new module forces a
// decision instead of silently selecting nothing.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const SCENARIOS = path.join(ROOT, "frontend", "tests", "e2e", "scenarios");

// Longest first, so the long ones never start last. `s` is the seconds a
// group took in CI (3 workers on the runner's 4 cores; its log's
// "── <group>  (N s" lines): the queue order, and what --shard balances.
// A group must not need another group's data: scenarios that hand data
// along share a group (notes → pdf → share).
export const GROUPS = [
  { id: "settings", files: ["settings.mjs"], s: 148 },
  { id: "notes-pdf-share", files: ["notes.mjs", "pdf.mjs", "share.mjs"], s: 121 },
  { id: "ink", files: ["ink.mjs"], s: 95 },
  { id: "triggered-guide", files: ["triggeredGuide.mjs"], s: 78 },
  { id: "chat-navigation", files: ["chatNavigation.mjs"], s: 67 },
  { id: "touch", files: ["touch.mjs"], s: 63 },
  { id: "guide", files: ["guide.mjs"], s: 61 },
  { id: "contextual-guide", files: ["contextualGuide.mjs"], s: 60 },
  { id: "ink-editing", files: ["inkEditing.mjs"], s: 48 },
  { id: "transfers", files: ["transfers.mjs"], s: 45 },
  { id: "textbox", files: ["textBoxes.mjs"], s: 42 },
  { id: "ipad", files: ["ipad.mjs"], s: 37 },
  { id: "collab", files: ["collab.mjs"], s: 33 },
  { id: "comments", files: ["comments.mjs"], s: 27 },
  { id: "pdf-load", files: ["pdfload.mjs"], s: 25 },
  { id: "publish", files: ["publish.mjs"], s: 25 },
  { id: "pdf-touch", files: ["pdfTouch.mjs"], s: 23 },
  { id: "files", files: ["files.mjs"], s: 22 },
  { id: "cloud-sign-in", files: ["cloudSignIn.mjs"], s: 20 },
  { id: "diagrams", files: ["diagrams.mjs"], s: 19 },
  { id: "mentions", files: ["mentions.mjs"], s: 18 },
  { id: "ink-shapes", files: ["inkShapes.mjs"], s: 18 },
  { id: "notebooks", files: ["notebooks.mjs"], s: 17 },
  { id: "auth", files: ["auth.mjs"], s: 17 },
  { id: "inbox", files: ["inbox.mjs"], s: 15 },
  { id: "quick-open", files: ["quickOpen.mjs"], s: 14 },
  { id: "folders", files: ["folders.mjs"], s: 14 },
  { id: "mcp", files: ["mcp.mjs"], s: 12 },
  { id: "note-mentions", files: ["noteMentions.mjs"], s: 10 },
  { id: "recording", files: ["recording.mjs"], s: 7 },
  { id: "mirror", files: ["mirror.mjs"], s: 7 },
  { id: "ink-zoom", files: ["inkZoom.mjs"], s: 7 },
  { id: "trash", files: ["trash.mjs"], s: 5 },
  { id: "i18n", files: ["i18n.mjs"], s: 4 },
];

// The groups of `ids` that shard k of n runs: each group, longest first,
// goes to the shard with the least time so far, so the shards end close
// together. Every shard computes the same split.
export function shardOf(ids, k, n) {
  const time = Object.fromEntries(GROUPS.map((g) => [g.id, g.s]));
  const load = Array(n).fill(0);
  const mine = new Set();
  for (const id of [...ids].sort((a, b) => time[b] - time[a])) {
    const i = load.indexOf(Math.min(...load));
    load[i] += time[id];
    if (i === k - 1) mine.add(id);
  }
  return ids.filter((id) => mine.has(id));
}

const ALL = "all";
const GUIDES = ["guide", "contextual-guide", "triggered-guide"];
const CHAT = ["chat-navigation", "mentions", "contextual-guide", "triggered-guide", "notes-pdf-share", "touch"];
// every group that opens a PDF in the viewer
const PDF_VIEW = ["notes-pdf-share", "pdf-load", "pdf-touch", "ink", "ink-editing", "ink-shapes", "ink-zoom", "textbox", "collab", "comments", "files", "transfers", "touch", ...GUIDES];
const EXPORTS = ["transfers", "files", "ink", "diagrams"];

// [glob, groups], first match wins. `**` spans folders, `*` stays in one.
// ALL is for what every scenario goes through (the app shell, shared UI,
// sign-in, block storage and the page's write path); [] for what the browser
// suite never loads.
export const RULES = [
  ["**/*.md", []],
  // the suite itself: a scenario file selects its group and every scenario importing it (see scenarioImporters)
  ["frontend/tests/e2e/select.mjs", []],
  ["frontend/tests/e2e/latexEditor.mjs", []], // its own runner: npm run e2e:latex
  ["frontend/tests/e2e/harness.mjs", ALL],
  ["frontend/tests/e2e/run.mjs", ALL],
  ["frontend/tests/e2e/**", []],
  ["frontend/tests/**", []],
  ["frontend/tools/**", []],

  ["frontend/src/shared/i18n/locales/**", ["i18n", "settings"]],
  ["frontend/src/shared/illustrations/**", GUIDES],
  ["frontend/src/shared/**", ALL],
  ["frontend/src/app/**", ALL],
  ["frontend/src/main.jsx", ALL],
  ["frontend/src/guide/**", GUIDES],
  // (a text box's menu is placed and labelled by the ink layer's helpers;
  // comments: a press with a pen or the Text tool armed leaves a margin card alone)
  ["frontend/src/ink/**", ["ink", "ink-editing", "ink-shapes", "ink-zoom", "pdf-touch", "triggered-guide", "notebooks", "textbox", "touch", "ipad", "comments"]], // ipad: the host claim path
  // what a page surface carries (ink and text boxes) and the tool strip, whose anchors the ink tour uses
  ["frontend/src/markup/**", ["ink", "ink-editing", "ink-shapes", "ink-zoom", "pdf-touch", "triggered-guide", "notebooks", "textbox", "touch", "comments"]],
  ["frontend/src/notebook/**", ["notebooks", "textbox", "touch"]],
  // the replay store every handwriting replay and a recording's player run on
  ["frontend/src/replay/**", ["ink", "ink-editing", "notebooks", "recording"]],
  ["frontend/src/recording/**", ["recording"]],
  // the row control, the thread popover, the Comments window, the page's threads over the socket
  ["frontend/src/comments/**", ["comments", "collab", "inbox"]],
  // the bell, its panel and the jumps (a thread's opens through comments/)
  ["frontend/src/inbox/**", ["inbox"]],
  ["frontend/src/pdf/**", PDF_VIEW],
  // (a text box's editor and read mode are the notes')
  // the @ menu and its chips (the reminders are App's, under app/**)
  ["frontend/src/editor/mentions.js", ["note-mentions", "notes-pdf-share"]],
  ["frontend/src/editor/MentionMenu.jsx", ["note-mentions", "notes-pdf-share", "inbox"]],
  // the @ menu's state and keys, a note row's and a comment field's
  ["frontend/src/editor/useMentionMenu.js", ["note-mentions", "notes-pdf-share", "comments", "inbox"]],
  ["frontend/src/editor/**", ["notes-pdf-share", "diagrams", "files", "collab", "comments", "transfers", "mcp", "guide", "triggered-guide", "textbox", "touch"]],
  ["frontend/src/chat/**", [...CHAT, "ink-zoom"]], // ink-zoom: a selection's Transcribe reaches the chat's request
  // the Inbox notifications row
  ["frontend/src/settings/SettingsAppearance.jsx", ["settings", "i18n", "mirror", "cloud-sign-in", "mcp", "publish", "triggered-guide", "ipad", "inbox"]],
  ["frontend/src/settings/**", ["settings", "i18n", "mirror", "cloud-sign-in", "mcp", "publish", "triggered-guide", "ipad"]], // ipad: Apple Pencil, under its host
  ["frontend/src/collaboration/MirrorPopover.jsx", ["mirror", "publish"]],
  ["frontend/src/collaboration/MergeResolver.jsx", ["mirror"]],
  ["frontend/src/collaboration/**", ALL], // the page's live session carries every edit
  ["frontend/src/library/QuickOpen.jsx", ["quick-open"]],
  ["frontend/src/library/RecentlyDeleted.jsx", ["trash"]],
  ["frontend/src/library/**", ALL], // the home view every scenario opens on
  ["frontend/src/search/**", ["notes-pdf-share", "quick-open"]],
  ["frontend/src/sharing/**", ["notes-pdf-share", "publish", "collab"]],
  ["frontend/src/transfers/**", EXPORTS],
  ["frontend/src/tasks/**", [...EXPORTS, "settings", "ipad"]], // the Background tasks tray and its store
  ["frontend/src/support/**", ["settings"]],
  ["frontend/src/auth/**", ["auth", "cloud-sign-in", "publish", "ipad"]], // ipad: the sign-in page's Server link
  ["frontend/public/**", ["ipad"]],
  ["frontend/index.html", ALL],
  ["frontend/vite.config.js", ALL],
  ["frontend/package.json", ALL],
  ["frontend/package-lock.json", ALL],

  ["backend/tests/notability_fixtures.py", ["transfers"]], // the Notability note the transfers scenario imports
  ["backend/tests/**", []],
  ["backend/gamma/ai_protocols/**", [...CHAT, "settings"]],
  ["backend/gamma/ai_*.py", [...CHAT, "settings", "ink-zoom"]],
  ["backend/gamma/chatgpt_oauth.py", ["settings"]],
  ["backend/gamma/routers/ai.py", [...CHAT, "settings", "ink-zoom"]],
  ["backend/gamma/routers/chats.py", CHAT],
  ["backend/gamma/mcp_*", ["mcp"]],
  ["backend/gamma/integrations.py", ["mcp", "mirror", "settings"]],
  ["backend/gamma/routers/integrations.py", ["mcp", "mirror", "settings"]],
  // folders on disk: the desktop app's suite drives them (desktop/test/e2e.js)
  ["backend/gamma/folder_links.py", []],
  ["backend/gamma/folder_sync.py", []],
  ["backend/gamma/gamma_sync.py", []],
  ["backend/gamma/routers/folder_links.py", []],
  ["backend/gamma/sync_*.py", ["mirror", "publish"]],
  ["backend/gamma/textmerge.py", ["mirror"]],
  ["backend/gamma/routers/sync.py", ["mirror", "publish"]],
  ["backend/gamma/routers/mirrors.py", ["mirror", "publish"]],
  ["backend/gamma/cloud_*.py", ["cloud-sign-in", "publish"]],
  ["backend/gamma/routers/cloud_auth.py", ["cloud-sign-in", "publish"]],
  ["backend/gamma/hosted.py", ["cloud-sign-in"]], // a hosted container's plan (read-only gates every write only there)
  ["backend/gamma/publish.py", ["publish"]],
  ["backend/gamma/routers/publish.py", ["publish"]],
  // transfers: the Notability import writes its strokes through ink.py (encode_points, the file's checks, group_files)
  ["backend/gamma/ink.py", ["ink", "ink-editing", "ink-shapes", "triggered-guide", "notebooks", "transfers"]],
  ["backend/gamma/routers/ink.py", ["ink", "ink-editing", "ink-shapes", "triggered-guide", "notebooks"]],
  ["backend/gamma/notebook.py", ["notebooks", "transfers"]], // transfers: a Notability note's paper pages (paper_ops)
  ["backend/gamma/recording.py", ["recording", ...EXPORTS]],
  ["backend/gamma/routers/recordings.py", ["recording"]],
  // what the exports, the notebook PDF and the agent's outline read of a text box
  ["backend/gamma/text_box.py", [...EXPORTS, "notebooks", "textbox", ...CHAT]],
  ["backend/gamma/pdf_meta.py", PDF_VIEW],
  ["backend/gamma/pdf_text.py", PDF_VIEW],
  ["backend/gamma/pdf_index.py", ["notes-pdf-share", "quick-open"]],
  ["backend/gamma/routers/pdf.py", PDF_VIEW],
  ["backend/gamma/textnorm.py", ["notes-pdf-share", "quick-open"]],
  ["backend/gamma/block_index.py", ["notes-pdf-share", "quick-open"]],
  ["backend/gamma/routers/search.py", ["notes-pdf-share", "quick-open"]],
  ["backend/gamma/markdown_*.py", EXPORTS],
  ["backend/gamma/obsidian_export.py", EXPORTS],
  ["backend/gamma/logseq_*.py", EXPORTS],
  ["backend/gamma/zotero_*.py", EXPORTS],
  ["backend/gamma/import_*.py", EXPORTS],
  ["backend/gamma/notability*.py", EXPORTS],
  ["backend/gamma/pdf_export.py", EXPORTS],
  ["backend/gamma/pdf_document.py", EXPORTS],
  ["backend/gamma/pdf_typeset.py", EXPORTS],
  ["backend/gamma/pdf_glyphs.py", EXPORTS],
  ["backend/gamma/pdf_image.py", EXPORTS],
  ["backend/gamma/pdf_notes.py", EXPORTS],
  ["backend/gamma/vector_text.py", EXPORTS],
  ["backend/gamma/note_markup.py", EXPORTS],
  ["backend/gamma/fonts/**", EXPORTS],
  ["backend/gamma/routers/export.py", EXPORTS],
  ["backend/gamma/routers/imports.py", EXPORTS],
  ["backend/gamma/jobs.py", [...EXPORTS, "settings", "notes-pdf-share"]], // exports, backups, the search indexer
  ["backend/gamma/routers/jobs.py", [...EXPORTS, "settings"]],
  ["backend/gamma/backup_schedule.py", ["settings"]],
  ["backend/gamma/backups.py", ["settings"]],
  ["backend/gamma/ws_backup.py", ["settings"]],
  ["backend/gamma/routers/backup_tasks.py", ["settings"]],
  ["backend/gamma/routers/ws_backups.py", ["settings"]],
  ["backend/gamma/notices.py", ["settings"]],
  ["backend/gamma/routers/notices.py", ["settings"]],
  ["backend/gamma/version.py", ["settings"]],
  ["backend/gamma/server_settings.py", ["settings", "mcp", "cloud-sign-in"]],
  ["backend/gamma/routers/admin.py", ["settings", "mcp", "cloud-sign-in"]],
  ["backend/gamma/routers/shares.py", ["notes-pdf-share", "publish", "collab", "inbox"]],
  ["backend/gamma/routers/links.py", ["notes-pdf-share", "guide"]],
  ["backend/gamma/routers/metadata.py", ["notes-pdf-share", "guide"]],
  ["backend/gamma/translate_engines.py", ["settings", "notes-pdf-share"]],
  // deleting a folder or a label (the trees' other writes are ops, ALL below)
  ["backend/gamma/routers/folders.py", ["folders", "notes-pdf-share"]],
  // comments: their storage and API, and the hooks every block writer runs
  // (orphan, place and purge on deletes, moves and page purges; the copies
  // of a backup merge and an import; after_write feeds the inbox)
  ["backend/gamma/comments.py", ["comments", "inbox", "notes-pdf-share", "collab", "transfers"]],
  ["backend/gamma/routers/comments.py", ["comments", "inbox"]],
  // the Inbox: its producers' items, the listing and its settle, the marks
  ["backend/gamma/inbox.py", ["inbox", "comments"]],
  ["backend/gamma/routers/inbox.py", ["inbox"]],
  ["backend/gamma/publisher_sessions.py", ["settings"]],
  ["backend/gamma/routers/publisher_sessions.py", ["settings"]],
  ["backend/gamma/routers/clip.py", []], // the browser extension's ingest
  ["backend/gamma/offsite.py", ["settings"]], // the Off-site copies section of Backups
  ["backend/gamma/s3.py", ["settings"]],
  // the core every scenario goes through: app, auth, db, migrations, workspaces, blocks, ops, uploads, prefs, collab …
  // the chat's pictures of handwriting (view_ink, an attached group, a transcribed selection): the AI modules' groups
  ["backend/gamma/ink_view.py", [...CHAT, "settings", "ink-zoom"]],
  ["backend/gamma/**", ALL],
  ["backend/app.py", ALL],
  ["backend/manage.py", ALL],
  ["backend/requirements.txt", ALL],
  ["backend/requirements-dev.txt", ["transfers"]], // the Zotero fixture
  ["backend/**", []],
  // docs, desktop, extension, cloud, sites, tools, plugins, CI …
  ["**", []],
];

const regexCache = new Map();
function globRegex(glob) {
  if (!regexCache.has(glob)) {
    const re = glob.replace(/[.+^${}()|[\]\\]/g, "\\$&")
      .replace(/\*\*\/?/g, "\u0000").replace(/\*/g, "[^/]*").replace(/\u0000/g, ".*");
    regexCache.set(glob, new RegExp(`^${re}$`));
  }
  return regexCache.get(glob);
}

// The rule a path falls under: { glob, groups } (groups is ALL or ids).
export function ruleFor(file) {
  const [glob, groups] = RULES.find(([g]) => globRegex(g).test(file));
  return { glob, groups };
}

// Scenario file → the groups it and its importers belong to (a helper in
// notes.mjs or pdf.mjs is used across the suite). Also covers the suite's
// other modules (fakeCloud.mjs, …).
export function scenarioImporters(file) {
  const e2e = "frontend/tests/e2e/";
  if (!file.startsWith(e2e)) return [];
  const name = path.basename(file);
  const groupOf = (f) => GROUPS.filter((g) => g.files.includes(f)).map((g) => g.id);
  const out = new Set(file.startsWith(`${e2e}scenarios/`) ? groupOf(name) : []);
  let files = [];
  try { files = fs.readdirSync(SCENARIOS).filter((f) => f.endsWith(".mjs")); } catch {}
  for (const f of files) {
    const src = fs.readFileSync(path.join(SCENARIOS, f), "utf8");
    if (new RegExp(`from "\\.\\.?/${name.replace(/\./g, "\\.")}"`).test(src)) groupOf(f).forEach((g) => out.add(g));
  }
  return [...out];
}

// A changed line naming a guide anchor can break a tour whatever file it is in
// (docs/dev/onboarding.md).
const ANCHOR = /data-guide|data-tour/;

// { groups: [id], reasons: {id: [file]}, all: file | null } for these changes;
// `lines(file)` gives the file's changed lines (for the anchor rule).
export function selectGroups(files, lines = () => []) {
  const reasons = {};
  let all = null;
  const add = (id, file) => { (reasons[id] ||= []).push(file); };
  for (const file of files) {
    const { groups } = ruleFor(file);
    if (groups === ALL) { all ||= file; continue; }
    for (const id of groups) add(id, file);
    for (const id of scenarioImporters(file)) add(id, file);
    if (file.startsWith("frontend/src/") && lines(file).some((l) => ANCHOR.test(l))) {
      for (const id of GUIDES) add(id, `${file} (guide anchor)`);
    }
  }
  const ids = all ? GROUPS.map((g) => g.id) : GROUPS.map((g) => g.id).filter((id) => reasons[id]);
  return { groups: ids, reasons, all };
}

const git = (...args) => execFileSync("git", args, { cwd: ROOT, encoding: "utf8", maxBuffer: 64 << 20, stdio: ["ignore", "pipe", "pipe"] });

// What changed: the working tree (staged or not) plus untracked files, against
// HEAD, or against where this branch left `ref` (`--changed main`).
export function changedFromGit(ref) {
  const base = ref ? git("merge-base", ref, "HEAD").trim() : "HEAD";
  const tracked = git("diff", "--name-only", base).split("\n").filter(Boolean);
  const untracked = git("ls-files", "--others", "--exclude-standard").split("\n").filter(Boolean);
  const files = [...new Set([...tracked, ...untracked])];
  const untrackedSet = new Set(untracked);
  const lines = (file) => {
    try {
      if (untrackedSet.has(file)) return fs.readFileSync(path.join(ROOT, file), "utf8").split("\n");
      return git("diff", "-U0", base, "--", file).split("\n").filter((l) => /^[+-][^+-]/.test(l));
    } catch { return []; }
  };
  return { base, files, lines };
}
