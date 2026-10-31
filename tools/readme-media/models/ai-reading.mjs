// The scripted words of the "Ask an AI about your papers" demo
// (record-native-agentic.mjs), on the curated atom-arrays paper (Bluvstein
// et al. 2022, Nature 604, 451). Gamma runs everything else for real: the
// figure the user boxed goes to the model as a picture, search_papers asks
// the registries, fetch_paper downloads and reads each paper, and the
// citations are links Gamma's viewer resolves against the PDF.
//
// Two questions:
//   - about the boxed figure: a short answer citing p. 2 (the Fig. 1
//     caption), no tools;
//   - "Read refs 3–5 and compare …": round 1 searches the three titles in one
//     turn (kind "article": OpenAlex and Crossref, not arXiv's rate-limited
//     search), round 2 fetches each paper by the source its search listed,
//     round 3 is the comparison table, each row citing its entry in the
//     reference list on p. 6.
// Before the table goes out, every fact it states about a paper is looked
// for in THAT paper's fetched text (and its year in the search listing);
// the result is logged (ctx.note: grounding) and with any fact missing the
// model says it could not read the paper instead, so the recorder refuses
// the take.
//
// ctx.args: { quotes: { fig, ref3, ref4, ref5 }: {page, text} } — verbatim
// pdf.js text of the curated PDF, which the recorder checks with Gamma's own
// matcher before the take; { dump }: a folder to write the tool results to.
import fs from 'node:fs';
import path from 'node:path';

export const pace = { cps: 120, firstToken: 600, toolPause: 450 };

export const ASKS_REFS = /\brefs?\.?\s*3\s*[–-]\s*5\b/i;

// References 3–5 of the paper, as its reference list (p. 6) gives them.
export const REFS = [
  { n: 3, who: 'Jaksch', year: 2000, title: 'Fast quantum gates for neutral atoms' },
  { n: 4, who: 'Urban', year: 2009, title: 'Observation of Rydberg blockade between two atoms' },
  { n: 5, who: 'Levine', year: 2019, title: 'Parallel implementation of high-fidelity multiqubit gates with neutral atoms' },
];

// What the table says about each paper, and the words of that paper's own
// text (whitespace folded) each statement rests on.
export const FACTS = {
  3: {
    row: 'Proposed fast two-qubit gates driven by the dipole–dipole interaction of atoms excited to Rydberg states',
    title: /Fast quantum gates for neutral atoms/i,
    claims: {
      'proposed fast two-qubit gates': /We propose several schemes for implementing a fast two-?\s?qubit quantum gate for neutral atoms/,
      'the dipole–dipole interaction of Rydberg atoms': /provided by the dipole-dipole interaction of atoms excited to low-lying Rydberg states/,
    },
  },
  4: {
    row: 'Saw the blockade: an atom excited to a Rydberg state blocks the excitation of a second atom more than 10 µm away',
    title: /Observation of Rydberg blockade between two atoms/i,
    claims: {
      'observed Rydberg blockade': /observation of Rydberg blockade between single neutral atoms/i,
      'blocks a second atom more than 10 µm away': /excited to the 79d5\/2 level blocks the subsequent excitation of a second atom located more than 10 ?[µμ]m away/,
    },
  },
  5: {
    row: 'Ran controlled-phase gates in parallel on several clusters of atoms, gate fidelity ≥ 97.4%',
    title: /Parallel implementation of high-?\s?fidelity multi-?\s?qubit gates/i,
    claims: {
      'controlled-phase gates': /we realize the controlled-phase gate/i,
      'in parallel on several clusters of atoms': /implemented in parallel on several clusters of atoms/,
      'gate fidelity ≥ 97.4%': /gate fidelity\s*≥\s*97\.4/,
    },
  },
};

export const folded = (text) => String(text || '').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f­]/g, '').replace(/\s+/g, ' ');

const cite = (pageId, q, label = `p. ${q.page}`) => `[${label}](/?page=${pageId}&pdf_page=${q.page}&quote=${encodeURIComponent(q.text)})`;

/** The search listing's line for a reference: its title link, year and the source to fetch it by. */
export function listedAs(ref, listing) {
  const want = folded(ref.title).toLowerCase();
  for (const line of String(listing || '').split('\n')) {
    const m = line.match(/^- \[((?:\\.|[^\]])+)\]\([^)]*\)(.*)$/);
    if (!m || folded(m[1].replace(/\\(.)/g, '$1')).toLowerCase() !== want) continue;
    const year = (m[2].match(/\((\d{4})[,)]/) || [])[1];
    const source = (m[2].match(/fetch_paper\(source="(arXiv:[^"]+)"\)/) || m[2].match(/fetch_paper\(source="([^"]+)"\)/) || [])[1];
    if (source) return { source, year: Number(year) };
  }
  return null;
}

export function answers(pageId, quotes) {
  const ref = (n) => cite(pageId, quotes[`ref${n}`], `ref. ${n}`);
  return {
    figure: `Panel c overlays the parity oscillations of a stationary and a transported atom pair: they match, so `
      + `“movement does not observably affect entanglement or coherence” ${cite(pageId, quotes.fig)}. `
      + `Panel d plots the Bell-state fidelity against the speed of the 110 μm move: it stays flat for moves slower than 200 μs and drops for faster ones, where atoms start to be lost.`,
    refs: 'Read all three. They go from an idea to working hardware:\n\n'
      + '| Paper | What it showed |\n|---|---|\n'
      + REFS.map((r) => `| ${ref(r.n)} ${r.who} ${r.year} | ${FACTS[r.n].row} |`).join('\n')
      + '\n\nSo the clause rests on a proposal (2000), the blockade it needs seen between two atoms (2009), and high-fidelity gates run in parallel (2019).',
  };
}

export function complete(req) {
  // The slide citation Gamma makes for an opened paper (CITE_PROMPT).
  if (/PPT-style citation/.test(req.system) && /Bluvstein/.test(req.userText)) return 'Bluvstein _et al._, Nature **604**, 451–456 (2022)';
  return null;
}

export function reply(req, ctx) {
  const { quotes, dump } = ctx.args;
  const pageId = req.pageId;
  if (!ASKS_REFS.test(req.question)) {
    ctx.note({ images: req.images.length });
    if (!req.images.length) return { text: 'I don’t see a figure attached to your message.' };
    return { text: answers(pageId, quotes).figure };
  }
  const searches = req.calls.filter((c) => c.name === 'search_papers');
  const fetches = req.calls.filter((c) => c.name === 'fetch_paper');
  if (dump) {
    fs.mkdirSync(dump, { recursive: true });
    req.calls.forEach((c, i) => fs.writeFileSync(path.join(dump, `${i}-${c.name}.txt`), `${JSON.stringify(c.args)}\n\n${c.result}`));
  }
  if (!searches.length) {
    if (!req.tools.includes('search_papers') || !req.tools.includes('fetch_paper')) return { text: 'I can’t search for papers in this chat.' };
    return { calls: REFS.map((r) => ({ name: 'search_papers', args: { query: r.title, kind: 'article', limit: 3 } })) };
  }
  const listing = searches.map((c) => c.result || '').join('\n');
  const listed = Object.fromEntries(REFS.map((r) => [r.n, listedAs(r, listing)]));
  ctx.note({ listed });
  if (!fetches.length) {
    const missing = REFS.filter((r) => !listed[r.n]);
    if (missing.length) return { text: `I couldn’t find ${missing.map((r) => `ref. ${r.n}`).join(' and ')} in the registries.` };
    return { calls: REFS.map((r) => ({ name: 'fetch_paper', args: { source: listed[r.n].source, title: r.title } })) };
  }
  // Every statement the table makes, in the text of the paper it is about.
  const grounding = {};
  for (const r of REFS) {
    const call = fetches.find((c) => c.args.source === listed[r.n]?.source);
    const text = folded(call?.result);
    grounding[r.n] = {
      source: call?.args.source || null, chars: text.length,
      error: !call || /^error\b/i.test(call.result || ''),
      title: FACTS[r.n].title.test(text),
      year: listed[r.n]?.year === r.year,
      claims: Object.fromEntries(Object.entries(FACTS[r.n].claims).map(([k, re]) => [k, re.test(text)])),
    };
  }
  const ok = (g) => !g.error && g.title && g.year && Object.values(g.claims).every(Boolean);
  const unread = REFS.filter((r) => !ok(grounding[r.n]));
  ctx.note({ grounding, grounded: !unread.length });
  if (unread.length) return { text: `I couldn’t read ${unread.map((r) => `ref. ${r.n}`).join(' and ')} well enough to compare them.` };
  return { text: answers(pageId, quotes).refs };
}
