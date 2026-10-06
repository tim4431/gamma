// Assemble ./dist for `wrangler deploy`:
//   ./site/**            copied as-is, `<!--#include name -->` expanded from ./templates
//   ./dist/media/**      brand artwork, demos, the screenshot and the favicon,
//                        copied from the repository (the site keeps no copies),
//                        plus any picture a rendered document shows
//   ./dist/<page>/       the repository's Markdown rendered through ./templates:
//                        the privacy policy, the terms and the user guide (DOCS below)
//   ./dist/sitemap.xml   every page above
//
// `node build.mjs --strict` fails on a broken internal link or anchor
// (CI does this); without the flag they are warnings.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Marked } from 'marked';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const DIST = path.join(HERE, 'dist');
const SITE = path.join(HERE, 'site');
const TEMPLATES = path.join(HERE, 'templates');
const STRICT = process.argv.includes('--strict');

const ORIGIN = 'https://gammapdf.com';
const REPO = 'https://github.com/tim4431/Gamma';
const BRANCH = 'main';

// Destination (under dist/media) → source (under the repository root).
const MEDIA = {
  'favicon.svg': 'frontend/public/media/icons/favicon.svg',
  'logo.svg': 'docs/assets/branding/gamma-logo.svg',
  'app.webp': 'docs/assets/screenshots/hero-app.webp',
  'hero-light.png': 'docs/assets/branding/gamma-hero-light.png',
  'library-light.svg': 'docs/assets/branding/gamma-library-light.svg',
  'connections-light.svg': 'docs/assets/branding/gamma-connections-light.svg',
  'workspaces-light.svg': 'docs/assets/branding/gamma-workspaces-light.svg',
  'anywhere-light.svg': 'docs/assets/branding/gamma-anywhere-light.svg',
  'demo-annotate-and-ink.webp': 'docs/assets/demos/demo-annotate-and-ink.webp',
  'demo-notes.webp': 'docs/assets/demos/demo-notes.webp',
  'demo-native-agentic.webp': 'docs/assets/demos/demo-native-agentic.webp',
  'demo-agentic-notes.webp': 'docs/assets/demos/demo-agentic-notes.webp',
  'demo-search.webp': 'docs/assets/demos/demo-search.webp',
  'demo-reference-links.webp': 'docs/assets/demos/demo-reference-links.webp',
  'demo-metadata.webp': 'docs/assets/demos/demo-metadata.webp',
  'demo-connector.webp': 'docs/assets/demos/demo-connector.webp',
  'demo-collab.webp': 'docs/assets/demos/demo-collab.webp',
};

// The Markdown pages. `source` is a file or a directory of .md files under the
// repository root; `at` is where it lands on the site. A directory's README.md
// becomes its index and every other file a folder of its own name
// (docs/dev/mcp.md → /docs/dev/mcp/). Everything else comes from the Markdown:
// the title is the first heading, the description the first paragraph, the
// sidebar's order and labels are the links of the section's index page, in
// order of appearance. A `section` puts the pages in the documentation sidebar;
// without one the page stands alone (the privacy policy, the terms).
const DOCS = [
  { source: 'PRIVACY.md', at: 'privacy/' },
  { source: 'TERMS.md', at: 'terms/' },
  { source: 'docs/user_guide/user_guide.md', at: 'docs/', section: 'User guide' },
];

const IMAGE = /\.(png|jpe?g|gif|webp|svg|avif)$/i;
const warnings = [];
const warn = msg => warnings.push(msg);

const template = name => fs.readFileSync(path.join(TEMPLATES, `${name}.html`), 'utf8');
const expand = html => html.replace(/<!--#include (\w+) -->/g, (_, name) => expand(template(name)));
const write = (rel, content) => {
  const out = path.join(DIST, rel);
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, content);
};
const walk = dir => fs.readdirSync(dir, { withFileTypes: true, recursive: true })
  .filter(entry => entry.isFile())
  .map(entry => path.join(entry.parentPath ?? entry.path, entry.name));
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const unescapeHtml = s => s.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'");
const posix = p => p.split(path.sep).join('/');

fs.rmSync(DIST, { recursive: true, force: true });

// 1. The hand-written pages and their static files.
for (const abs of walk(SITE)) {
  const rel = path.relative(SITE, abs);
  if (abs.endsWith('.html')) write(rel, expand(fs.readFileSync(abs, 'utf8')));
  else write(rel, fs.readFileSync(abs));
}

// 2. The artwork the pages use.
const mediaBySource = new Map(Object.entries(MEDIA).map(([dest, src]) => [src, `/media/${dest}`]));
for (const [dest, src] of Object.entries(MEDIA)) {
  const from = path.join(ROOT, src);
  if (!fs.existsSync(from)) throw new Error(`Missing repository asset: ${src}`);
  write(path.join('media', dest), fs.readFileSync(from));
}
// A picture a document shows: an existing copy above, else copied under its
// repository path.
const asset = repoPath => {
  if (mediaBySource.has(repoPath)) return mediaBySource.get(repoPath);
  const url = `/media/${repoPath}`;
  mediaBySource.set(repoPath, url);
  write(path.join('media', repoPath), fs.readFileSync(path.join(ROOT, repoPath)));
  return url;
};

// 3. The Markdown pages: first the list, so links between them can be resolved.
const pages = [];
for (const doc of DOCS) {
  const abs = path.join(ROOT, doc.source);
  if (!fs.existsSync(abs)) throw new Error(`Missing document: ${doc.source}`);
  const files = fs.statSync(abs).isDirectory()
    ? fs.readdirSync(abs).filter(f => f.endsWith('.md')).sort().map(f => `${doc.source}/${f}`)
    : [doc.source];
  for (const source of files) {
    const name = path.posix.basename(source, '.md');
    const index = files.length === 1 || name === 'README';
    const url = index ? `/${doc.at}` : `/${doc.at}${name}/`;
    pages.push({ source, url, rel: `${url.slice(1)}index.html`, section: doc.section, index });
  }
}
const bySource = new Map(pages.map(p => [p.source, p]));
const byUrl = new Map(pages.map(p => [p.url, p]));

// GitHub's heading slugs, so links written for the repository keep working.
const slugger = () => {
  const used = new Map();
  return text => {
    const base = text.trim().toLowerCase().replace(/<[^>]+>/g, '').replace(/[^\p{L}\p{N}\p{M}\s_-]/gu, '').replace(/\s/g, '-');
    const n = used.get(base) ?? 0;
    used.set(base, n + 1);
    return n ? `${base}-${n}` : base;
  };
};
const plain = tokens => tokens.map(t => {
  if (t.type === 'codespan') return unescapeHtml(t.text);
  if (t.tokens) return plain(t.tokens);
  if (t.type === 'html') return t.text.replace(/<[^>]+>/g, '');
  return t.text ?? t.raw ?? '';
}).join('');

// Resolve a document's relative link: a published document → its page, a
// picture → a copy under /media, anything else in the repository → GitHub.
const resolver = page => {
  const dir = path.posix.dirname(page.source);
  return href => {
    if (/^(#|[a-z][a-z0-9+.-]*:|\/)/i.test(href)) return href;
    const m = href.match(/^([^#?]*)(.*)$/);
    const [rel, hash] = [m[1], m[2]];
    const target = path.posix.normalize(path.posix.join(dir, decodeURI(rel))).replace(/\/$/, '');
    const published = bySource.get(target) ?? bySource.get(`${target}/README.md`);
    if (published) return published.url + hash;
    const abs = path.join(ROOT, target);
    if (!fs.existsSync(abs)) {
      warn(`${page.source}: link to a file that does not exist: ${href}`);
      return `${REPO}/blob/${BRANCH}/${target}${hash}`;
    }
    if (fs.statSync(abs).isDirectory()) return `${REPO}/tree/${BRANCH}/${target}`;
    if (IMAGE.test(target)) return asset(target);
    return `${REPO}/blob/${BRANCH}/${target}${hash}`;
  };
};

const render = page => {
  const md = fs.readFileSync(path.join(ROOT, page.source), 'utf8');
  const resolve = resolver(page);
  const slug = slugger();
  page.headings = [];
  const marked = new Marked({
    gfm: true,
    renderer: {
      heading({ tokens, depth }) {
        const text = plain(tokens);
        const id = slug(text);
        page.headings.push({ depth, id, text });
        const anchor = depth > 1 ? `<a class="anchor" href="#${id}" aria-label="Link to this section">#</a>` : '';
        return `<h${depth} id="${id}">${this.parser.parseInline(tokens)}${anchor}</h${depth}>\n`;
      },
      code({ text, lang }) {
        const cls = lang ? ` class="language-${esc(lang.split(/\s/)[0])}"` : '';
        return `<div class="code"><pre><code${cls}>${esc(text)}\n</code></pre><button class="copy" type="button" data-copy>Copy</button></div>\n`;
      },
      link({ href, title, tokens }) {
        const t = title ? ` title="${esc(title)}"` : '';
        return `<a href="${esc(resolve(href))}"${t}>${this.parser.parseInline(tokens)}</a>`;
      },
      image({ href, title, text }) {
        const t = title ? ` title="${esc(title)}"` : '';
        return `<img src="${esc(resolve(href))}" alt="${esc(text)}"${t}>`;
      },
      html({ text }) {
        // Raw HTML in the Markdown: its pictures are resolved too.
        return text.replace(/(<img\b[^>]*\bsrc=")([^"]+)(")/g, (_, a, src, b) => a + esc(resolve(unescapeHtml(src))) + b);
      },
    },
  });
  const tokens = marked.lexer(md);
  const h1 = tokens.find(t => t.type === 'heading' && t.depth === 1);
  page.title = h1 ? plain(h1.tokens) : path.posix.basename(page.source, '.md');
  const para = tokens.find(t => t.type === 'paragraph' && plain(t.tokens).replace(/\s+/g, ' ').trim().length >= 60);
  const text = para ? plain(para.tokens).replace(/\s+/g, ' ').trim() : page.title;
  page.description = text.length > 160 ? `${text.slice(0, 157).replace(/\s+\S*$/, '')}…` : text;
  page.html = marked.parser(tokens).replace(/<table>[\s\S]*?<\/table>/g, m => `<div class="table">${m}</div>`);
  // The page's links, those in tables first: an index's topic table names
  // its pages better than a mention in passing does.
  const links = [];
  const collect = (list, inTable) => {
    for (const t of list ?? []) {
      if (t.type === 'link') links.push({ label: plain(t.tokens), href: t.href, inTable });
      if (t.type === 'table') {
        for (const cell of [...t.header, ...t.rows.flat()]) collect(cell.tokens, true);
      } else {
        collect(t.tokens, inTable);
        for (const item of t.items ?? []) collect(item.tokens, inTable);
      }
    }
  };
  collect(tokens, false);
  page.links = [...links.filter(l => l.inTable), ...links.filter(l => !l.inTable)];
};
pages.forEach(render);

// The sidebar: sections, each with its pages in the order its index links
// them (then the rest by title) and the current page's headings. The index
// page's link text is the label, unless it is just the file name.
const sections = DOCS.filter(d => d.section).map(doc => {
  const own = pages.filter(p => p.section === doc.section);
  const index = own.find(p => p.index);
  const resolve = resolver(index);
  const labels = new Map();
  const order = [];
  for (const { label, href } of index.links) {
    const target = byUrl.get(resolve(href).replace(/[#?].*$/, ''));
    if (!target || target.section !== doc.section || target === index || order.includes(target)) continue;
    order.push(target);
    if (!/\.md$/.test(label)) labels.set(target, label);
  }
  const rest = own.filter(p => p !== index && !order.includes(p)).sort((a, b) => a.title.localeCompare(b.title));
  return { ...doc, index, pages: [index, ...order, ...rest], labels };
});
const nav = page => `<details class="docnav" open>
<summary>Documentation</summary>
<nav aria-label="Documentation">
${sections.map(s => `<details class="docnav__section"${s.section === page.section ? ' open' : ''}>
<summary>${esc(s.section)}</summary>
<ul>
${s.pages.map(p => {
    const current = p === page;
    const label = p.index ? (s.pages.length > 1 ? 'Overview' : esc(p.title)) : esc(s.labels.get(p) ?? p.title);
    const toc = current && page.headings.some(h => h.depth === 2)
      ? `\n<ol class="docnav__toc">\n${page.headings.filter(h => h.depth === 2).map(h => `<li><a href="#${h.id}">${esc(h.text)}</a></li>`).join('\n')}\n</ol>`
      : '';
    return `<li><a href="${p.url}"${current ? ' aria-current="page"' : ''}>${label}</a>${toc}</li>`;
  }).join('\n')}
</ul>
</details>`).join('\n')}
</nav>
</details>`;

for (const page of pages) {
  const html = expand(template(page.section ? 'doc' : 'page'))
    .replaceAll('{{title}}', esc(page.title))
    .replaceAll('{{description}}', esc(page.description))
    .replaceAll('{{path}}', page.url)
    .replace('{{nav}}', () => (page.section ? nav(page) : ''))
    .replace('{{content}}', () => page.html)
    .replace('{{source}}', () => `Rendered from <a href="${REPO}/blob/${BRANCH}/${page.source}">${page.source}</a> in the Gamma repository.`);
  write(page.rel, html);
}

// 4. The sitemap: every page in dist/ except the 404.
const htmlFiles = walk(DIST).filter(f => f.endsWith('.html') && path.basename(f) !== '404.html');
const urlOf = f => {
  const rel = posix(path.relative(DIST, f));
  return rel === 'index.html' ? '/' : rel.endsWith('/index.html') ? `/${rel.slice(0, -'index.html'.length)}` : `/${rel.replace(/\.html$/, '')}`;
};
const urls = htmlFiles.map(urlOf).sort();
write('sitemap.xml', `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls.map(u => `  <url><loc>${ORIGIN}${u}</loc></url>`).join('\n')}\n</urlset>\n`);

// 5. Every internal link in every page lands on a page, and its anchor on an
// id in that page.
const idsByUrl = new Map(htmlFiles.map(f => [urlOf(f), new Set([...fs.readFileSync(f, 'utf8').matchAll(/\bid="([^"]+)"/g)].map(m => m[1]))]));
const exists = url => {
  if (urls.includes(url)) return true;
  const rel = url.slice(1);
  return rel && fs.existsSync(path.join(DIST, rel)) && fs.statSync(path.join(DIST, rel)).isFile();
};
for (const f of htmlFiles) {
  const from = urlOf(f);
  const html = fs.readFileSync(f, 'utf8');
  for (const [, href] of html.matchAll(/\bhref="([^"]+)"/g)) {
    if (!/^[/#]/.test(href) || href.startsWith('//')) continue;
    const [, p, hash] = href.match(/^([^#]*)(#.*)?$/);
    const target = p || from;
    if (p && !exists(target)) { warn(`${from}: link to a page that does not exist: ${href}`); continue; }
    if (hash && hash.length > 1 && idsByUrl.has(target) && !idsByUrl.get(target).has(decodeURIComponent(hash.slice(1)))) {
      warn(`${from}: anchor not found: ${href}`);
    }
  }
}

const out = walk(DIST);
const bytes = out.reduce((n, f) => n + fs.statSync(f).size, 0);
console.log(`dist/: ${out.length} files, ${(bytes / 1024 / 1024).toFixed(1)} MiB; ${pages.length} pages rendered from Markdown`);
for (const w of warnings) console.warn(`${STRICT ? 'error' : 'warning'}: ${w}`);
if (warnings.length && STRICT) process.exit(1);
