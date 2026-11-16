#!/usr/bin/env node
// Checks the relative links of the repository's Markdown: every link to a
// file lands on one, and every #anchor on a heading of its target, by
// GitHub's heading ids (the website's build uses the same rule, sites/build.mjs).
// The website's own check (`node sites/build.mjs --strict`) covers only the
// pages it renders; this one covers docs/dev, docs/research, the skills and
// every other tracked .md file.
//
//   node tools/check_doc_links.mjs                 # every tracked .md file
//   node tools/check_doc_links.mjs docs/dev/ai.md  # the links of these files only
//
// Exits 1 when a link is broken, listing each as file:line.
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
// Files whose links resolve somewhere else: the backend's test inputs (vaults
// and exports whose links are the case under test) and the marketplace
// README, which tools/package_plugins.py puts at another repository's root.
const ELSEWHERE = [/^backend\/tests\//, /^tools\/marketplace-README\.md$/];
const tracked = execFileSync('git', ['ls-files', '-z', '*.md'], { cwd: ROOT, encoding: 'utf8' })
  .split('\0').filter(f => f && !ELSEWHERE.some(re => re.test(f)));

// A line with its code spans blanked out. A span opens on a run of backticks
// and closes on the next run of the same length (CommonMark), so ``` ``` ```
// is one span.
const withoutCode = line => {
  let out = '';
  let i = 0;
  while (i < line.length) {
    if (line[i] !== '`') { out += line[i++]; continue; }
    const run = line.slice(i).match(/^`+/)[0];
    let j = i + run.length;
    let close = -1;
    while (j < line.length) {
      const next = line.indexOf('`', j);
      if (next < 0) break;
      const r = line.slice(next).match(/^`+/)[0];
      if (r.length === run.length) { close = next; break; }
      j = next + r.length;
    }
    if (close < 0) { out += run; i += run.length; continue; }
    i = close + run.length;
  }
  return out;
};

// Lines outside fenced code blocks, without their code spans, so a link
// written as an example is not read as one.
const proseLines = text => {
  let fence = null;
  return text.split('\n').map(line => {
    const m = line.match(/^\s*(`{3,}|~{3,})/);
    if (m) {
      if (!fence) fence = m[1][0];
      else if (m[1][0] === fence) fence = null;
      return '';
    }
    return fence ? '' : withoutCode(line);
  });
};

const slugger = () => {
  const used = new Map();
  return text => {
    const base = text.trim().toLowerCase().replace(/<[^>]+>/g, '').replace(/[^\p{L}\p{N}\p{M}\s_-]/gu, '').replace(/\s/g, '-');
    const n = used.get(base) ?? 0;
    used.set(base, n + 1);
    return n ? `${base}-${n}` : base;
  };
};

const anchorsOf = new Map();
const anchors = file => {
  if (anchorsOf.has(file)) return anchorsOf.get(file);
  const slug = slugger();
  const ids = new Set();
  let fence = null;
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    const f = line.match(/^\s*(`{3,}|~{3,})/);
    if (f) { if (!fence) fence = f[1][0]; else if (f[1][0] === fence) fence = null; continue; }
    if (fence) continue;
    const h = line.match(/^#{1,6}\s+(.*?)\s*#*\s*$/);
    if (!h) continue;
    const text = h[1]
      .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')   // a link or picture: its text
      .replace(/`([^`]*)`/g, '$1')                 // inline code: its content
      .replace(/(\*\*|__|\*)/g, '');               // emphasis markers
    ids.add(slug(text));
  }
  for (const [, id] of fs.readFileSync(file, 'utf8').matchAll(/<a\s+(?:name|id)="([^"]+)"/g)) ids.add(id);
  anchorsOf.set(file, ids);
  return ids;
};

const only = process.argv.slice(2).map(p => path.relative(ROOT, path.resolve(p)).split(path.sep).join('/'));
const files = only.length ? only : tracked;
const broken = [];
for (const rel of files) {
  const abs = path.join(ROOT, rel);
  if (!fs.existsSync(abs)) { broken.push(`${rel}: no such file`); continue; }
  proseLines(fs.readFileSync(abs, 'utf8')).forEach((line, i) => {
    for (const [, href] of line.matchAll(/\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)/g)) {
      if (/^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith('//')) continue;  // http:, mailto:, …
      const [, p, hash] = href.match(/^([^#]*)(?:#(.*))?$/);
      const target = p ? path.resolve(path.dirname(abs), decodeURI(p)) : abs;
      const where = `${rel}:${i + 1}`;
      if (!fs.existsSync(target)) { broken.push(`${where}: no such file: ${href}`); continue; }
      if (hash && target.endsWith('.md') && !anchors(target).has(decodeURIComponent(hash).toLowerCase())) {
        broken.push(`${where}: no such heading: ${href}`);
      }
    }
  });
}
for (const b of broken) console.log(b);
console.log(`${files.length} files read, ${broken.length} broken link${broken.length === 1 ? '' : 's'}`);
process.exit(broken.length ? 1 : 0);
