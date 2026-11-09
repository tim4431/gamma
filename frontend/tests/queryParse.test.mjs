// The search box's grammar (search/queryParse.js): which tokens are
// clauses, how a value splits into alternatives, ops and ranges, and the
// free text that is left when clauses are cut out.
import assert from "node:assert/strict";
import { test } from "node:test";
import { cutSpans, lastAlternative, parseQuery, quoteValue, tokenAt, tokenize, valueHead, valueTail } from "../src/search/queryParse.js";

const KEYS = ["label", "folder", "is", "year", "in", "created"];
const parse = (q) => parseQuery(q, KEYS);
const clauses = (q) => parse(q).filter((tok) => tok.type === "clause");
const texts = (q) => parse(q).filter((tok) => tok.type === "text").map((tok) => tok.raw);
const vals = (q) => clauses(q).map((c) => c.values.map((v) => v.text));

test("a known key is a clause; the rest is text, in order, with its places", () => {
  const tokens = parse("label:cs229 attention is all");
  assert.deepEqual(tokens.map((tok) => [tok.type, tok.raw, tok.from, tok.to]), [
    ["clause", "label:cs229", 0, 11], ["text", "attention", 12, 21], ["text", "is", 22, 24], ["text", "all", 25, 28]]);
  assert.equal(tokens[0].key, "label");
  assert.equal(tokens[0].neg, false);
  assert.equal(tokens[0].value, "cs229");
  assert.equal(tokens[0].valueFrom, 6);
  assert.deepEqual(tokens[0].values, [{ op: "=", a: "cs229", b: "", text: "cs229" }]);
});

test("keys fold case and width, colons and commas their full-width forms", () => {
  for (const q of ["Label:cs229", "LABEL:cs229", "ｌａｂｅｌ:cs229", "label：cs229"]) {
    assert.deepEqual(clauses(q).map((c) => [c.key, c.values[0].text]), [["label", "cs229"]], q);
  }
  assert.deepEqual(clauses("IS:pdf").map((c) => c.key), ["is"]);
  assert.deepEqual(vals("Year：2019，2020"), [["2019", "2020"]]);
  assert.deepEqual(clauses("Year：2019，2020")[0].key, "year");
});

test("quotes hold spaces and commas in a value and come off it, smart quotes too", () => {
  assert.deepEqual(vals('label:"Fresh, label" x'), [["Fresh, label"]]);
  assert.deepEqual(texts('label:"Fresh, label" x'), ["x"]);
  assert.deepEqual(vals("label:“quantum optics”"), [["quantum optics"]]);
  assert.deepEqual(vals("label:”quantum optics”"), [["quantum optics"]]);
  assert.deepEqual(vals('label:a,"b, c",d'), [["a", "b, c", "d"]]);
  // still being typed: an unclosed quote runs to the end
  assert.deepEqual(vals('label:"Fresh, la'), [["Fresh, la"]]);
});

test("the raw text is read: digit separators are no join", () => {
  assert.deepEqual(vals("year:2019,2020"), [["2019", "2020"]]);
  assert.deepEqual(clauses("year:2019,2020")[0].values.map((v) => v.a), ["2019", "2020"]);
});

test("unknown keys, times and identifiers stay text", () => {
  const q = "arXiv:2301.12345 doi:10.1103/x 10:30 Fig: lable:x";
  assert.equal(clauses(q).length, 0);
  assert.deepEqual(texts(q), ["arXiv:2301.12345", "doi:10.1103/x", "10:30", "Fig:", "lable:x"]);
});

test("a leading minus negates a known key only", () => {
  const [neg] = clauses("-label:read");
  assert.equal(neg.neg, true);
  assert.equal(neg.key, "label");
  assert.equal(clauses("－label:read")[0].neg, true);
  assert.deepEqual(texts("-1/2 -3 dB --label:x -arXiv:1"), ["-1/2", "-3", "dB", "--label:x", "-arXiv:1"]);
});

test("ops, ranges, commas and an empty value", () => {
  const ops = (q) => clauses(q)[0].values.map(({ op, a, b }) => [op, a, b]);
  assert.deepEqual(ops("year:>2020,>=2020,<2015,<=2015,=2018"),
    [[">", "2020", ""], [">=", "2020", ""], ["<", "2015", ""], ["<=", "2015", ""], ["=", "2018", ""]]);
  assert.deepEqual(ops("year:2019..2021,2019..,..2021"), [["range", "2019", "2021"], ["range", "2019", ""], ["range", "", "2021"]]);
  assert.deepEqual(ops("year:＞＝2020,≤2015,2019…2021"), [[">=", "2020", ""], ["<=", "2015", ""], ["range", "2019", "2021"]]);
  assert.deepEqual(ops("created:>=@today-7d"), [[">=", "@today-7d", ""]]);
  // a name keeps what was typed: ops only matter to the keys that read them
  assert.deepEqual(clauses("label:>x")[0].values[0].text, ">x");
  assert.deepEqual(ops('label:">=x"'), [["=", ">=x", ""]]);
  assert.deepEqual(clauses("label:")[0].values, []);
  assert.deepEqual(clauses("label:,")[0].values, []);
  assert.deepEqual(vals("label:a,,b,"), [["a", "b"]]);
});

test("a quoted run in free text is one token, quotes kept; a quoted clause is text", () => {
  assert.deepEqual(texts('"quantum optics" lab'), ['"quantum optics"', "lab"]);
  assert.deepEqual(parse('"label:x" y').map((tok) => tok.type), ["text", "text"]);
  assert.deepEqual(tokenize('  a   "b  c"d  ').map((tok) => [tok.raw, tok.from, tok.to]), [["a", 2, 3], ['"b  c"d', 6, 13]]);
});

test("cutSpans: no clause leaves query.trim(); a cut collapses its whitespace only", () => {
  for (const q of ['  "3,000-qubit"   systems ', "3000  qubit", "x"]) assert.equal(cutSpans(q, []), q.trim());
  const q = "a  label:x   b  c";
  const [clause] = clauses(q);
  assert.equal(cutSpans(q, [clause]), "a b  c");
  assert.equal(cutSpans("label:x attention", clauses("label:x attention")), "attention");
  assert.equal(cutSpans("attention is:pdf", clauses("attention is:pdf")), "attention");
  assert.equal(cutSpans("is:pdf label:x", clauses("is:pdf label:x")), "");
});

test("tokenAt: the token the caret is in or just after, else an empty one", () => {
  const q = 'ab label:"x y" cd';
  assert.deepEqual(tokenAt(q, 0), { raw: "ab", from: 0, to: 2 });
  assert.deepEqual(tokenAt(q, 2), { raw: "ab", from: 0, to: 2 });
  assert.deepEqual(tokenAt(q, 11), { raw: 'label:"x y"', from: 3, to: 14 });
  assert.deepEqual(tokenAt(q, 17), { raw: "cd", from: 15, to: 17 });
  assert.deepEqual(tokenAt("ab  cd", 3), { raw: "", from: 3, to: 3 });
});

test("valueHead and lastAlternative split off the alternative being typed", () => {
  assert.equal(valueHead('a,"b, c",fr'), 'a,"b, c",');
  assert.equal(valueHead("fr"), "");
  assert.equal(lastAlternative('a,"b, c",fr'), "fr");
  assert.equal(lastAlternative('"Fresh, la'), "Fresh, la");
});

test("valueTail keeps the alternatives after the one at the caret", () => {
  assert.equal(valueTail("a,b", 1), ",b");
  assert.equal(valueTail("a,b", 0), ",b");
  assert.equal(valueTail("a,b", 3), "");
  assert.equal(valueTail('"x, y",b', 2), ",b");
  assert.equal(valueTail("a，b", 1), "，b");
  assert.equal(valueTail("", 0), "");
});

test("quoteValue quotes what must be, and round-trips through parseQuery", () => {
  assert.equal(quoteValue("plain"), "plain");
  for (const name of ["Fresh, label", "quantum optics", 'say "hi"', "a，b", "Lab, renamed / 2026", ""]) {
    const [clause] = clauses(`label:${quoteValue(name)} rest`);
    assert.deepEqual(clause.values.map((v) => v.text), name ? [name] : [], name);
  }
});
