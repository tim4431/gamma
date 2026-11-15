// A scripted Codex for the README demos, as demo-model.mjs is a scripted AI
// model. Gamma starts it where it would start Codex CLI (GAMMA_AGENT_CODEX,
// docs/dev/local_agents.md "Finding the agents") and drives it through
// Codex's app-server protocol (codex-cli 0.167.0, "The drivers"; the test
// stand-in backend/tests/fake_agents/fake_codex.py speaks the same). Gamma
// builds every prompt, runs the turns, shows their steps and writes the
// replies in the threads; this process is only the agent.
//
// What a turn does is its story's (agents/<story>.mjs): the turn whose
// message is the comment Gamma hands over, its `check` of what Gamma sent
// (the thread's file, quote and source line, the session it resumed), then
// its steps done for real in the folder Gamma started it in, each reported
// as Codex reports it: an edit written (fileChange), a command run (the
// build: commandExecution), a call of Gamma's tools with the turn's own
// token (mcpToolCall), then its final message, which Gamma writes in the
// thread. Only the words and the choice of steps are written in advance. A
// turn the story does not know, or whose check fails, fails, so a take shows
// no answer Gamma did not ask for.
//
//   node demo-agent.mjs <story> <log dir> --version | login status | app-server
//
// <log dir>/turns.jsonl gets one line per turn (what Gamma sent, the steps
// and how each went, the reply, the problems): the recorder's checks read it.
// <log dir>/sessions.json keeps the sessions' turns, for a resumed one.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import readline from 'node:readline';
import { spawn } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const [story, logDir, ...args] = process.argv.slice(2);
const GAMMA = 'gamma_turn';   // the server Gamma names in a thread's config for its tools (events.MCP_NAME)
const TURN = 'demo-turn';

function send(message) { process.stdout.write(JSON.stringify(message) + '\n'); }
const notify = (method, params) => send({ method, params });
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function record(entry) {
  fs.mkdirSync(logDir, { recursive: true });
  fs.appendFileSync(path.join(logDir, 'turns.jsonl'), JSON.stringify({ at: new Date().toISOString(), ...entry }) + '\n');
}
const SESSIONS = () => path.join(logDir, 'sessions.json');
function sessions() { try { return JSON.parse(fs.readFileSync(SESSIONS(), 'utf8')); } catch { return {}; } }
function remember(thread, name) {
  const all = sessions();
  all[thread] = [...(all[thread] || []), name];
  fs.mkdirSync(logDir, { recursive: true });
  fs.writeFileSync(SESSIONS(), JSON.stringify(all, null, 1));
}

/** The prompt's JSON block that holds the person's `message` (a chat's or a
 *  thread's turn: gamma/local_agents/prompt.py), {} for none. */
function blockOf(prompt) {
  for (const found of prompt.matchAll(/```json\n([\s\S]*?)\n```/g)) {
    try {
      const block = JSON.parse(found[1]);
      if (block && typeof block === 'object' && 'message' in block) return block;
    } catch {}
  }
  return {};
}

/** A unified diff of one change, for the fileChange item. */
function diffOf(rel, before, after) {
  const a = before.split('\n'), b = after.split('\n');
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length, endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB--; }
  return [`--- a/${rel}`, `+++ b/${rel}`, `@@ -${start + 1},${endA - start} +${start + 1},${endB - start} @@`,
    ...a.slice(start, endA).map((l) => `-${l}`), ...b.slice(start, endB).map((l) => `+${l}`)].join('\n') + '\n';
}

function run(command, cwd) {
  return new Promise((resolve) => {
    const child = spawn('/bin/sh', ['-c', command], { cwd, stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '';
    child.stdout.on('data', (d) => { out += d; });
    child.stderr.on('data', (d) => { out += d; });
    const timer = setTimeout(() => child.kill('SIGKILL'), 120000);
    child.on('close', (code) => { clearTimeout(timer); resolve({ code: code ?? -1, output: out.slice(-4000) }); });
    child.on('error', (e) => { clearTimeout(timer); resolve({ code: -1, output: String(e) }); });
  });
}

/** One tools/call to Gamma's /mcp with the turn's token, as the stateless
 *  JSON transport takes it (backend/tests/fake_agents/gamma_call.py). */
async function callGamma(server, tool, args) {
  const token = process.env[server.bearer_token_env_var || ''] || '';
  try {
    const answer = await fetch(server.url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream',
        'MCP-Protocol-Version': '2025-11-25', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: tool, arguments: args } }),
    });
    const found = await answer.json().catch(() => ({}));
    const result = found.result || {};
    const text = (result.content || []).find((p) => p.type === 'text')?.text || JSON.stringify(found).slice(0, 500);
    return { http: answer.status, error: !answer.ok || !!result.isError || 'error' in found, text: text.slice(0, 2000) };
  } catch (e) {
    return { http: 0, error: true, text: String(e) };
  }
}

const state = { config: {}, quiet: [], thread: '', resumed: false, interrupted: false, busy: false };

function item(method, body) {
  notify(method, { threadId: state.thread, turnId: TURN, item: body, startedAtMs: Date.now(), completedAtMs: Date.now() });
}
function complete(status, error = '') {
  notify('turn/completed', { threadId: state.thread, turn: { id: TURN, status, items: [], error: error ? { message: error } : null } });
}

async function turn(request) {
  const params = request.params || {};
  const prompt = (params.input || []).filter((p) => p.type === 'text').map((p) => p.text).join('\n');
  const block = blockOf(prompt);
  const cwd = process.cwd();
  notify('turn/started', { threadId: state.thread, turn: { id: TURN, status: 'inProgress', items: [] } });
  send({ id: request.id, result: { turn: { id: TURN, status: 'inProgress', items: [] } } });
  const { turns } = await import(pathToFileURL(path.join(HERE, 'agents', `${story}.mjs`)).href);
  const script = turns.find((t) => t.message === block.message);
  const before = sessions()[state.thread] || [];
  const seen = { message: block.message, about: block.about, thread: block.thread, resumed: state.resumed, before, cwd };
  const steps = [];
  const fail = (problem) => {
    record({ turn: script?.name || null, session: state.thread, ...seen, steps, problems: [problem] });
    notify('error', { threadId: state.thread, turnId: TURN, willRetry: false, error: { message: problem } });
    complete('failed');
  };
  if (!script) return fail(`The demo agent has no turn for the message ${JSON.stringify(block.message ?? null)}.`);
  const problems = script.check ? script.check(seen) : [];
  if (problems.length) return fail(`The demo agent's ${script.name} turn was not handed what it expects: ${problems.join('; ')}`);
  const gamma = (state.config.mcp_servers || {})[GAMMA] || {};
  for (const [n, step] of script.steps.entries()) {
    if (state.interrupted) { record({ turn: script.name, session: state.thread, ...seen, steps, stopped: true }); return complete('interrupted'); }
    if (step.think) {
      const end = Date.now() + step.think * 1000;
      while (Date.now() < end && !state.interrupted) await sleep(50);
      continue;
    }
    if (step.edit) {
      const file = path.join(cwd, step.edit);
      const text = fs.readFileSync(file, 'utf8');
      const count = text.split(step.old).length - 1;
      if (count !== 1) return fail(`${step.edit} holds the text to replace ${count} times, not once: ${JSON.stringify(step.old.slice(0, 80))}`);
      const after = text.replace(step.old, step.new);
      const change = { path: file, kind: { type: 'update', move_path: null }, diff: diffOf(step.edit, text, after) };
      item('item/started', { type: 'fileChange', id: `fc${n}`, changes: [change], status: 'inProgress' });
      fs.writeFileSync(file, after);
      item('item/completed', { type: 'fileChange', id: `fc${n}`, changes: [change], status: 'completed' });
      steps.push({ edit: step.edit, ok: true });
    } else if (step.run) {
      const body = { type: 'commandExecution', id: `ce${n}`, command: step.run, cwd, commandActions: [],
        status: 'inProgress', aggregatedOutput: null, exitCode: null };
      item('item/started', body);
      const { code, output } = await run(step.run, cwd);
      item('item/completed', { ...body, status: code === 0 ? 'completed' : 'failed', aggregatedOutput: output, exitCode: code });
      steps.push({ run: step.run, ok: code === 0, code, output: output.slice(-600) });
      if (code !== 0) return fail(`\`${step.run}\` exited ${code}: ${output.trim().split('\n').slice(-3).join(' ')}`);
    } else if (step.gamma) {
      if (!gamma.url) return fail('Gamma named no tools for the turn.');
      const named = { $thread: block.thread?.thread_id || '' };
      const callArgs = Object.fromEntries(Object.entries(step.arguments || {}).map(([k, v]) => [k, named[v] ?? v]));
      const body = { type: 'mcpToolCall', id: `mc${n}`, server: GAMMA, tool: step.gamma, arguments: callArgs, status: 'inProgress' };
      item('item/started', body);
      const got = await callGamma(gamma, step.gamma, callArgs);
      item('item/completed', { ...body, status: got.error ? 'failed' : 'completed', result: { content: [{ type: 'text', text: got.text }] } });
      steps.push({ gamma: step.gamma, arguments: callArgs, ok: !got.error, http: got.http, text: got.text.slice(0, 400) });
      if (got.error) return fail(`Gamma's ${step.gamma} answered ${got.http}: ${got.text.slice(0, 300)}`);
    }
  }
  remember(state.thread, script.name);
  const text = script.reply;
  item('item/started', { type: 'agentMessage', id: 'am0', text: '' });
  if (!state.quiet.includes('item/agentMessage/delta')) {
    for (const piece of text.match(/\s*\S+/g) || [text]) {
      notify('item/agentMessage/delta', { threadId: state.thread, turnId: TURN, itemId: 'am0', delta: piece });
      await sleep(script.typing ?? 30);
    }
  }
  item('item/completed', { type: 'agentMessage', id: 'am0', text });
  record({ turn: script.name, session: state.thread, ...seen, steps, reply: text, problems: [] });
  notify('thread/tokenUsage/updated', { threadId: state.thread, turnId: TURN, tokenUsage: {
    total: { inputTokens: 0, cachedInputTokens: 0, outputTokens: 0, reasoningOutputTokens: 0, totalTokens: 0 },
    last: { inputTokens: 0, cachedInputTokens: 0, outputTokens: 0, reasoningOutputTokens: 0, totalTokens: 0 } } });
  complete('completed');
}

function handle(message) {
  const { method, id, params = {} } = message;
  if (method === 'turn/interrupt') { state.interrupted = true; if (id != null) send({ id, result: {} }); return; }
  if (method == null || id == null) return;   // a notification (initialized), or an answer
  if (method === 'initialize') {
    state.quiet = params.capabilities?.optOutNotificationMethods || [];
    send({ id, result: { userAgent: 'codex-demo', codexHome: '', platformFamily: '', platformOs: '' } });
  } else if (method === 'model/list') {
    send({ id, result: { data: [], nextCursor: null } });   // no model of its own to name
  } else if (method === 'windowsSandbox/readiness') {
    send({ id, result: { status: 'ready' } });
  } else if (method === 'config/read') {
    send({ id, result: { config: {}, origins: {} } });
  } else if (method === 'thread/start' || method === 'thread/resume') {
    if (method === 'thread/resume' && !(params.threadId in sessions())) {
      send({ id, error: { code: -32600, message: `no rollout found for thread id ${params.threadId}` } });
      return;
    }
    state.config = params.config || {};
    state.resumed = method === 'thread/resume';
    state.thread = params.threadId || `demo-${crypto.randomUUID()}`;
    send({ id, result: { thread: { id: state.thread, status: { type: 'idle' }, turns: [] },
      sandbox: { type: 'workspaceWrite', writableRoots: [], networkAccess: false },
      approvalPolicy: params.approvalPolicy, approvalsReviewer: 'user', cwd: params.cwd || process.cwd(),
      model: 'demo', modelProvider: 'demo' } });
    const gamma = (state.config.mcp_servers || {})[GAMMA];
    if (gamma?.url) {
      notify('mcpServer/startupStatus/updated', { name: GAMMA, status: 'starting', threadId: null });
      const ready = !!process.env[gamma.bearer_token_env_var || ''];
      notify('mcpServer/startupStatus/updated', { name: GAMMA, status: ready ? 'ready' : 'failed', threadId: null,
        ...(ready ? {} : { error: `Environment variable ${gamma.bearer_token_env_var} is not set` }) });
    }
  } else if (method === 'thread/name/set') {
    send({ id, result: {} });
  } else if (method === 'turn/steer') {
    send({ id, result: { turnId: TURN } });
  } else if (method === 'turn/start') {
    state.busy = true;
    state.interrupted = false;
    turn(message).catch((e) => {
      record({ turn: null, problems: [String(e && e.stack || e)] });
      notify('error', { threadId: state.thread, turnId: TURN, willRetry: false, error: { message: `The demo agent failed: ${e}` } });
      complete('failed');
    }).finally(() => { state.busy = false; });
  } else {
    send({ id, error: { code: -32601, message: `demo agent: no method ${method}` } });
  }
}

if (!story || !logDir) {
  console.error('usage: node demo-agent.mjs <story> <log dir> --version | login status | app-server');
  process.exit(2);
}
if (args[0] === '--version') {
  console.log('codex-cli 0.167.0 (the README demos’ stand-in)');
} else if (args[0] === 'login' && args[1] === 'status') {
  console.log('Logged in using ChatGPT');
} else if (args[0] === 'app-server') {
  const lines = readline.createInterface({ input: process.stdin });
  lines.on('line', (line) => {
    let message;
    try { message = JSON.parse(line); } catch { return; }
    handle(message);
  });
  // Gamma closes stdin once the turn ended; a turn still running finishes first.
  lines.on('close', async () => { while (state.busy) await sleep(50); process.exit(0); });
} else {
  console.error(`demo agent: unknown command ${JSON.stringify(args)}`);
  process.exit(2);
}
