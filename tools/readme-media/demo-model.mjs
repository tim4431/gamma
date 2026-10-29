// A scripted chat model for the README recordings: an OpenAI-compatible
// service (Chat Completions, streamed) that Gamma reaches like any other
// connection. Gamma builds the request, runs the tools the model calls,
// shows their steps and approval cards and makes the edits; only the model's
// words and tool calls come from a script, so a take plays the same way
// every time.
//
//   node demo-model.mjs <script> '<json args>'
// prints {"port": N} once listening on 127.0.0.1. take.mjs demoModel() starts
// it and saves the connection. GET /__log lists one entry per request.
//
// A script is models/<script>.mjs exporting
//   reply(req, ctx)     -> { text } | { calls: [{ name, args }] }  (may be async)
//   complete(req, ctx)  -> string, for a non-streamed call (optional; "ok")
//   pace                -> overrides of PACE (optional)
// `req` is the request read for a script: { question, userText, system,
// images, tools, pageId, cursor, calls: this reply's tool calls so far with
// their results, body }. `ctx.args` are the json args, `ctx.note(obj)` adds
// to this request's log entry.
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const [scriptName, rawArgs = '{}'] = process.argv.slice(2);
if (!scriptName) throw new Error('usage: node demo-model.mjs <script> [json args]');
const script = await import(pathToFileURL(path.join(HERE, 'models', `${scriptName}.mjs`)).href);
const args = JSON.parse(rawArgs);
const MODEL = args.model || 'demo-model';
// Milliseconds. Text streams at `cps` visible characters a second with ±25 %
// jitter; tool calls stream their arguments in `argChunk` slices.
const PACE = { firstToken: 600, toolPause: 500, cps: 120, argChunk: 24, argMs: 6, ...(script.pace || {}), ...(args.pace || {}) };
const PROBE = 'Reply with the single word "ok".';
const TOOL_PICTURES = 'Pictures returned by the tool calls above';
const log = [];
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const textOf = (m) => (typeof m?.content === 'string' ? m.content
  : (m?.content || []).filter((p) => p.type === 'text').map((p) => p.text).join('\n'));

export function readRequest(body) {
  const messages = body.messages || [];
  // The turn the user wrote: the last user message that is not the pictures
  // a tool returned.
  let u = messages.length - 1;
  while (u >= 0 && !(messages[u].role === 'user' && !textOf(messages[u]).startsWith(TOOL_PICTURES))) u--;
  const user = messages[u] || {};
  const calls = [];
  for (const m of messages.slice(u + 1)) {
    if (m.role === 'assistant') for (const c of m.tool_calls || []) {
      let parsed = {};
      try { parsed = JSON.parse(c.function.arguments || '{}'); } catch {}
      calls.push({ id: c.id, name: c.function.name, args: parsed, result: null });
    }
    if (m.role === 'tool') { const c = calls.find((x) => x.id === m.tool_call_id); if (c) c.result = textOf(m); }
  }
  const userText = textOf(user);
  const all = messages.map(textOf).join('\n');
  const at = userText.lastIndexOf('User question: ');
  const question = (at >= 0 ? userText.slice(at + 15) : userText).split('\n\n')[0].trim();
  const cursor = all.match(/The user's cursor is on this note block[^\n]*\n- \[([^\]]+)\]\s*([^\n]*)/);
  return {
    question, userText, calls, body,
    system: messages[0]?.role === 'system' ? textOf(messages[0]) : '',
    images: (Array.isArray(user.content) ? user.content : []).filter((p) => p.type === 'image_url').map((p) => p.image_url.url),
    tools: (body.tools || []).map((t) => t.function?.name).filter(Boolean),
    pageId: (all.match(/Gamma page ID:\s*([\w-]+)/) || [])[1] || '',
    cursor: cursor ? { id: cursor[1], text: cursor[2] } : null,
  };
}

// Stream units: a Markdown link and a table row arrive whole (no half-drawn
// link or row; a table's header with its delimiter row, so the header never
// shows as raw pipes), the rest in words of up to six characters.
function pieces(text) {
  const out = [];
  const words = (s) => out.push(...(s.match(/\s*\S{1,6}|\s+/g) || []));
  const re = /\[[^\]\n]*\]\([^)\s]*\)|(?:^|\n)\|[^\n]*(?:\n\|[\s:|-]*-[\s:|-]*(?=\n|$))?/g;
  let i = 0, m;
  while ((m = re.exec(text))) { words(text.slice(i, m.index)); out.push(m[0]); i = re.lastIndex; }
  words(text.slice(i));
  return out;
}
const visible = (piece) => piece.replace(/\]\([^)]*\)/g, ']').replace(/[[\]|]/g, '').length;

let seed = 7;
const jitter = () => 0.75 + 0.5 * ((seed = (seed * 1103515245 + 12345) % 2 ** 31) / 2 ** 31);

function chunk(res, delta, finish = null) {
  res.write(`data: ${JSON.stringify({ id: 'demo', object: 'chat.completion.chunk', created: Math.floor(Date.now() / 1000), model: MODEL,
    choices: [{ index: 0, delta, finish_reason: finish }] })}\n\n`);
}

async function stream(res, decided, req) {
  res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' });
  if (decided.calls?.length) {
    await sleep(PACE.toolPause);
    for (const [index, call] of decided.calls.entries()) {
      const id = `call_demo_${Date.now().toString(36)}_${index}`;
      chunk(res, { ...(index ? {} : { role: 'assistant' }), tool_calls: [{ index, id, type: 'function', function: { name: call.name, arguments: '' } }] });
      const json = JSON.stringify(call.args);
      for (let i = 0; i < json.length; i += PACE.argChunk) {
        chunk(res, { tool_calls: [{ index, function: { arguments: json.slice(i, i + PACE.argChunk) } }] });
        await sleep(PACE.argMs);
      }
    }
    chunk(res, {}, 'tool_calls');
  } else {
    await sleep(req.calls.length ? PACE.toolPause : PACE.firstToken);
    chunk(res, { role: 'assistant', content: '' });
    for (const piece of pieces(decided.text || '')) {
      chunk(res, { content: piece });
      await sleep((visible(piece) / PACE.cps) * 1000 * jitter());
    }
    chunk(res, {}, 'stop');
  }
  // About 4 characters a token; an attachment is not counted as its base64
  // (which reads as millions and turns the context ring red): a picture as
  // ~850 tokens, a file (a PDF sent whole) as ~1 token per 300 bytes.
  const prompt = Math.round(JSON.stringify(req.body.messages || []).replace(/data:([\w.+-]+)\/[\w.+-]+;base64,([A-Za-z0-9+/=]+)/g,
    (_, kind, b64) => 'x'.repeat(4 * (kind === 'image' ? 850 : Math.ceil(b64.length * 0.75 / 300)))).length / 4);
  res.write(`data: ${JSON.stringify({ id: 'demo', object: 'chat.completion.chunk', model: MODEL, choices: [],
    usage: { prompt_tokens: prompt, completion_tokens: Math.round((decided.text || JSON.stringify(decided.calls)).length / 4) } })}\n\n`);
  res.write('data: [DONE]\n\n');
  res.end();
}

const server = http.createServer(async (request, res) => {
  try {
    if (request.method === 'GET' && request.url.startsWith('/__log')) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify(log));
    }
    if (request.method === 'GET' && /\/v1\/models/.test(request.url)) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ object: 'list', data: [{ id: MODEL, object: 'model', context_window: 200000 }] }));
    }
    if (request.method !== 'POST' || !/\/chat\/completions/.test(request.url)) { res.writeHead(404); return res.end(); }
    let raw = '';
    for await (const part of request) raw += part;
    const body = JSON.parse(raw);
    const req = readRequest(body);
    const entry = { t: Date.now(), stream: !!body.stream, question: req.question.slice(0, 200), calls: req.calls.map((c) => c.name) };
    log.push(entry);
    const ctx = { args, note: (obj) => Object.assign(entry, obj) };
    if (!body.stream) {
      const all = JSON.stringify(body.messages || []);
      const content = all.includes(PROBE) ? 'ok' : (await script.complete?.(req, ctx)) ?? 'ok';
      entry.reply = content.slice(0, 200);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ id: 'demo', object: 'chat.completion', model: MODEL,
        choices: [{ index: 0, message: { role: 'assistant', content }, finish_reason: 'stop' }], usage: { prompt_tokens: 10, completion_tokens: 1 } }));
    }
    const decided = await script.reply(req, ctx);
    entry.decided = decided.calls ? decided.calls.map((c) => c.name) : (decided.text || '').slice(0, 200);
    await stream(res, decided, req);
  } catch (error) {
    console.error('demo-model:', error.stack || error);
    if (!res.headersSent) { res.writeHead(500, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ error: { message: String(error) } })); }
    else res.end();
  }
});
server.listen(args.port || 0, '127.0.0.1', () => console.log(JSON.stringify({ port: server.address().port })));
