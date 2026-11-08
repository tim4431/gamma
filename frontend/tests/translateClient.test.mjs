// App's side of the translated view (src/app/translateClient.js): what a
// glossary write the server refused says to the reader, in the reader's
// words rather than the server's.
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";

// shared/lib/utils.js imports without extensions and reads `window` as it loads.
register("data:text/javascript," + encodeURIComponent(`
  export async function resolve(specifier, context, next) {
    try { return await next(specifier, context); } catch (err) {
      if (err?.code === "ERR_MODULE_NOT_FOUND" && /^\\.\\.?\\//.test(specifier) && !/\\.[a-z]+$/.test(specifier)) return next(specifier + ".js", context);
      throw err;
    }
  }`));
globalThis.window = globalThis;
globalThis.location = new URL("http://gamma.test/");
globalThis.dispatchEvent = () => true;

let answer = null;
globalThis.fetch = async () => answer;
const { addTerm, editTerm, termRefusal } = await import("../src/app/translateClient.js");
const refusal = (status, detail) => ({ ok: false, status, text: async () => JSON.stringify({ detail }) });

test("a glossary write the server refused says why in the reader's words", async () => {
  answer = refusal(409, "the glossary has that term already");
  await assert.rejects(addTerm("zh-CN", " switched ", "切换"), (err) => {
    assert.equal(err.message, "The glossary has “switched” already");
    assert.equal(err.status, 409);
    return true;
  });
  answer = refusal(409, "the glossary holds 500 terms in this language at most");
  await assert.rejects(addTerm("zh-CN", "qubit", "量子比特"), { message: "The glossary is full in this language. Delete a term to add another." });
  answer = refusal(404, "the glossary has no such term");
  await assert.rejects(editTerm("zh-CN", "gone", "gone", "x"), { message: "That term is no longer in the glossary." });
  // Anything else as the server said it.
  assert.equal(termRefusal(Object.assign(new Error("a term needs its source and its translation"), { status: 400 })),
    "a term needs its source and its translation");
  assert.equal(termRefusal(null), "Couldn't add the term");
});
