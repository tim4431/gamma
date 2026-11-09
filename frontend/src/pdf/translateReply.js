// A translation's way back in the PDF translated view: a unit's reply split
// onto its blocks (`results`), the text streamed so far (`partials`), and a
// block's translation as it is drawn (`textParts`). Pure, like
// pdf/translatePlan.js, whose units it reads. docs/dev/translation.md "What a
// request carries" describes the rules.

import { TOKEN } from "./pageFlow.js";
import { NO_SPACE } from "./pdfTranslate.js";
import { BREAK } from "./translatePlan.js";

// --- results ----------------------------------------------------------------

// Abbreviations whose period ends no sentence.
const ABBREVIATION = /(?:^|[\s(])(?:[A-Za-z]|e\.g|i\.e|cf|vs|al|etc|approx|Fig|Figs|Eq|Eqs|Ref|Refs|Sec|Secs|Tab|Abb|Gl|Kap|bzw|z\.B|vgl|Nr|No|resp)\.$/;

// Where to cut `text` near `at`: a sentence end, else a clause mark, else a
// space, else (CJK) anywhere, searching outward up to a quarter of the text.
// A period ends a sentence only before a space and a word that is no number,
// and not after an abbreviation ("Fig.", "e.g.", "0.987").
function cutNear(text, at) {
  const reach = Math.max(8, Math.round(text.length * 0.25));
  const sentence = (k) => {
    const c = text[k - 1];
    if (/[!?;。！？；]/.test(c)) return true;
    if (c !== ".") return false;
    if (!/\s/.test(text[k] || "") || /^\s*\d/.test(text.slice(k))) return false;
    return !ABBREVIATION.test(text.slice(Math.max(0, k - 8), k));
  };
  // A comma between digits ("0,987", "1,000") is part of a number.
  const clause = (k) => /[,，、:：]/.test(text[k - 1]) && !(text[k - 1] === "," && /\d/.test(text[k - 2] || "") && /\d/.test(text[k] || ""));
  for (const test of [sentence, clause, (k) => /\s/.test(text[k - 1])]) {
    for (let d = 0; d <= reach; d++) {
      for (const k of [at + d, at - d]) {
        if (k > 0 && k < text.length && test(k)) return k;
      }
    }
  }
  return Math.max(0, Math.min(text.length, at));
}

// Split `text` into `weights.length` pieces sized like the weights.
function splitByShare(text, weights) {
  const total = weights.reduce((n, w) => n + w, 0) || 1;
  const pieces = [];
  let rest = text, left = total;
  for (let k = 0; k < weights.length - 1; k++) {
    const at = cutNear(rest, Math.round(rest.length * weights[k] / left));
    pieces.push(rest.slice(0, at).trim());
    rest = rest.slice(at);
    left -= weights[k];
  }
  pieces.push(rest.trim());
  return pieces;
}

// Remove the markers from a text, joining around each the way lines join:
// no space between kana or Han.
function unmark(s) {
  return s.split(BREAK).map((x) => x.trim()).filter(Boolean)
    .reduce((acc, x) => (!acc ? x : NO_SPACE.test(acc[acc.length - 1]) && NO_SPACE.test(x[0]) ? acc + x : `${acc} ${x}`), "");
}

// The non-empty parts' pieces of a unit's reply: one per non-empty part, cut
// at the markers, or by the parts' lengths at the nearest sentence or clause
// boundary when the model lost, doubled or misplaced one.
function pieces(unit, text) {
  const live = unit.parts.filter(Boolean);
  if (live.length <= 1) return [String(text ?? "").replaceAll(BREAK, "").trim()];
  const cut = String(text ?? "").split(BREAK).map((x) => x.trim());
  if (cut.length === live.length && cut.every(Boolean)) return cut;
  return splitByShare(unmark(String(text ?? "")), live.map((p) => p.length || 1));
}

// A piece's tokens, numbered for the block it lands in: its own {vk}, or
// {v:pn.i.k} for a token of another block of the unit (the model moved a
// formula across a join), which the overlay looks up there.
function localize(unit, target, piece) {
  return piece.replace(TOKEN, (whole, n) => {
    const t = unit.tokens[Number(n) - 1];
    if (!t) return "";
    return t.pn === target.pn && t.i === target.i ? `{v${t.k}}` : `{v:${t.pn}.${t.i}.${t.k}}`;
  });
}

// A unit's reply → what each of its blocks shows: [{pn, i, text}], text a
// translation, null for one that came back as its source (the original
// shows; "Translate this page" asks again), or "" for a block whose words
// all went to the part before it (it is masked).
export function results(unit, reply) {
  const got = pieces(unit, reply);
  let k = 0;
  return unit.targets.map((t, j) => {
    const part = unit.parts[j];
    if (!part) return { ...t, text: "" };
    const piece = got[k++] ?? "";
    return { ...t, text: piece && piece !== part ? localize(unit, t, piece) : null };
  });
}

// The text streamed so far → what each block can show yet: the pieces
// before the markers written so far, and the one in progress (a marker or
// token half-written at its end dropped). Blocks past it get nothing yet.
export function partials(unit, text) {
  const live = unit.targets.filter((_, j) => unit.parts[j]);
  const cut = String(text ?? "").split(BREAK).slice(0, live.length);
  cut[cut.length - 1] = cut[cut.length - 1].replace(/\{[^}]*$/, "");
  return cut.map((piece, k) => ({ ...live[k], text: localize(unit, live[k], piece.trim()) })).filter((x) => x.text);
}

// --- drawing a result -------------------------------------------------------

// A token in a block's translation: {vK} the block's own K-th span, {v:pn.i.K}
// block i's on page pn (one the model moved across a join, `localize`).
const TRANS_TOKEN = /\{v(?::(\d+)\.(\d+)\.)?(\d+)\}/g;

// A block's translation as it is drawn: its text, with each formula token as
// {span, page}, the original formula's span and the page it is printed on.
// The overlay (pdf/TransOverlay.jsx) crops that formula into the text on
// screen and an export (pdf/translateExport.js) into the file, both from
// these parts, so the file holds what the screen shows. `spans` are the
// block's own (on `page`), `spanOf(pn, i, k)` looks up another block's. A
// token for text (a citation marker, a URL) is that text again; one that
// names no span is dropped. Neighbouring texts are one string.
export function textParts(text, { spans = [], page = 0, spanOf = null } = {}) {
  const src = String(text ?? "");
  const parts = [];
  const add = (s) => {
    if (!s) return;
    if (typeof parts[parts.length - 1] === "string") parts[parts.length - 1] += s;
    else parts.push(s);
  };
  let at = 0;
  for (const m of src.matchAll(TRANS_TOKEN)) {
    add(src.slice(at, m.index));
    const pn = m[1] ? Number(m[1]) : page;
    const span = m[1] ? spanOf?.(pn, Number(m[2]), Number(m[3])) : spans?.[Number(m[3]) - 1];
    if (span?.kind === "math") parts.push({ span, page: pn });
    else if (span) add(span.str);
    at = m.index + m[0].length;
  }
  add(src.slice(at));
  return parts;
}
