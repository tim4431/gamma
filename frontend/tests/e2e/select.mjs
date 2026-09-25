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

// Longest first (seconds on one worker, roughly), so the long ones never
// start last. A group must not need another group's data: scenarios that
// hand data along share a group (notes → pdf → share).
export const GROUPS = [
  { id: "guide", files: ["guide.mjs"] }, // ~55
  { id: "settings", files: ["settings.mjs"] }, // ~45
  { id: "notes-pdf-share", files: ["notes.mjs", "pdf.mjs", "share.mjs"] }, // ~40
  { id: "contextual-guide", files: ["contextualGuide.mjs"] }, // ~27
  { id: "triggered-guide", files: ["triggeredGuide.mjs"] }, // ~27
  { id: "ink-editing", files: ["inkEditing.mjs"] }, // ~22
  { id: "pdf-load", files: ["pdfload.mjs"] }, // ~20
  { id: "ink", files: ["ink.mjs"] }, // ~19
  { id: "pdf-touch", files: ["pdfTouch.mjs"] }, // ~15
  { id: "publish", files: ["publish.mjs"] }, // ~15
  { id: "chat-navigation", files: ["chatNavigation.mjs"] }, // ~12
  { id: "mentions", files: ["mentions.mjs"] }, // ~11
  { id: "collab", files: ["collab.mjs"] }, // ~11
  { id: "mirror", files: ["mirror.mjs"] }, // ~9
  { id: "transfers", files: ["transfers.mjs"] }, // ~9
  { id: "mcp", files: ["mcp.mjs"] }, // ~8
  { id: "files", files: ["files.mjs"] }, // ~7
  { id: "mermaid", files: ["mermaid.mjs"] }, // ~6
  { id: "i18n", files: ["i18n.mjs"] }, // ~6
  { id: "cloud-sign-in", files: ["cloudSignIn.mjs"] },
  { id: "auth", files: ["auth.mjs"] },
  { id: "quick-open", files: ["quickOpen.mjs"] },
  { id: "ipad", files: ["ipad.mjs"] },
];

const ALL = "all";
const GUIDES = ["guide", "contextual-guide", "triggered-guide"];
const CHAT = ["chat-navigation", "mentions", "contextual-guide", "triggered-guide", "notes-pdf-share"];
// every group that opens a PDF in the viewer
const PDF_VIEW = ["notes-pdf-share", "pdf-load", "pdf-touch", "ink", "ink-editing", "collab", "files", "transfers", ...GUIDES];
const EXPORTS = ["transfers", "files", "ink", "mermaid"];

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
  ["frontend/src/ink/**", ["ink", "ink-editing", "pdf-touch", "triggered-guide"]],
  ["frontend/src/pdf/**", PDF_VIEW],
  ["frontend/src/editor/**", ["notes-pdf-share", "mermaid", "files", "collab", "transfers", "mcp", "guide", "triggered-guide"]],
  ["frontend/src/chat/**", CHAT],
  ["frontend/src/settings/**", ["settings", "i18n", "mirror", "cloud-sign-in", "mcp", "publish", "triggered-guide"]],
  ["frontend/src/collaboration/MirrorPopover.jsx", ["mirror", "publish"]],
  ["frontend/src/collaboration/MergeResolver.jsx", ["mirror"]],
  ["frontend/src/collaboration/**", ALL], // the page's live session carries every edit
  ["frontend/src/library/QuickOpen.jsx", ["quick-open"]],
  ["frontend/src/library/**", ALL], // the home view every scenario opens on
  ["frontend/src/search/**", ["notes-pdf-share", "quick-open"]],
  ["frontend/src/sharing/**", ["notes-pdf-share", "publish", "collab"]],
  ["frontend/src/transfers/**", EXPORTS],
  ["frontend/src/support/**", ["settings"]],
  ["frontend/src/auth/**", ["auth", "cloud-sign-in", "publish"]],
  ["frontend/public/**", ["ipad"]],
  ["frontend/index.html", ALL],
  ["frontend/vite.config.js", ALL],
  ["frontend/package.json", ALL],
  ["frontend/package-lock.json", ALL],

  ["backend/tests/**", []],
  ["backend/gamma/ai_protocols/**", [...CHAT, "settings"]],
  ["backend/gamma/ai_*.py", [...CHAT, "settings"]],
  ["backend/gamma/chatgpt_oauth.py", ["settings"]],
  ["backend/gamma/routers/ai.py", [...CHAT, "settings"]],
  ["backend/gamma/routers/chats.py", CHAT],
  ["backend/gamma/mcp_*", ["mcp"]],
  ["backend/gamma/integrations.py", ["mcp", "mirror", "settings"]],
  ["backend/gamma/routers/integrations.py", ["mcp", "mirror", "settings"]],
  ["backend/gamma/sync_*.py", ["mirror", "publish"]],
  ["backend/gamma/textmerge.py", ["mirror"]],
  ["backend/gamma/routers/sync.py", ["mirror", "publish"]],
  ["backend/gamma/routers/mirrors.py", ["mirror", "publish"]],
  ["backend/gamma/cloud_*.py", ["cloud-sign-in", "publish"]],
  ["backend/gamma/routers/cloud_auth.py", ["cloud-sign-in", "publish"]],
  ["backend/gamma/publish.py", ["publish"]],
  ["backend/gamma/routers/publish.py", ["publish"]],
  ["backend/gamma/ink.py", ["ink", "ink-editing", "triggered-guide"]],
  ["backend/gamma/routers/ink.py", ["ink", "ink-editing", "triggered-guide"]],
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
  ["backend/gamma/routers/shares.py", ["notes-pdf-share", "publish", "collab"]],
  ["backend/gamma/routers/links.py", ["notes-pdf-share", "guide"]],
  ["backend/gamma/routers/metadata.py", ["notes-pdf-share", "guide"]],
  ["backend/gamma/translate_engines.py", ["settings", "notes-pdf-share"]],
  ["backend/gamma/foldertags.py", ["notes-pdf-share", "files"]],
  ["backend/gamma/publisher_sessions.py", ["settings"]],
  ["backend/gamma/routers/publisher_sessions.py", ["settings"]],
  ["backend/gamma/routers/clip.py", []], // the browser extension's ingest
  // the core every scenario goes through: app, auth, db, migrations, workspaces, blocks, ops, uploads, prefs, collab …
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
