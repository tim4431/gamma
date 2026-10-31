// The scripted model for record-agentic-notes.mjs: asked, with a lecture
// slide pasted into a page chat, to put the slide's equation under the block
// at the user's cursor. It calls Gamma's create_block once (Gamma shows the
// approval card and makes the edit), then answers in one line naming the
// block it went under. A request without the page, the cursor block, the one
// picture or the tool gets a reply saying what was missing, which the
// recorder's checks refuse.
//

// The generalized Rabi formula the recorder draws on the slide.
export const LATEX = '$$P_e(t)=\\frac{\\Omega^2}{\\Omega^2+\\Delta^2}\\sin^2\\left(\\frac{\\sqrt{\\Omega^2+\\Delta^2}}{2}\\,t\\right)$$';

// Slow enough that the one-line reply reads as a stream once the edit plays
// it at 2x. toolPause, before the tool call and before the reply after it,
// lets the sent slide read in its bubble before the card comes up, and the
// landed equation be seen before the reply starts streaming.
export const pace = { firstToken: 700, toolPause: 750, cps: 60 };

// The caption is quoted without its closing colon, so the reply's own full
// stop does not follow a colon inside the quote.
const quoted = (text) => `“${text.replace(/\s+/g, ' ').trim().replace(/[\s:;,.]+$/, '')}”`;

export function reply(req, ctx) {
  const done = req.calls.find((c) => c.name === 'create_block');
  if (done) {
    ctx.note({ created: done.result });
    if (/^ok\b/.test(done.result || '')) {
      return { text: `Added the generalized Rabi formula from your slide as display math under ${quoted(req.cursor?.text || 'your block')}.` };
    }
    return { text: `I could not add the equation: ${done.result || 'the tool gave no result'}.` };
  }
  const missing = [
    !req.tools.includes('create_block') && 'the create_block tool',
    !req.pageId && 'the page id',
    !req.cursor && 'the block at your cursor',
    req.images.length !== 1 && `one picture (got ${req.images.length})`,
  ].filter(Boolean);
  ctx.note({ pageId: req.pageId, cursor: req.cursor, images: req.images.length, tools: req.tools.length, missing });
  if (missing.length) return { text: `I could not add it: the request is missing ${missing.join(', ')}.` };
  // Ids first, the LaTeX last: a top-level block on the page, right after
  // the cursor's block (create_block: parent_id is the page, after_id the
  // sibling to follow).
  return { calls: [{ name: 'create_block', args: { parent_id: req.pageId, after_id: req.cursor.id, content: LATEX } }] };
}
