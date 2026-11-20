// Skills in the chat (chat/skills.js): where "/" opens the picker, what a
// pick inserts, how a message as typed splits into the skill it runs and
// its text, the picker's ranking, how such a message reads as text and as a
// title (tests/shared/skilltitles.json holds gamma/skills.py to the same
// answers), and the built-ins' words the catalog translates.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";
import { BUILTIN_TEXT, insertSkill, rankSkills, skillAt, skillText, skillTitle, splitSkill } from "../src/chat/skills.js";
import { actionSummary } from "../src/chat/agentSteps.js";

const SHARED = JSON.parse(readFileSync(new URL("../../tests/shared/skilltitles.json", import.meta.url), "utf8"));
const BUILTINS = new URL("../../backend/gamma/builtin_skills/", import.meta.url);

const catalog = [
  { id: "a1", name: "referee", description: "Write a referee report", argument_hint: "", source: "account" },
  { id: "builtin:review", name: "review", description: "A referee-style critique of the paper", source: "builtin" },
  { id: "builtin:summarize", name: "summarize", description: "A structured summary of the paper", source: "builtin" },
  { id: "a2", name: "translate-zh", description: "把论文段落翻译成中文", source: "account" },
];

test("the picker opens only for a / at the message's start, the caret in the word after it", () => {
  assert.deepEqual(skillAt("/su", 3), { start: 0, end: 3, query: "su" });
  assert.deepEqual(skillAt("  /", 3), { start: 2, end: 3, query: "" });
  assert.deepEqual(skillAt("/sum rest", 2), { start: 0, end: 4, query: "s" }, "a pick replaces the whole word");
  assert.deepEqual(skillAt("/总结", 3), { start: 0, end: 3, query: "总结" });
  for (const [text, at] of [["see /usr", 8], ["a\n/b", 4], ["/sum rest", 9], ["/sum ", 5]]) assert.equal(skillAt(text, at), null, text);
  assert.equal(skillAt("/sum", 1, 3), null, "not over a selection");
});

test("a pick puts /name and a space in place of the word, the caret after them", () => {
  assert.deepEqual(insertSkill("/su", skillAt("/su", 3), "summarize"), { text: "/summarize ", caret: 11 });
  const text = "/sm   eq. 3";
  assert.deepEqual(insertSkill(text, skillAt(text, 3), "summarize"), { text: "/summarize eq. 3", caret: 11 });
});

test("a message runs a skill only by a name in the catalog, followed by a blank or its end", () => {
  assert.deepEqual(splitSkill("/summarize  for a journal club \n", catalog),
    { skill: { id: "builtin:summarize", name: "summarize", args: "for a journal club" }, text: "for a journal club" });
  assert.deepEqual(splitSkill(" /review", catalog), { skill: { id: "builtin:review", name: "review", args: "" }, text: "" });
  for (const raw of ["/usr/bin/env", "/ 2", "/reviews", "/Review", "see /review", "/nothing here"]) {
    assert.deepEqual(splitSkill(raw, catalog), { skill: null, text: raw }, raw);
  }
  assert.deepEqual(splitSkill("/review x", []), { skill: null, text: "/review x" }, "an unknown catalog sends text");
});

test("the ranking: a name it begins, then a word of the description, then a fuzzy name", () => {
  const names = (q) => rankSkills(catalog, q).map((r) => r.name);
  assert.deepEqual(names(""), ["referee", "review", "summarize", "translate-zh"], "every skill in the catalog's order");
  assert.deepEqual(names("re"), ["referee", "review"]);
  assert.deepEqual(names("review"), ["review"]);
  assert.deepEqual(names("refer"), ["referee", "review"], "review's description has a word that begins with it");
  assert.deepEqual(names("sz"), ["summarize"], "fuzzy on the name");
  assert.deepEqual(names("翻译"), ["translate-zh"], "a query in Chinese finds a word inside a description");
  assert.deepEqual(names("xyz"), []);
  const [top] = rankSkills(catalog, "struct");
  assert.equal(top.name, "summarize");
  assert.deepEqual(top.match.description, [2, 3, 4, 5, 6, 7], "the letters to mark");
});

test("a message reads as the server's display and title read it", () => {
  for (const { skill, text, display, title } of SHARED.cases) {
    const message = { role: "user", text, ...(skill ? { skill } : {}) };
    assert.equal(skillText(message), display, `display of ${JSON.stringify(message)}`);
    assert.equal(skillTitle(message), title, `title of ${JSON.stringify(message)}`);
  }
});

// A front matter key's value as gamma/skills.py parse_skill_md reads the
// shipped files' (plain or double-quoted, on one line).
const frontKey = (text, key) => {
  const value = new RegExp(`^${key}:[ \\t]*(.*)$`, "m").exec(text.split(/^---$/m)[1])?.[1].trim() ?? "";
  return /^".*"$/.test(value) ? JSON.parse(value) : value;
};

test("every shipped built-in's description and argument hint is a catalog string", () => {
  const folders = readdirSync(BUILTINS, { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name);
  assert.ok(folders.length, "built-in skills ship");
  for (const name of folders) {
    const text = readFileSync(new URL(`${name}/SKILL.md`, BUILTINS), "utf8");
    for (const key of ["description", "argument-hint"]) {
      const value = frontKey(text, key);
      if (value) assert.ok(BUILTIN_TEXT.includes(value), `${name}'s ${key} is in BUILTIN_TEXT: ${value}`);
    }
  }
});

test("a skill the agent failed to load reads as its error", () => {
  const failed = { kind: "skill", tool: "load_skill", args: { name: "nope" }, error: true, summary: "error: no skill nope" };
  assert.equal(actionSummary(failed), "error: no skill nope");
});
