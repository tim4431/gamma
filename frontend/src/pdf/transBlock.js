// What the translated view's overlay (pdf/TransOverlay.jsx) draws for one block
// of a page's entry (pdf/translateJob.js), where, and how its text is set. How
// large is pdf/transFit.js. Pure, so the tests run it in Node.

// The overlay's layout scale, px per pt. It is laid out at this one scale and
// transformed to the page's, as the text layer is from scale 1, so a zoom lays
// nothing out again. Above 1, where glyph advances are not hinted.
export const REF_SCALE = 2;
export const PAD = 0.75; // pt the boxes stand out from the original's lines (gamma/pdf_translated.py PAD too)

// Of the language's text, how far a line's pitch is from its size: Latin and
// Cyrillic take the block's own pitch, within what papers set, or the usual one
// with a single line to read it from. CJK glyphs fill the em and keep their own.
const LEADING = { min: 1.12, max: 1.3, single: 1.15, cjk: 1.28 };
const CJK_LANG = /^(zh|ja|ko)\b/;
// A box narrower than this many em of its size opens wide gaps when justified.
const JUSTIFY_EM = 18;
// The part of the em around the baseline that a formula's layout box stays in,
// inside any line's strut at the leadings above.
const CROP_ROOM = { up: 0.85, down: 0.2 };

const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));

// A block's line height, as a multiple of its font size, in target language
// `lang`: the median step between its lines' tops. gamma/pdf_translated.py
// _leading is this rule for the export, which has the same lines.
export function blockLeading(p, lang) {
  if (CJK_LANG.test(lang)) return LEADING.cjk;
  const tops = (p.lines || []).map((l) => l.y1);
  if (tops.length < 2) return LEADING.single;
  const steps = tops.slice(1).map((y, j) => y - tops[j]).sort((a, b) => a - b);
  return clamp(steps[Math.floor((steps.length - 1) / 2)] / p.size, LEADING.min, LEADING.max);
}

// The left and right of the box a block's text fills: the room of a one-line
// block (pageShape.js), else its own.
const textEdges = (p) => p.room || p;

// What a block's text is set to besides its size, at REF_SCALE: `lead` and
// `ragged` (too narrow to justify), the first line's `indent`, in px of the
// page since it comes from the page: a paragraph opener's, not a hanging one's,
// and none on a centred block, whose first line is only shorter than its second;
// and `italic`: CJK text has no italic, a browser only leans its glyphs.
export function blockSetting(p, lang) {
  const { x1, x2 } = textEdges(p);
  return {
    lead: blockLeading(p, lang),
    ragged: x2 - x1 < p.size * JUSTIFY_EM,
    indent: p.centred ? 0 : Math.max(0, p.indent || 0) * REF_SCALE,
    italic: !!p.italic && !CJK_LANG.test(lang),
  };
}

// A formula's box in em of its paragraph's size `em`: drawn `w` by `h`, sitting
// on its baseline at the height it had. Its layout box is cut to the room around
// the baseline by the margins `top` and `bottom`, so a tall formula hangs into
// the leading, as it does on the page, and stretches no line. `shift` raises the
// layout box's bottom edge, an inline block's baseline, from the text's.
export function cropBox(span, em) {
  const up = (span.base - span.y1) / em, down = (span.y2 - span.base) / em;
  const hi = clamp(up, -CROP_ROOM.down, CROP_ROOM.up), lo = clamp(-down, -CROP_ROOM.down, CROP_ROOM.up);
  return { w: (span.x2 - span.x1) / em, h: up + down, top: hi - up, bottom: -(lo + down), shift: lo };
}

// Whether block `p` is text the reader can have translated: the body's, or a
// figure's, which is drawn once asked for.
export const translatable = (p) => !!(p && (p.translate || p.figure));

// A block's results by index: text, null (came back untranslated, or left
// original as part of a reference list, the entry's `left`: the original
// shows), "" (its words went to the part before it: masked, nothing written),
// or missing (not landed yet). `working`: translation is still arriving for
// the page. Gives the block's {text, streaming, again, busy, key}, its key
// holding the final text too, so that a translation asked again (now with the
// paper's brief) fades in like a new one; {pending, busy} while nothing can
// show yet and its translation is queued or in flight, the original staying
// visible under a wash (not once this job could not translate it);
// {failed} once a job could not translate it (a quiet mark beside its
// original); or null when the overlay draws nothing. A text asked for again
// (Translate again) streams over the one it had (`again`), which shows,
// shimmering (`busy`), until the new one's first words come. A figure's text
// is drawn only once the reader asked for it, and nothing is drawn over a
// block the reader shows in the original (the entry's `original`).
export function blockShow(entry, i, working) {
  const p = entry.paras[i];
  if (!translatable(p) || entry.original?.has(i)) return null;
  const final = entry.texts[i];
  const partial = final === "" ? null : entry.partial?.[i];
  const busy = !!entry.busy?.has(i);
  const text = final === "" ? "" : partial || final;
  if (text === "" || text) {
    return { text, streaming: !!partial, again: !!partial && !!final, busy: busy && !partial && !!final,
             key: final ? `${i}:${final}` : i };
  }
  const waiting = busy || (final === undefined && entry.queued && p.translate && !entry.failed?.has(i));
  if (final == null && working && waiting) return { pending: true, busy };
  return final == null && entry.failed?.has(i) ? { failed: true } : null;
}

// How block `i` of `entry` comes to show its final text, `before` being the
// entry at the overlay's last commit: "new" over nothing but the original or
// over another final text, "streamed" over the same words as they streamed, or
// null: nothing landed, or a mount (no `before`), which shows what is there.
export function blockLanding(before, entry, i) {
  const final = entry.texts[i];
  if (!before || !final || before.texts[i] === final) return null;
  return before.partial?.[i] ? "streamed" : "new";
}

// How many of a streaming block's `lines` (blockBoxes) its text has reached,
// set down to `bottom`, from the top of its box.
export const linesReached = (lines, bottom) => lines.filter((l) => l.top < bottom).length;

// Whether block `i` of a page's `entry` has a translation drawn over its
// original: one landed ("" too: its words went to the block before, and it is
// masked) or streaming in, and not shown in the original by the reader.
export const drawnOver = (entry, i) => "text" in (blockShow(entry, i, false) || {});

// The blocks of a page's entry the overlay masks: those with text drawn over
// them (drawnOver), by index.
export const maskedBlocks = (entry) => entry.paras.flatMap((_, i) => (drawnOver(entry, i) ? [i] : []));

// The page's text runs (pdfTranslate.textRuns, at scale 1) that lie under the
// masks of `blocks`, as a set of their indices: the original text there is
// hidden. A run is where its middle is; rotated text belongs to no block.
export function maskedRuns(runs, paras, blocks) {
  const masks = blocks.flatMap((i) => {
    const { box, lines } = blockBoxes(paras[i], 1);
    return lines.map((l) => ({
      x1: box.left + l.left, y1: box.top + l.top,
      x2: box.left + l.left + l.width, y2: box.top + l.top + l.height,
    }));
  });
  const out = new Set();
  runs.forEach((r, k) => {
    const x = r.x + r.w / 2, y = r.y - r.h * 0.3;
    if (!r.rotated && masks.some((m) => x >= m.x1 && x <= m.x2 && y >= m.y1 && y <= m.y2)) out.add(k);
  });
  return out;
}

// A block's boxes at `scale`, in px of the page at that scale: `box`, which its
// text fills, down to the block's `foot` when one starts inside it (pageShape.js:
// the next block's text opens at the top of its box, PAD above the foot, as this
// one's does), and its `lines` inside it, the masks over the original's lines,
// wherever a one-line block's room puts the box. The overlay draws them at
// REF_SCALE.
export function blockBoxes(p, scale) {
  const lns = p.lines || [];
  const pad = PAD * scale;
  const { x1, x2 } = textEdges(p);
  const box = {
    left: x1 * scale - pad, top: p.y1 * scale - pad,
    width: (x2 - x1) * scale + 2 * pad,
    height: Math.min(p.y2 - p.y1 + 2 * PAD, (p.foot ?? Infinity) - p.y1) * scale,
  };
  const lines = lns.map((l, j) => {
    // Fill the inter-line leading too: the original's line pitch exceeds the
    // glyph-box height, and slivers of the original text otherwise peek
    // through between masks. A line whose glyph box runs into the next one's
    // keeps its own bottom: the next line may be shorter.
    const next = lns[j + 1];
    const bottom = next && next.y1 - l.y2 < p.size * 1.2 ? Math.max(l.y2, next.y1) : l.y2;
    return {
      left: (l.x1 - x1) * scale, top: (l.y1 - p.y1) * scale,
      width: (l.x2 - l.x1) * scale + 2 * pad,
      height: (bottom - l.y1) * scale + 2 * pad,
    };
  });
  return { box, lines };
}
