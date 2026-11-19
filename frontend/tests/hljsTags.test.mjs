// The code view's colours (src/documents/viewers/hljsTags.js): every lezer
// tag maps to a class app.css colours for every theme.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { Tag } from "@lezer/highlight";
import { HLJS_TAGS } from "../src/documents/viewers/hljsTags.js";

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
