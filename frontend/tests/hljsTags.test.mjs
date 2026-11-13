// The code view's colours (src/documents/viewers/hljsTags.js): every lezer
// tag maps to a class app.css colours for every theme, and the common
// tokens of a grammar land on the classes the notes' fenced code uses.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { Tag, tags } from "@lezer/highlight";
import { HLJS_CLASSES, HLJS_TAGS } from "../src/documents/viewers/hljsTags.js";

const css = fs.readFileSync(new URL("../src/shared/styles/app.css", import.meta.url), "utf8");
// the `.hljs-*` class words app.css styles, `class_` and `escape_` included
const styled = new Set([...css.matchAll(/\.hljs-([\w]+)(?:\.([\w]+))?/g)].flatMap(([, a, b]) => (b ? [`hljs-${a}`, b] : [`hljs-${a}`])));

test("every tag is a lezer tag and every class a styled one", () => {
  assert.ok(HLJS_TAGS.length > 40);
  for (const { tag, class: cls } of HLJS_TAGS) {
    assert.ok(tag instanceof Tag, `${cls}: a Tag`);
    for (const word of cls.split(" ")) assert.ok(styled.has(word), `${word} is coloured by app.css`);
  }
});

test("the common tokens take the notes' classes", () => {
  const cls = (tag) => HLJS_CLASSES.style([tag]);
  assert.equal(cls(tags.keyword), "hljs-keyword");
  assert.equal(cls(tags.controlKeyword), "hljs-keyword", "a modified tag falls back on its parent");
  assert.equal(cls(tags.comment), "hljs-comment");
  assert.equal(cls(tags.string), "hljs-string");
  assert.equal(cls(tags.number), "hljs-number");
  assert.equal(cls(tags.bool), "hljs-literal");
  assert.equal(cls(tags.typeName), "hljs-type");
  assert.equal(cls(tags.className), "hljs-title class_");
  assert.equal(cls(tags.function(tags.variableName)), "hljs-title");
  assert.equal(cls(tags.variableName), "hljs-variable");
  assert.equal(cls(tags.tagName), "hljs-name");
  assert.equal(cls(tags.attributeName), "hljs-attr");
  assert.equal(cls(tags.heading), "hljs-section");
  assert.equal(cls(tags.operator), null, "an operator keeps the text colour");
});
