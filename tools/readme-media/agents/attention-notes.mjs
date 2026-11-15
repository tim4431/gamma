// The agent-collab story's Codex (demo-agent.mjs): lecture notes on
// attention, kept as a LaTeX project in a synced folder
// (agents/attention-notes/, built with `make`). Three comments, each sent to
// Codex as it is written, and a reply in one of their threads, in the order
// an author works: write, read the result, then the figure:
//   multihead  a line of sections/multihead.tex: write the empty section
//   scale      a passage of notes.pdf: the scores are divided by d_k, not √d_k
//   mask       a region of figures/attention-map.pdf: mask the future tokens
//   footnote   the reply to multihead's question, in the same session
// Each turn checks what Gamma hands it (the thread's file, quote and source
// line, the session it runs in), edits the sources, runs the build, and
// answers in the thread. The edits leave every commented passage as it was,
// so its highlight is found again whole in the rebuilt PDF.

/** What the person types, comment by comment (record-agent-collab.mjs). */
export const ASK = {
  scale: 'Divide by $\\sqrt{d_k}$, not $d_k$, and say why.',
  mask: 'This is the decoder: mask the future tokens.',
  multihead: 'Fill this in: heads in parallel, with the equation.',
  footnote: 'Yes, as a footnote.',
};

/** Where each comment goes: the passage selected, the region, the line. */
export const PLACES = {
  scale: 'the scores are turned into weights by a softmax',
  mask: { file: 'figures/attention-map.pdf', keys: ['sat', 'mat'], queries: ['the', 'cat'] },
  multihead: '\\section{Multi-head attention}',
};

export const REPLY = {
  scale: 'Fixed (1) to divide by $\\sqrt{d_k}$ and said why under it: with unit-variance entries $q \\cdot k$ has '
    + 'variance $d_k$, so large scores would saturate the softmax.',
  mask: 'Masked the future tokens and renormalised each row over the ones left, so the map is lower-triangular, '
    + 'as a decoder\'s is. The caption says so too.',
  multihead: 'Wrote §2: $h$ heads attend in parallel on projections of size $d_k = d/h$ and are concatenated, '
    + 'equation (2). Should I add the cost per layer, $O(n^2 d)$, as well?',
  footnote: 'Added it as a footnote to §2.',
};

const BUILD = 'make';
const expect = (problems, ok, problem) => { if (!ok) problems.push(problem); return problems; };

export const turns = [
  {
    name: 'multihead',
    message: ASK.multihead,
    check: ({ thread, resumed }) => {
      const p = [];
      expect(p, !resumed, 'a new session');
      expect(p, thread?.file === 'sections/multihead.tex', `the thread is on sections/multihead.tex, not ${thread?.file}`);
      expect(p, (thread?.quote || '').includes(PLACES.multihead), `the quote is ${JSON.stringify(thread?.quote)}`);
      return p;
    },
    steps: [
      { think: 1.5 },
      {
        edit: 'sections/multihead.tex',
        old: '\\label{sec:multihead}\n',
        new: '\\label{sec:multihead}\n\n'
          + 'One attention layer mixes the values with one set of weights. Multi-head\n'
          + 'attention runs $h$ of them in parallel, each on its own learned projections\n'
          + 'of size $d_k = d/h$, and concatenates the results:\n'
          + '\\begin{equation}\n'
          + '  \\operatorname{MultiHead}(Q, K, V)\n'
          + '    = \\operatorname{Concat}(\\mathrm{head}_1, \\dots, \\mathrm{head}_h)\\, W^{O},\n'
          + '  \\label{eq:multihead}\n'
          + '\\end{equation}\n'
          + 'where $\\mathrm{head}_i = \\operatorname{Attention}(Q W_i^{Q}, K W_i^{K}, V W_i^{V})$.\n'
          + 'Each head can follow a relation of its own, such as the previous word or the\n'
          + 'subject of a verb, for the cost of one layer of the full size.\n',
      },
      // a look over what it wrote before the build: the source, open in
      // Gamma, shows the section (some 5 s after the edit: the hot loop and
      // the code view's reload) while the thread still says it works
      { think: 7 },
      { run: BUILD },
    ],
    reply: REPLY.multihead,
  },
  {
    name: 'scale',
    message: ASK.scale,
    check: ({ thread, resumed }) => {
      const p = [];
      expect(p, !resumed, 'a new session');
      expect(p, thread?.file === 'notes.pdf', `the thread is on notes.pdf, not ${thread?.file}`);
      expect(p, (thread?.quote || '').replace(/\s+/g, ' ').includes(PLACES.scale), `the quote is ${JSON.stringify(thread?.quote)}`);
      // SyncTeX maps the passage to its source line (docs/dev/annotations.md "SyncTeX")
      expect(p, thread?.source?.file === 'sections/attention.tex' && thread.source.line >= 3 && thread.source.line <= 5,
        `its source is ${JSON.stringify(thread?.source)}`);
      return p;
    },
    steps: [
      { think: 2.5 },
      { edit: 'sections/attention.tex', old: '\\frac{QK^{\\top}}{d_k}', new: '\\frac{QK^{\\top}}{\\sqrt{d_k}}' },
      {
        edit: 'sections/attention.tex',
        old: '\\end{equation}\nEach row',
        new: '\\end{equation}\n'
          + 'Dividing by $\\sqrt{d_k}$ keeps the scores at unit variance: for queries and\n'
          + 'keys with unit-variance entries, $q \\cdot k$ has variance $d_k$, and large\n'
          + 'scores would push the softmax into saturation, where its gradients vanish.\n\n'
          + 'Each row',
      },
      { run: BUILD },
    ],
    reply: REPLY.scale,
  },
  {
    name: 'mask',
    message: ASK.mask,
    check: ({ thread, resumed }) => {
      const p = [];
      expect(p, !resumed, 'a new session');
      expect(p, thread?.file === PLACES.mask.file, `the thread is on ${PLACES.mask.file}, not ${thread?.file}`);
      expect(p, thread?.page === 1, `on page 1, not ${thread?.page}`);
      return p;
    },
    steps: [
      { think: 2.5 },
      { edit: 'figures/attention-map.tex', old: '\\causalfalse  % true', new: '\\causaltrue   % true' },
      {
        edit: 'sections/attention.tex',
        old: '\\caption{Self-attention weights for \\emph{the cat sat on the mat}: row $i$\n'
          + '    is how much token $i$ attends to each token.}',
        new: '\\caption{Causal self-attention weights for \\emph{the cat sat on the mat}:\n'
          + '    token $i$ attends only to itself and the tokens before it.}',
      },
      { run: BUILD },
    ],
    reply: REPLY.mask,
  },
  {
    name: 'footnote',
    message: ASK.footnote,
    // The person's reply to the question: the same session goes on.
    check: ({ thread, resumed, before }) => {
      const p = [];
      expect(p, resumed && before.includes('multihead'), `the multihead session resumed, not ${JSON.stringify({ resumed, before })}`);
      expect(p, thread?.file === 'sections/multihead.tex', `the thread is on sections/multihead.tex, not ${thread?.file}`);
      return p;
    },
    steps: [
      { think: 2 },
      {
        edit: 'sections/multihead.tex',
        old: 'for the cost of one layer of the full size.\n',
        new: 'for the cost of one layer of the full size.\\footnote{Per layer, attention costs\n'
          + '  $O(n^2 d)$ for $n$ tokens: $h$ heads of size $d/h$ cost what one head of size $d$ would.}\n',
      },
      { run: BUILD },
      // It answers with Gamma's comment tool, then resolves the thread: nothing is left to decide.
      { gamma: 'comment', arguments: { thread_id: '$thread', body: REPLY.footnote } },
      { gamma: 'resolve', arguments: { thread_id: '$thread' } },
    ],
    reply: REPLY.footnote,
  },
];
