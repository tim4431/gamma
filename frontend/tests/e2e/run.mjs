// Browser end-to-end suite: drives the built app (frontend/dist) served by an
// ISOLATED backend over a throwaway data dir, through Playwright's Chromium.
//
//   npm run e2e                 # everything, on parallel workers (build first: npm run build)
//   npm run e2e -- --jobs 1     # one worker: the groups in order, live output
//   npm run e2e -- --changed    # only the groups the working tree's changes need (select.mjs)
//   npm run e2e -- --changed main --list   # what a branch needs, without running it
//   npm run e2e -- --group ink,collab      # these groups
//   npm run e2e -- --only pdf   # steps whose name contains "pdf" (one worker unless --jobs)
//   npm run e2e -- --continue   # keep going after a failure
//   npm run e2e -- --headed     # watch it
//   npm run e2e -- --keep       # leave the temp data dirs + server.log behind
//
// One line per step and a summary; exit 1 on any failure. Each step asserts
// both the visible outcome AND that no API call failed, no console error and
// no page error happened meanwhile (harness.openPage records them).
//
// The scenarios run in GROUPS. Each worker is its own process with its own
// backend, data dir and browser, and takes the next group off the queue
// (longest first) until none are left; a group's steps print together when
// it ends. Groups share nothing across workers, so a scenario that needs
// another's data belongs in the same group (notes → pdf → share). The
// groups and which source changes select them live in select.mjs.
import { fork } from "node:child_process";
import os from "node:os";
import { fileURLToPath } from "node:url";
import {
  Account, Server, assert, assertEq, assertNoProblems, flags, launchBrowser, makePdf,
  openPage, results, sleep, step, until,
} from "./harness.mjs";
import { noteScenarios } from "./scenarios/notes.mjs";
import { mermaidScenarios } from "./scenarios/mermaid.mjs";
import { pdfScenarios } from "./scenarios/pdf.mjs";
import { fileScenarios } from "./scenarios/files.mjs";
import { collabScenarios } from "./scenarios/collab.mjs";
import { shareScenarios } from "./scenarios/share.mjs";
import { settingsScenarios } from "./scenarios/settings.mjs";
import { mcpScenarios } from "./scenarios/mcp.mjs";
import { mirrorScenarios } from "./scenarios/mirror.mjs";
import { mentionScenarios } from "./scenarios/mentions.mjs";
import { chatNavigationScenarios } from "./scenarios/chatNavigation.mjs";
import { transferScenarios } from "./scenarios/transfers.mjs";
import { inkScenarios } from "./scenarios/ink.mjs";
import { guideScenarios } from "./scenarios/guide.mjs";
import { contextualGuideScenarios } from "./scenarios/contextualGuide.mjs";
import { triggeredGuideScenarios } from "./scenarios/triggeredGuide.mjs";
import { inkEditingScenarios } from "./scenarios/inkEditing.mjs";
import { pdfLoadScenarios } from "./scenarios/pdfload.mjs";
import { pdfTouchScenarios } from "./scenarios/pdfTouch.mjs";
import { ipadScenarios } from "./scenarios/ipad.mjs";
import { quickOpenScenarios } from "./scenarios/quickOpen.mjs";
import { cloudSignInScenarios } from "./scenarios/cloudSignIn.mjs";
import { publishScenarios } from "./scenarios/publish.mjs";
import { i18nScenarios } from "./scenarios/i18n.mjs";
import { authScenarios } from "./scenarios/auth.mjs";
import { GROUPS, changedFromGit, selectGroups } from "./select.mjs";

const RUNNERS = {
  "guide": guideScenarios,
  "settings": settingsScenarios,
  "notes-pdf-share": async (env) => {
    const notes = await noteScenarios(env);
    const pdf = await pdfScenarios(env, notes);
    await shareScenarios(env, { ...notes, ...pdf });
  },
  "contextual-guide": contextualGuideScenarios,
  "triggered-guide": triggeredGuideScenarios,
  "ink-editing": inkEditingScenarios,
  "pdf-load": pdfLoadScenarios,
  "ink": inkScenarios,
  "pdf-touch": pdfTouchScenarios,
  "publish": publishScenarios,
  "chat-navigation": chatNavigationScenarios,
  "mentions": mentionScenarios,
  "collab": collabScenarios,
  "mirror": mirrorScenarios,
  "transfers": transferScenarios,
  "mcp": mcpScenarios,
  "files": fileScenarios,
  "mermaid": mermaidScenarios,
  "i18n": i18nScenarios,
  "cloud-sign-in": cloudSignInScenarios,
  "auth": authScenarios,
  "quick-open": quickOpenScenarios,
  "ipad": ipadScenarios,
};

const missing = GROUPS.filter((g) => !RUNNERS[g.id]).map((g) => g.id);
if (missing.length || Object.keys(RUNNERS).length !== GROUPS.length) throw new Error(`select.mjs GROUPS and RUNNERS differ (${missing.join(", ")})`);

// The value after a flag, unless the next word is another flag.
const argValue = (name) => {
  const i = process.argv.indexOf(name);
  return i > 0 && process.argv[i + 1] && !process.argv[i + 1].startsWith("--") ? process.argv[i + 1] : undefined;
};

if (process.argv.includes("--worker")) await worker();
else {
  const selected = selection();
  const jobs = Math.max(1, Number(argValue("--jobs")) ||
    (flags.only ? 1 : Math.min(6, Math.floor((os.availableParallelism?.() || os.cpus().length) / 2))));
  if (process.argv.includes("--list")) process.exit(0);
  if (!selected.length) { console.log("no browser-suite group covers these changes"); process.exit(0); }
  if (Math.min(jobs, selected.length) === 1) await sequential(selected);
  else await parallel(selected, jobs);
}

// The group ids to run, in queue order: --group ids, --changed [ref]'s
// selection (printed with the files behind each group), or every group.
function selection() {
  const known = GROUPS.map((g) => g.id);
  if (process.argv.includes("--group")) {
    const ids = (argValue("--group") || "").split(",").map((s) => s.trim()).filter(Boolean);
    const unknown = ids.filter((id) => !known.includes(id));
    if (unknown.length || !ids.length) {
      console.log(`unknown group ${unknown.join(", ") || "(none given)"}; groups: ${known.join(", ")}`);
      process.exit(2);
    }
    return known.filter((id) => ids.includes(id));
  }
  if (process.argv.includes("--changed")) {
    const ref = argValue("--changed");
    let changed;
    try { changed = changedFromGit(ref); } catch (e) {
      console.log(`--changed: ${String(e.stderr || e.message).trim()}`);
      process.exit(2);
    }
    const { base, files, lines } = changed;
    const { groups, reasons, all } = selectGroups(files, lines);
    console.log(`${files.length} changed file${files.length === 1 ? "" : "s"} against ${ref ? `${ref} (merge base ${base.slice(0, 10)})` : "HEAD"}`);
    if (all) console.log(`  every group: ${all} is shared by all of them`);
    else for (const id of groups) {
      const why = reasons[id];
      console.log(`  ${id.padEnd(17)} ← ${why.slice(0, 3).join(", ")}${why.length > 3 ? ` (+${why.length - 3})` : ""}`);
    }
    console.log(`${groups.length} of ${known.length} groups selected\n`);
    return groups;
  }
  if (process.argv.includes("--list")) console.log(known.join("\n"));
  return known;
}

// A backend with alice and bob, and a browser: what every group runs against.
async function startEnv() {
  const server = new Server();
  await server.start();
  server.manage("create-user", "alice", "alice-pw");
  server.manage("create-user", "bob", "bob-pw");
  const alice = await new Account(server, "alice", "alice-pw").login();
  const bob = await new Account(server, "bob", "bob-pw").login();
  const browser = await launchBrowser();
  return { server, browser, alice, bob, makePdf, step, until, sleep, assert, assertEq, assertNoProblems, openPage, flags };
}

async function stopEnv(env) {
  if (env?.browser) await env.browser.close().catch(() => {});
  if (env?.server) await env.server.stop();
}

function summary(all, t0) {
  const failed = all.filter((r) => !r.ok);
  console.log(`\n${all.length - failed.length}/${all.length} steps passed in ${Math.round((Date.now() - t0) / 1000)} s` +
    `${failed.length ? `; failed: ${failed.map((f) => f.name).join(", ")}` : ""}`);
  process.exit(failed.length || !all.length ? 1 : 0);
}

// One process, one backend, the groups in order, output as it happens.
async function sequential(ids) {
  const t0 = Date.now();
  let env;
  try {
    console.log("starting isolated backend...");
    env = await startEnv();
    console.log(`  ${env.server.base}  data: ${env.server.dataDir}`);
    for (const id of ids) await RUNNERS[id](env);
  } catch (e) {
    if (!results.length || results[results.length - 1].ok) console.log(`\nsetup failed: ${e.stack || e}`);
  } finally {
    await stopEnv(env);
  }
  summary(results, t0);
}

// The parent: forks the workers, hands out groups, prints each group's
// output as one block when it ends. Without --continue a failure stops the
// hand-out; groups already running finish.
async function parallel(ids, jobs) {
  const t0 = Date.now();
  const queue = [...ids];
  const all = [];
  let stopping = false;
  console.log(`running ${queue.length} groups on ${jobs} workers, each with its own backend and browser...`);
  const script = fileURLToPath(import.meta.url);
  const args = process.argv.slice(2).filter((a, i, list) => a !== "--jobs" && list[i - 1] !== "--jobs");
  await Promise.all(Array.from({ length: Math.min(jobs, queue.length) }, (_, n) => new Promise((resolve) => {
    const child = fork(script, [...args, "--worker"], { stdio: ["ignore", "inherit", "inherit", "ipc"] });
    const next = () => {
      const group = !stopping && queue.shift();
      if (group) child.send({ group });
      else child.send({ done: true });
    };
    child.on("message", (m) => {
      if (m.ready) return next();
      if (m.setupFailed) {
        console.log(`\nworker ${n + 1}: setup failed: ${m.setupFailed}`);
        all.push({ name: `worker ${n + 1} setup`, ok: false });
        return child.send({ done: true });
      }
      all.push(...m.results);
      const bad = m.results.some((r) => !r.ok) || m.error;
      if (bad && !flags.continueOnFail) stopping = true;
      if (m.output.length || m.error) {
        console.log(`\n── ${m.group}  (${Math.round(m.ms / 1000)} s, worker ${n + 1})`);
        if (m.output.length) console.log(m.output.join("\n"));
        if (m.error) console.log(`  group aborted: ${m.error}`);
      }
      next();
    });
    child.on("exit", (code) => {
      if (code) all.push({ name: `worker ${n + 1} exited with ${code}`, ok: false });
      resolve();
    });
  })));
  if (stopping && queue.length) console.log(`\nnot run after the failure: ${queue.join(", ")}`);
  summary(all, t0);
}

// A worker: its own backend and browser, one group per message. Output is
// collected per group and sent with its results; "kept: <dir>" and setup
// errors print directly.
async function worker() {
  let env;
  try {
    env = await startEnv();
  } catch (e) {
    process.send({ setupFailed: String(e.stack || e) });
  }
  process.on("message", async (m) => {
    if (m.done) {
      await stopEnv(env);
      process.disconnect();
      // a scenario's stray handle (a fake server, a socket) must not keep the worker up
      setTimeout(() => process.exit(0), 500).unref();
      return;
    }
    const run = RUNNERS[m.group];
    const before = results.length;
    const output = [];
    const log = console.log;
    console.log = (...a) => output.push(a.join(" "));
    const t0 = Date.now();
    let error = "";
    try {
      await run(env);
    } catch (e) {
      // A failed step already printed itself; anything else is the group's own setup.
      if (results.length === before || results[results.length - 1].ok) error = String(e.stack || e);
    } finally {
      console.log = log;
    }
    process.send({ group: m.group, ms: Date.now() - t0, results: results.slice(before), output, error });
  });
  if (env) process.send({ ready: true });
}
