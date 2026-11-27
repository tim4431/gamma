// jsonInit (shared/lib/utils.js): apiJson's options for a JSON request,
// which the requests of the settings panes, the comments, the computer's
// chat, the inbox, the jobs, the search, the uploads, Zotero and the
// glossary are built with.
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
const { jsonInit } = await import("../src/shared/lib/utils.js");

const headers = { "Content-Type": "application/json" };

test("a JSON request sends its body as JSON, and one without a body sends none", () => {
  assert.deepEqual(jsonInit("PUT", { folder: "Backups", keep: [1, 2] }), { method: "PUT", headers, body: '{"folder":"Backups","keep":[1,2]}' });
  assert.deepEqual(jsonInit("POST", {}), { method: "POST", headers, body: "{}" });
  assert.deepEqual(jsonInit("DELETE"), { method: "DELETE", headers });
  // the bucket's Test of the settings in force (BackupBucket's tryBucket(null)) sends none, not "null"
  assert.deepEqual(jsonInit("POST", null), { method: "POST", headers });
});
