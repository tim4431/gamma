// Assemble ./dist for `wrangler deploy`:
//   ./site/**            copied as-is, `<!--#include name -->` expanded from ./templates
//                        and `<!--#repofile path -->` from the repository (below);
//                        site/media holds the front page's cuts of two demos and the
//                        stills of its clips, from tools/readme-media/site-cuts.py
//   ./dist/media/**      brand artwork, demos, the screenshot, the badges, the favicon and the Inter font
//                        with its licence, copied from the repository (the site keeps no
//                        copies), plus any picture a rendered document shows
//   ./dist/<page>/       the repository's Markdown rendered through ./templates:
//                        the privacy policy, the terms, the page on deleting a Gamma
//                        Cloud account, the user guide and the design pages (DOCS below)
//   ./dist/sitemap.xml   every page above
//
// `node build.mjs --strict` fails on a broken internal link, picture or anchor,
// or a diagram wider than the page's column (CI does this); without the flag
// they are warnings.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Marked } from 'marked';
import { renderMermaidSVG } from 'beautiful-mermaid';

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
  'testflight-ipad.svg': 'docs/assets/badges/testflight-ipad.svg',
  'chrome-web-store.png': 'docs/assets/badges/chrome-web-store.png',
  'demo-native-agentic.webp': 'docs/assets/demos/demo-native-agentic.webp',
  'inter.woff2': 'desktop/ui/fonts/inter-latin-wght-normal.woff2',
  'inter-OFL.txt': 'desktop/ui/fonts/OFL.txt',
};

// The Markdown pages. `source` is a .md file under the repository root; `at`
// is where it lands on the site. Everything else comes from the Markdown: the
// title is the first heading, the description the first paragraph. A `section`
// puts the page in the documentation sidebar, with its headings; without one
// the page stands alone (the privacy policy, the terms, and deleting a Gamma
// Cloud account, which the apps and the store listings link to). The design
// pages are a section of their own (designPages below).
const DOCS = [
  { source: 'PRIVACY.md', at: 'privacy/' },
  { source: 'TERMS.md', at: 'terms/' },
  { source: 'DELETE_ACCOUNT.md', at: 'delete-account/' },
  { source: 'docs/user_guide/user_guide.md', at: 'docs/', section: 'User guide' },
  ...designPages(),
];

// docs/design/README.md, then each page its table of pages links, in that
// order. A page of docs/design/ the table leaves out, or a link to no page,
// fails the build: the table is where a new page is listed.
function designPages() {
  const dir = path.join(ROOT, 'docs', 'design');
  const listed = [...new Set([...fs.readFileSync(path.join(dir, 'README.md'), 'utf8').matchAll(/^\| \[[^\]]+\]\(([\w-]+)\.md\)/gm)].map(m => m[1]))];
  const files = fs.readdirSync(dir).filter(f => f.endsWith('.md') && f !== 'README.md').map(f => f.slice(0, -3));
  const unlisted = files.filter(f => !listed.includes(f)), missing = listed.filter(f => !files.includes(f));
  if (unlisted.length || missing.length) {
    throw new Error(`docs/design/README.md's table of pages disagrees with the folder: ${[...unlisted.map(f => `${f}.md not listed`), ...missing.map(f => `${f}.md missing`)].join(', ')}`);
  }
  return [{ source: 'docs/design/README.md', at: 'docs/design/', section: 'Design' },
    ...listed.map(name => ({ source: `docs/design/${name}.md`, at: `docs/design/${name}/`, section: 'Design' }))];
}

const IMAGE = /\.(png|jpe?g|gif|webp|svg|avif)$/i;
const warnings = [];
const warn = msg => warnings.push(msg);

const template = name => fs.readFileSync(path.join(TEMPLATES, `${name}.html`), 'utf8');
const expand = html => html
  .replace(/<!--#include (\w+) -->/g, (_, name) => expand(template(name)))
  .replace(/<!--#repofile (\S+) -->/g, (_, file) => repofile(file));
const write = (rel, content) => {
  const out = path.join(DIST, rel);
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, content);
};
const walk = dir => fs.readdirSync(dir, { withFileTypes: true, recursive: true })
  .filter(entry => entry.isFile())
  .map(entry => path.join(entry.parentPath ?? entry.path, entry.name));
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
// A file of the repository shown on a page, so the page cannot drift from it.
// Line ends as git holds them, whatever the checkout did to them.
const repofile = file => {
  const from = path.join(ROOT, file);
  if (!fs.existsSync(from)) throw new Error(`Missing repository file: ${file}`);
  return esc(fs.readFileSync(from, 'utf8').replace(/\r\n/g, '\n'));
};
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
// The user guide's interactive demos (docs/user_guide/demos/): published
// as they are under /docs/demos/, where the guide's links to them land.
const DEMOS = 'docs/user_guide/demos';
if (fs.existsSync(path.join(ROOT, DEMOS))) {
  for (const name of fs.readdirSync(path.join(ROOT, DEMOS))) {
    write(path.join('docs', 'demos', name), fs.readFileSync(path.join(ROOT, DEMOS, name)));
  }
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
const pages = DOCS.map(doc => {
  if (!fs.existsSync(path.join(ROOT, doc.source))) throw new Error(`Missing document: ${doc.source}`);
  return { source: doc.source, url: `/${doc.at}`, rel: `${doc.at}index.html`, section: doc.section };
});
const bySource = new Map(pages.map(p => [p.source, p]));

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
    const published = bySource.get(target);
    if (published) return published.url + hash;
    const abs = path.join(ROOT, target);
    if (!fs.existsSync(abs)) {
      warn(`${page.source}: link to a file that does not exist: ${href}`);
      return `${REPO}/blob/${BRANCH}/${target}${hash}`;
    }
    if (fs.statSync(abs).isDirectory()) return `${REPO}/tree/${BRANCH}/${target}`;
    if (IMAGE.test(target)) return asset(target);
    if (target.startsWith(`${DEMOS}/`)) return `/docs/demos/${path.posix.basename(target)}${hash}`;
    return `${REPO}/blob/${BRANCH}/${target}${hash}`;
  };
};

// A ```mermaid fence. GitHub draws it itself; here it becomes an inline SVG at
// build time (beautiful-mermaid: a layout in plain JavaScript, no browser), so
// the page runs no script for it and fetches nothing. The drawing's colours
// are CSS variables, renamed to --dg-* so the site's own --bg, --line, --accent
// … cannot leak in, and set from the site's tokens by `.diagram` in
// styles.css, so a diagram follows light and dark. Its Google Fonts import is
// dropped for the page's own faces, its ids are made unique on the page, and
// an accTitle / accDescr line (which GitHub reads too) labels it. A diagram
// that does not render fails the build; one wider than the column is a warning
// (an error under --strict), since it would be shrunk until it reads badly.
const DIAGRAM_WIDTH = 720; // .docs__main's 820px less the figure's padding
let diagrams = 0;
const diagram = (source, page) => {
  const acc = key => source.match(new RegExp(`^\\s*${key}\\s*:\\s*(.+)$`, 'm'))?.[1].trim();
  const [title, descr] = [acc('accTitle'), acc('accDescr')];
  let svg;
  try {
    svg = renderMermaidSVG(source.replace(/^\s*acc(Title|Descr)\s*:.*$/gm, ''), { transparent: true, padding: 12 });
  } catch (e) {
    throw new Error(`${page.source}: a mermaid diagram does not render: ${e.message}`);
  }
  const prefix = `d${++diagrams}-`;
  for (const id of new Set([...svg.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]))) {
    svg = svg.replaceAll(`id="${id}"`, `id="${prefix}${id}"`).replaceAll(`url(#${id})`, `url(#${prefix}${id})`).replaceAll(`href="#${id}"`, `href="#${prefix}${id}"`);
  }
  svg = svg
    .replace(/^\s*@import url\([^)]*\);\s*$/gm, '')
    .replace(/font-family: 'Inter'[^;]*;/g, 'font-family: var(--font);')
    .replace(/font-family: 'JetBrains Mono'[^;]*;/g, 'font-family: var(--mono);')
    .replace(/--(bg|fg|line|accent|muted|surface|border)(?![\w-])/g, '--dg-$1')
    .replace(/^<svg\b[^>]*>/, tag => tag.replace(/\sstyle="[^"]*"/, '')
      .replace('<svg', `<svg role="img"${title ? ` aria-label="${esc(descr ? `${title}: ${descr}` : title)}"` : ''}`));
  const width = Number(svg.match(/^<svg\b[^>]*\swidth="([\d.]+)"/)?.[1]);
  if (width > DIAGRAM_WIDTH) warn(`${page.source}: diagram ${title ? `"${title}" ` : ''}is ${Math.round(width)}px wide, more than the column's ${DIAGRAM_WIDTH}px`);
  // On a narrow screen it scrolls sideways rather than shrinking below 75 %.
  return `<figure class="diagram"><div class="diagram__scroll">${svg.replace(/^<svg\b/, `<svg style="min-width:${Math.round(width * 0.75)}px"`)}</div></figure>\n`;
};

// GitHub's alerts, `> [!NOTE]` … `> [!CAUTION]`, as callouts.
const CALLOUTS = { NOTE: 'Note', TIP: 'Tip', IMPORTANT: 'Important', WARNING: 'Warning', CAUTION: 'Caution' };

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
        const language = lang?.split(/\s/)[0];
        if (language === 'mermaid') return diagram(text, page);
        // A `text` fence is a picture made of characters (a tree, a layout),
        // not something to copy and run: a light box with no Copy button.
        if (language === 'text') return `<div class="code code--figure"><pre><code class="language-text">${esc(text)}\n</code></pre></div>\n`;
        const cls = language ? ` class="language-${esc(language)}"` : '';
        return `<div class="code"><pre><code${cls}>${esc(text)}\n</code></pre><button class="copy" type="button" data-copy>Copy</button></div>\n`;
      },
      blockquote({ tokens }) {
        const html = this.parser.parse(tokens);
        const m = html.match(/^<p>\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*/);
        if (!m) return `<blockquote>\n${html}</blockquote>\n`;
        const kind = m[1].toLowerCase();
        const body = html.slice(m[0].length).replace(/^<\/p>\s*/, '');
        return `<div class="callout callout--${kind}"><p class="callout__title">${CALLOUTS[m[1]]}</p>\n${body.startsWith('<') ? body : `<p>${body}`}</div>\n`;
      },
      // A table whose every row starts with a link to a page of the site, and
      // says what it is in one more cell, is a list of pages: shown as cards.
      table({ header, rows }) {
        if (header.length !== 2 || !rows.length) return false;
        const cards = rows.map(([first, second]) => {
          const parts = first.tokens.filter(t => !(t.type === 'text' && !t.text.trim()));
          if (parts.length !== 1 || parts[0].type !== 'link') return null;
          const href = resolve(parts[0].href);
          return pages.some(p => p.url === href) ? { href, title: plain(parts[0].tokens), text: plain(second.tokens) } : null;
        });
        if (cards.some(c => !c)) return false;
        return `<ul class="cards">\n${cards.map(c => `<li><a href="${esc(c.href)}"><strong>${esc(c.title)}</strong><span>${esc(c.text)}</span></a></li>`).join('\n')}\n</ul>\n`;
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
};
pages.forEach(render);

// The sidebar: each section's pages, the current one with its headings.
const sections = [...new Set(DOCS.map(d => d.section).filter(Boolean))]
  .map(section => ({ section, pages: pages.filter(p => p.section === section) }));
const nav = page => `<details class="docnav" open>
<summary>Documentation</summary>
<nav aria-label="Documentation">
${sections.map(s => `<details class="docnav__section"${s.section === page.section ? ' open' : ''}>
<summary>${esc(s.section)}</summary>
<ul>
${s.pages.map(p => {
    const current = p === page;
    const toc = current && page.headings.some(h => h.depth === 2)
      ? `\n<ol class="docnav__toc">\n${page.headings.filter(h => h.depth === 2).map(h => `<li><a href="#${h.id}">${esc(h.text)}</a></li>`).join('\n')}\n</ol>`
      : '';
    return `<li><a href="${p.url}"${current ? ' aria-current="page"' : ''}>${esc(p.title)}</a>${toc}</li>`;
  }).join('\n')}
</ul>
</details>`).join('\n')}
</nav>
</details>`;

// Beside the page on a wide screen: its headings, the section it is reading
// marked by site.js. Narrower, the sidebar's list of the page's sections
// stands in for it (styles.css shows one or the other).
const toc = page => {
  const items = [];
  for (const h of page.headings.filter(h => h.depth === 2 || h.depth === 3)) {
    if (h.depth === 2) items.push({ ...h, children: [] });
    else if (items.length) items.at(-1).children.push(h);
  }
  if (!items.length) return '';
  const link = h => `<a href="#${h.id}">${esc(h.text)}</a>`;
  return `<aside class="doctoc" aria-label="On this page">
<p class="doctoc__title">On this page</p>
<ol>
${items.map(h => `<li>${link(h)}${h.children.length ? `<ol>${h.children.map(c => `<li>${link(c)}</li>`).join('')}</ol>` : ''}</li>`).join('\n')}
</ol>
<a class="doctoc__top" href="#main">Back to top</a>
</aside>`;
};
// Docs › the section › this page, above the title of any page but the
// documentation's front page.
const crumbs = page => {
  if (page.url === '/docs/') return '';
  const first = pages.find(p => p.section === page.section);
  const trail = [`<a href="/docs/">Docs</a>`];
  if (first !== page) trail.push(`<a href="${first.url}">${esc(page.section)}</a>`);
  trail.push(`<span aria-current="page">${esc(first === page ? page.section : page.title)}</span>`);
  return `<nav class="crumbs" aria-label="Breadcrumb">${trail.join('<span class="crumbs__sep" aria-hidden="true">/</span>')}</nav>\n`;
};
// The pages before and after this one in its section.
const pager = page => {
  const list = pages.filter(p => p.section === page.section);
  const i = list.indexOf(page);
  const [prev, next] = [list[i - 1], list[i + 1]];
  if (!prev && !next) return '';
  const card = (p, cls, label) => p ? `<a class="pager__${cls}" href="${p.url}"><span>${label}</span>${esc(p.title)}</a>` : '<span></span>';
  return `<nav class="pager" aria-label="${esc(page.section)} pages">${card(prev, 'prev', 'Previous')}${card(next, 'next', 'Next')}</nav>\n`;
};

for (const page of pages) {
  const html = expand(template(page.section ? 'doc' : 'page'))
    .replaceAll('{{title}}', esc(page.title))
    .replaceAll('{{description}}', esc(page.description))
    .replaceAll('{{path}}', page.url)
    .replace('{{nav}}', () => (page.section ? nav(page) : ''))
    .replace('{{crumbs}}', () => (page.section ? crumbs(page) : ''))
    .replace('{{toc}}', () => (page.section ? toc(page) : ''))
    .replace('{{pager}}', () => (page.section ? pager(page) : ''))
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

// 5. Every internal link, picture and clip in every page lands on a page or a
// file, and a link's anchor on an id in that page.
const idsByUrl = new Map(htmlFiles.map(f => [urlOf(f), new Set([...fs.readFileSync(f, 'utf8').matchAll(/\bid="([^"]+)"/g)].map(m => m[1]))]));
const exists = url => {
  if (urls.includes(url)) return true;
  const rel = url.slice(1);
  return rel && fs.existsSync(path.join(DIST, rel)) && fs.statSync(path.join(DIST, rel)).isFile();
};
for (const f of htmlFiles) {
  const from = urlOf(f);
  const html = fs.readFileSync(f, 'utf8');
  for (const [, href] of html.matchAll(/\b(?:href|src|srcset|data-clip)="([^"]+)"/g)) {
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
