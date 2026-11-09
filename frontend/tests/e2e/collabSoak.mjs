// The crowd soak for real-time collaboration from the command line
// (docs/dev/collab.md "Testing", debugging.md "Browser end-to-end suite"):
// N tabs, each the app's own page session over Node's fetch and WebSocket,
// typing on a few pages of a shared workspace on an isolated server, then
// quiet until every tab shows the server's tree. The engine and its oracle
// are scenarios/collabSoak.mjs, whose short run is the collab-soak group.
// No build needed: the server serves the API alone.
//
//   node tests/e2e/collabSoak.mjs                          # 20 tabs on 5 pages for 120 s
//   node tests/e2e/collabSoak.mjs --faults                 # socket drops and 4 s outages
//   node tests/e2e/collabSoak.mjs --seconds 300 --strict   # memory, pages.db and fan-out p99 must level off
//   node tests/e2e/collabSoak.mjs --clients 8 --accounts 2 --pages 1 --seed 7 --keep-live 10 --json out.json
//   node tests/e2e/collabSoak.mjs --sabotage --seconds 20  # the oracle's own check: one blind replace, exit 1 naming it
//
// It prints a table per 30 s window (batches/s; ack, fan-out and visible
// latency p50/p95/p99; server memory, pages.db + WAL; driver event-loop
// delay p99) and the totals, and exits 1 on any failure: a tab differing
// from the server, a token lost but by the merge's rules (each loss is
// replayed through gamma/textmerge.py), a token there twice or kept after
// its delete, a shared block gone or a deleted note back, an op log past
// its bound, a 5xx, a traceback in server.log; with --strict, also memory
// or pages.db growing over the last third, or a window's fan-out p99 past
// max(3 × the first full window's, 50 ms). A failed run keeps the server's
// temp dir with forensic.json (the report, and base, ours, theirs, stored
// and the merge for each token lost).
import fs from "node:fs";
import path from "node:path";
import { inspect } from "node:util";
import { Account, Server, argValue, flags } from "./harness.mjs";
import { levelingFailures, printReport, runSoak, seedPages } from "./scenarios/collabSoak.mjs";

const num = (name, fallback, min = 1) => {
  const value = Number(argValue(name, String(fallback)));
  if (!Number.isInteger(value) || value < min) throw new Error(`${name} takes a whole number from ${min} (got ${argValue(name)})`);
  return value;
};
const clients = num("--clients", 20);
const accounts = Math.min(num("--accounts", clients), clients);
const pageCount = num("--pages", 5);
const seconds = num("--seconds", 120);
const seed = num("--seed", 1, 0);
const keepLive = num("--keep-live", 25, 0);
const faults = process.argv.includes("--faults");
const strict = process.argv.includes("--strict");
const sabotage = process.argv.includes("--sabotage");
const jsonOut = argValue("--json", "");

const server = new Server({ static: false });
let code = 1;
try {
  await server.start();
  const t0 = Date.now();
  // manage.py takes most of a second a call: four at a time
  const names = Array.from({ length: accounts }, (_, i) => `soak${i}`);
  const batches = async (list, fn) => { for (let k = 0; k < list.length; k += 4) await Promise.all(list.slice(k, k + 4).map(fn)); };
  await batches(names, (name) => server.manage("create-user", name, `${name}-pw`));
  const out = await server.manage("create-workspace", "Soak", names[0], "shared");
  const team = (out.match(/workspace (\S+)/) || [])[1];
  if (!team) throw new Error(`no workspace id in: ${out}`);
  await batches(names.slice(1), (name) => server.manage("set-member", team, name, "editor"));
  const signedIn = [];
  for (const name of names) {
    const account = await new Account(server, name, `${name}-pw`).login();
    account.ws = team;
    signedIn.push(account);
  }
  const pages = await seedPages(signedIn[0], pageCount, { seed });
  console.log(`setup ${((Date.now() - t0) / 1000).toFixed(1)} s: ${accounts} accounts, ${pageCount} pages, server pid ${server.proc.pid} at ${server.base}`);
  const report = await runSoak({ server, accounts: signedIn, workspace: team, pages, clients, seconds, seed, faults, keepLive, sabotage, log: console.log });
  if (strict) report.failures.push(...levelingFailures(report));
  console.log("");
  printReport(report);
  if (jsonOut) fs.writeFileSync(jsonOut, JSON.stringify(report, null, 1));
  if (report.failures.length) {
    fs.writeFileSync(path.join(server.dir, "forensic.json"), JSON.stringify(report, null, 1));
    flags.keep = true;
  }
  code = report.failures.length ? 1 : 0;
} catch (err) {
  console.log(`soak failed: ${inspect(err)}`);
  flags.keep = true;
} finally {
  await server.stop();
}
process.exit(code);
