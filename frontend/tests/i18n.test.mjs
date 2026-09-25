import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { catalogLocales, checkCatalog, readCatalog, scanSources } from "../tools/i18n.mjs";

// The scanner finds every literal-keyed call and nothing else.
test("scanSources picks up t(), tn() and T() literals only", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "i18n-"));
  fs.writeFileSync(path.join(dir, "a.jsx"), [
    'const a = t("Copy link");',
    "const b = t('It\\'s \"quoted\"', { n });",
    'const c = tn("{n} page", "{n} pages", n);',
    'const items = [{ label: T("Heading 1") }];',
    'const skip = [x.at(0), s.split("x"), PT("no"), t(message), t(`tpl`)];',
  ].join("\n"));
  const keys = scanSources(dir);
  assert.deepEqual([...keys.keys()].sort(), ["Copy link", "Heading 1", 'It\'s "quoted"', "{n} page"]);
  assert.equal(keys.get("{n} page").plural, "{n} pages");
  assert.deepEqual(keys.get("Copy link").files, ["a.jsx"]);
});

test("checkCatalog reports missing, empty, orphan and mismatched entries", () => {
  const keys = new Map([
    ["Copy link", { files: ["a.jsx"] }],
    ["Open {name}", { files: ["a.jsx"] }],
    ["{n} page", { plural: "{n} pages", files: ["a.jsx"] }],
    ["Delete", { files: ["a.jsx"] }],
  ]);
  const problems = checkCatalog({
    "Copy link": "复制链接",
    "Open {name}": "打开",
    "{n} page": { one: "{n} page", other: "{n} pages" },
    "Delete": "",
    "Gone": "x",
  }, keys);
  assert.deepEqual(problems, [
    'placeholders differ: "Open {name}" → "打开"',
    'untranslated: "Delete"',
    'orphan: "Gone"',
  ]);
  assert.deepEqual(checkCatalog({ "{n} page": "{n} 页" }, new Map([["{n} page", { plural: "{n} pages", files: [] }]])), []);
  // An "_" placeholder (English plural s) may be dropped, but nothing may be added.
  const plural = new Map([["Copied {n} page{_s}.", { files: [] }]]);
  assert.deepEqual(checkCatalog({ "Copied {n} page{_s}.": "已复制 {n} 个页面。" }, plural), []);
  assert.equal(checkCatalog({ "Copied {n} page{_s}.": "已复制 {count} 个页面。" }, plural).length, 1);
});

// The real catalogs: every string in src/ is translated, nothing stale is
// kept. `npm run i18n -- --sync` adds the missing keys to fill in.
for (const locale of catalogLocales()) {
  test(`the ${locale} catalog is complete`, () => {
    const problems = checkCatalog(readCatalog(locale), scanSources());
    assert.deepEqual(problems, [], `run \`npm run i18n -- --sync\` and translate:\n${problems.join("\n")}`);
  });
}
