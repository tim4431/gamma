import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Server, results, sweepRuns } from "./e2e/harness.mjs";

// What the browser suite leaves in the system temp directory, without starting
// a backend: the harness's folders, in a temp directory of the test's own.
function ownTemp(t) {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "e2e-folders-"));
  const saved = Object.fromEntries(["TMPDIR", "TEMP", "TMP"].map((key) => [key, process.env[key]]));
  for (const key of Object.keys(saved)) process.env[key] = temp;
  t.after(() => {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    fs.rmSync(temp, { recursive: true, force: true });
  });
  return temp;
}

test("a passing run removes its folder", async (t) => {
  const temp = ownTemp(t);
  const server = new Server();
  fs.mkdirSync(path.join(server.dir, "dist"));
  await server.stop();
  assert.deepEqual(fs.readdirSync(temp), []);
});

test("a failed run keeps its folder without the copy of the build", async (t) => {
  ownTemp(t);
  const server = new Server();
  for (const name of ["dist", "data", "failures"]) fs.mkdirSync(path.join(server.dir, name));
  results.push({ name: "a failed step", ok: false });
  t.after(() => results.pop());
  await server.stop();
  assert.deepEqual(fs.readdirSync(server.dir).sort(), ["data", "failures", "tmp"]);
});

test("a run sweeps old folders of the suite's and nothing else", (t) => {
  const temp = ownTemp(t);
  const days = (n) => new Date(Date.now() - n * 24 * 60 * 60 * 1000);
  const folder = (name, age) => {
    fs.mkdirSync(path.join(temp, name));
    fs.utimesSync(path.join(temp, name), days(age), days(age));
  };
  folder("gamma-e2e-old", 4);
  folder("gamma-e2e-recent", 1);
  folder("something-else", 4);
  sweepRuns();
  assert.deepEqual(fs.readdirSync(temp).sort(), ["gamma-e2e-recent", "something-else"]);
});
